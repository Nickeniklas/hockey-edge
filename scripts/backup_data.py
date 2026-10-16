"""Weekly off-machine backup of data/ into the Google Drive folder.

The local data/ is the best copy of this project's data anywhere: for
2015-2024 period stats it is the only good copy (liiga.fi now serves those
stripped, see docs/RECOVERY_BACKLOG.md), and missed snapshot windows can never
be refetched. This writes one verified zip of it into BACKUP_DIR, a folder
that Google Drive for desktop syncs. Plan: docs/PIPELINE_SAFETY_PLAN.md,
phase S1. Restore steps: docs/BACKUP.md.

What goes in the zip (arcnames are repo-relative, so it unzips at the root):

    data/hockey.db       via sqlite3 Connection.backup(), never a file copy
    data/snapshots.db    the same
    data/raw/            every file
    data/recovery/       every file, if the folder exists

What stays out: data/backups/ (1.2 GB of old pre-recovery copies, decided
from the 2026-10-14 size report), features.db and backtest.db (rebuilt in
seconds), snapshots.pre-kind-migration.db, and anything else in data/.

The steps, in order:

1. Each database is copied with Connection.backup() into a local temp
   folder. That is safe while the snapshot job or the nightly sync is
   writing, so the backup can run at any time. The copy is checked with
   PRAGMA integrity_check right away.
2. The zip is built in the same temp folder.
3. It is verified there: testzip(), the member list against the plan, then
   each database extracted again and checked with integrity_check and a row
   count (games, odds_snapshots) equal to the copy's. The copy's count must
   also not exceed the live database's count read afterwards; both tables
   only ever grow.
4. Only a verified zip reaches BACKUP_DIR. The temp folder is on C: and the
   Drive folder may be on another drive, where a move is really a copy, so
   the zip is copied in under a hidden ".partial" name and then renamed. The
   final name appears in one atomic step and is never a half-written file.
5. Retention keeps the --keep newest files whose name fully matches
   hockey-edge-data-YYYY-MM-DD.zip (newest by the date in the name). Nothing
   else in the folder is ever touched, including a .partial left by a run
   that crashed: that one is logged, not deleted.

The script cannot see whether Drive has finished uploading. Leave the PC on
for a while after a run.

Usage (from repo root, venv active):

    python scripts/backup_data.py
    python scripts/backup_data.py --dry-run
    python scripts/backup_data.py --dest D:\\somewhere\\else --keep 3

Logs to logs/backup.log, plus stdout from a terminal. The weekly task runs
pythonw.exe, where sys.stdout/sys.stderr are None and the log file is the
only record. Exits non-zero on any failure.

Heartbeat (docs/PIPELINE_SAFETY_PLAN.md phase S2): a real run pings the
healthchecks.io `backup` check with start, then success, or fail with the
error. --dry-run sends nothing.
"""

import argparse
import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from hockey_edge.ops import heartbeat as ops_heartbeat

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
LOG_DIR = REPO_ROOT / "logs"

# Database file name -> the table whose row count is checked after restore.
DATABASES = {"hockey.db": "games", "snapshots.db": "odds_snapshots"}
# Folders under data/ copied whole. "raw" must exist; "recovery" may not.
REQUIRED_DIRS = ("raw",)
OPTIONAL_DIRS = ("recovery",)
# Reported in the size report only, never zipped.
EXCLUDED_DIRS = ("backups",)

DEFAULT_KEEP = 4
ZIP_PREFIX = "hockey-edge-data-"
ZIP_NAME_RE = re.compile(r"hockey-edge-data-\d{4}-\d{2}-\d{2}\.zip")
PARTIAL_NAME_RE = re.compile(r"\.hockey-edge-data-\d{4}-\d{2}-\d{2}\.zip\.partial")

logger = logging.getLogger("hockey_edge.backup")


class BackupError(Exception):
    """A backup step failed. Nothing unverified was put in the destination."""


def heartbeat(status: str, body: str = "") -> None:
    """Ping the healthchecks.io `backup` check: "start", "success" or "fail".

    HC_BACKUP from .env; unset is a no-op. Never raises (see
    hockey_edge.ops.heartbeat).
    """
    ops_heartbeat.ping("backup", status, body)


def zip_name(day: date) -> str:
    return f"{ZIP_PREFIX}{day.isoformat()}.zip"


def _tree_size(path: Path) -> tuple[int, int]:
    total = count = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            total += os.path.getsize(os.path.join(root, name))
            count += 1
    return total, count


def size_report(data_dir: Path) -> dict[str, int | None]:
    """Bytes per backup part, None for a part that does not exist."""
    sizes: dict[str, int | None] = {}
    for name in DATABASES:
        path = data_dir / name
        sizes[name] = path.stat().st_size if path.is_file() else None
    for name in REQUIRED_DIRS + OPTIONAL_DIRS + EXCLUDED_DIRS:
        path = data_dir / name
        sizes[name + "/"] = _tree_size(path)[0] if path.is_dir() else None
    return sizes


def _mb(n: int | None) -> str:
    return "missing" if n is None else f"{n / 2**20:,.1f} MB"


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _integrity(conn: sqlite3.Connection) -> str:
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    return "; ".join(str(r[0]) for r in rows)


def _open_ro(path: Path) -> sqlite3.Connection:
    # Read-only URI: a missing file raises instead of being created empty.
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)


def backup_database(src: Path, dest: Path, table: str) -> int:
    """Copy src to dest with the online backup API. Returns dest's row count of table.

    Safe while another connection is writing: the copy is a consistent
    snapshot of the last committed state, and CPython retries while the
    source is locked.
    """
    source = _open_ro(src)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
            result = _integrity(target)
            if result != "ok":
                raise BackupError(f"{src.name}: integrity_check on the copy returned {result!r}")
            return _count(target, table)
        finally:
            target.close()
    finally:
        source.close()


def _files_under(data_dir: Path, name: str) -> list[tuple[Path, str]]:
    """(path, arcname) for every file under data_dir/name, sorted."""
    out = []
    base = data_dir / name
    for root, dirs, files in os.walk(base):
        dirs.sort()
        for f in sorted(files):
            path = Path(root) / f
            out.append((path, "data/" + path.relative_to(data_dir).as_posix()))
    return out


def plan_members(data_dir: Path, db_copies: dict[str, Path]) -> list[tuple[Path, str]]:
    """Every (source path, arcname) the zip will hold, databases first."""
    members = [(db_copies[name], f"data/{name}") for name in DATABASES]
    for name in REQUIRED_DIRS:
        if not (data_dir / name).is_dir():
            raise BackupError(f"required folder missing: {data_dir / name}")
        members += _files_under(data_dir, name)
    for name in OPTIONAL_DIRS:
        if (data_dir / name).is_dir():
            members += _files_under(data_dir, name)
    return members


def build_zip(members: list[tuple[Path, str]], zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path, arcname in members:
            zf.write(path, arcname)


def verify_zip(zip_path: Path, expected_names: set[str], expected_counts: dict[str, int],
               work_dir: Path) -> list[str]:
    """Check the zip before it goes anywhere. Returns report lines; raises BackupError."""
    lines = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise BackupError(f"testzip: bad CRC in {bad}")
            lines.append(f"testzip: ok ({len(zf.namelist())} members)")

            names = set(zf.namelist())
            if names != expected_names:
                missing = sorted(expected_names - names)[:5]
                extra = sorted(names - expected_names)[:5]
                raise BackupError(f"member list differs from plan: missing {missing}, extra {extra}")
            lines.append("member list: matches plan")

            for name, table in DATABASES.items():
                extracted = Path(zf.extract(f"data/{name}", work_dir))
                conn = sqlite3.connect(extracted)
                try:
                    result = _integrity(conn)
                    count = _count(conn, table)
                finally:
                    conn.close()
                if result != "ok":
                    raise BackupError(f"{name}: integrity_check after extract returned {result!r}")
                if count != expected_counts[name]:
                    raise BackupError(
                        f"{name}: {table} has {count} rows after extract, the copy had "
                        f"{expected_counts[name]}"
                    )
                lines.append(f"{name}: integrity_check ok, {table} {count:,} rows")
                extracted.unlink()
    except BackupError:
        raise
    except Exception as exc:
        # A damaged deflate stream raises zlib.error from testzip, not
        # BadZipFile; treat anything unexpected as a failed verification.
        raise BackupError(f"zip verification failed: {type(exc).__name__}: {exc}") from exc
    return lines


def publish(zip_path: Path, dest: Path) -> Path:
    """Copy a verified zip into dest; the final name appears by one atomic rename."""
    final = dest / zip_path.name
    partial = dest / f".{zip_path.name}.partial"
    with open(zip_path, "rb") as src, open(partial, "wb") as out:
        shutil.copyfileobj(src, out, 1024 * 1024)
        out.flush()
        os.fsync(out.fileno())
    if partial.stat().st_size != zip_path.stat().st_size:
        partial.unlink()
        raise BackupError("copy into the destination came out a different size")
    if final.exists():
        logger.info("replacing today's earlier backup %s", final.name)
    os.replace(partial, final)
    return final


def apply_retention(dest: Path, keep: int) -> list[str]:
    """Delete all but the `keep` newest pattern-matching zips. Returns deleted names."""
    if keep < 1:
        raise BackupError(f"--keep must be at least 1, got {keep}")
    entries = list(dest.iterdir())
    matching = sorted(p.name for p in entries if p.is_file() and ZIP_NAME_RE.fullmatch(p.name))
    for p in entries:
        if PARTIAL_NAME_RE.fullmatch(p.name):
            logger.warning("left-over partial copy from an earlier run, not touched: %s", p.name)
    doomed = matching[:-keep]
    for name in doomed:
        (dest / name).unlink()
    return doomed


def run_backup(data_dir: Path, dest: Path, keep: int = DEFAULT_KEEP, dry_run: bool = False,
               today: date | None = None, tmp_parent: Path | None = None) -> dict:
    """One full backup. Raises BackupError (or OSError) on any failure."""
    started = time.monotonic()
    if not dest.is_dir():
        raise BackupError(f"backup destination does not exist or is not a folder: {dest}")
    if keep < 1:
        raise BackupError(f"--keep must be at least 1, got {keep}")
    for name in DATABASES:
        if not (data_dir / name).is_file():
            raise BackupError(f"database missing: {data_dir / name}")

    sizes = size_report(data_dir)
    for part, n in sizes.items():
        note = "  (excluded)" if part.rstrip("/") in EXCLUDED_DIRS else ""
        logger.info("size %-14s %s%s", part, _mb(n), note)

    name = zip_name(today or date.today())
    logger.info("plan: %s into %s, keep %d", name, dest, keep)
    if dry_run:
        logger.info("dry-run: nothing copied, zipped or deleted")
        return {"dry_run": True, "sizes": sizes, "zip_name": name}

    with tempfile.TemporaryDirectory(prefix="hockey-edge-backup-", dir=tmp_parent) as tmp:
        tmp_dir = Path(tmp)
        copies_dir = tmp_dir / "copies"
        copies_dir.mkdir()

        copies, counts = {}, {}
        for db_name, table in DATABASES.items():
            copies[db_name] = copies_dir / db_name
            counts[db_name] = backup_database(data_dir / db_name, copies[db_name], table)
            logger.info("copied %s: integrity_check ok, %s %s rows", db_name, table, f"{counts[db_name]:,}")

        # Both tables only grow, so a copy holding more rows than the live
        # database now holds means the copy is not of this database.
        for db_name, table in DATABASES.items():
            conn = _open_ro(data_dir / db_name)
            try:
                live = _count(conn, table)
            finally:
                conn.close()
            if counts[db_name] > live:
                raise BackupError(f"{db_name}: copy has {counts[db_name]} {table} rows, live has {live}")
            logger.info("source %s: %s %s rows now (copy %s)", db_name, table, f"{live:,}", f"{counts[db_name]:,}")

        members = plan_members(data_dir, copies)
        zip_path = tmp_dir / name
        build_zip(members, zip_path)
        zip_size = zip_path.stat().st_size
        logger.info("built %s: %d members, %s", name, len(members), _mb(zip_size))

        verify_dir = tmp_dir / "verify"
        verify_dir.mkdir()
        for line in verify_zip(zip_path, {arc for _p, arc in members}, counts, verify_dir):
            logger.info("verify %s", line)

        final = publish(zip_path, dest)
        logger.info("published %s", final)

    deleted = apply_retention(dest, keep)
    for d in deleted:
        logger.info("retention: deleted %s", d)
    kept = sorted(p.name for p in dest.iterdir() if p.is_file() and ZIP_NAME_RE.fullmatch(p.name))
    logger.info("retention: kept %s", ", ".join(kept))
    logger.info(
        "drive quota estimate: about %s (2 x keep %d x zip, Drive trash keeps deleted files 30 days)",
        _mb(2 * keep * zip_size), keep,
    )

    elapsed = time.monotonic() - started
    logger.info("backup finished ok in %.0f s", elapsed)
    return {"dry_run": False, "sizes": sizes, "zip_name": name, "zip_size": zip_size,
            "path": final, "counts": counts, "deleted": deleted, "seconds": elapsed}


def _configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        file_handler = logging.FileHandler(LOG_DIR / "backup.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
        # Under pythonw sys.stdout is None; a StreamHandler on it would fail
        # once per record.
        if sys.stdout is not None:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(fmt)
            logger.addHandler(console_handler)


def main() -> None:
    _configure_logging()
    try:
        _main()
    except SystemExit:
        raise
    except BaseException:
        logger.critical("backup aborted with an unhandled exception", exc_info=True)
        raise


def _main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dest", type=Path, help="backup folder (default: BACKUP_DIR from .env)")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                        help=f"how many backup zips to keep (default {DEFAULT_KEEP})")
    parser.add_argument("--dry-run", action="store_true", help="sizes and plan only, writes nothing")
    args = parser.parse_args()

    # Explicit path: the scheduled task's working directory should be the
    # repo root, but this must not depend on it.
    load_dotenv(REPO_ROOT / ".env")
    dest = args.dest or (Path(os.environ["BACKUP_DIR"]) if os.environ.get("BACKUP_DIR") else None)

    if not args.dry_run:
        heartbeat("start")
    try:
        if dest is None:
            raise BackupError("no destination: set BACKUP_DIR in .env or pass --dest")
        run_backup(DATA_DIR, dest, keep=args.keep, dry_run=args.dry_run)
    except Exception as exc:
        logger.critical("backup FAILED: %s", exc, exc_info=not isinstance(exc, BackupError))
        if not args.dry_run:
            heartbeat("fail", f"{type(exc).__name__}: {exc}")
        sys.exit(1)
    if not args.dry_run:
        heartbeat("success")


if __name__ == "__main__":
    main()
