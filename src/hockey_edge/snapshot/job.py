"""Snapshot capture job — Layer 2 per docs/DATA_PIPELINE.md.

Scheduler is dumb, job is smart: an external scheduler fires this every 15
minutes and the job itself decides whether anything is due. Registered as a
Windows scheduled task on the desktop since 2026-09-02, running 11:00–23:00
local (see docs/snapshot_job_task_scheduler.xml for the definition, the
registration command, and why the window starts at 11:00 rather than 14:00).
docs/snapshot_job_launchd.plist and docs/snapshot_job_cron.txt remain
scaffolding for other hosts, not deployed. Run manually or via the scheduler:

    python -m hockey_edge.snapshot.job --once       # normal pass
    python -m hockey_edge.snapshot.job --dry-run     # zero HTTP requests

Each --once pass: discover live fixtures (games_by_date -- see fixtures.py),
mark any windows that genuinely passed uncaptured as 'missed', work out which
(season, game_id, window) capture windows are due right now, and — if any are
due and the monthly OddsPapi ceiling isn't hit — poll odds once (one poll
covers the whole tournament board, so it can satisfy every currently-due
window in a single request; see docs/DATA_PIPELINE.md's OddsPapi section).
Lineup capture (see lineups.py) is wired: one game_detail(+game_preview)
fetch per due (season, game_id), regardless of window -- opening/mid windows
mostly return unassigned squads (line=null everywhere) and that's expected,
captured anyway as evidence of when lineups actually post; the closing
window (T-25min, closest to the T-30 test that unblocked this) is where a
real confirmed lineup shows up. capture_lineups re-queries due windows
itself rather than reusing the `due` list computed for odds above, so it
stays correct regardless of whether odds capture already marked some of
those windows satisfied this pass.

The odds provider is currently NullOddsProvider — makes zero HTTP requests,
always returns no snapshots. Wiring a real provider is a one-line change
below once one is chosen (see NullOddsProvider's docstring for why none is
wired yet: docs/SNAPSHOT_FINDINGS.md's OddsPapi recon).

Alerting: failures log at CRITICAL, to both console and logs/snapshot_job.log.
There's no email/Slack hookup yet (open item) — until one exists, a CRITICAL
line in the log is the signal to check in manually. A missed capture is gone
forever, so this job must never silently swallow a failure, and --once exits
non-zero on any failure so a scheduler can surface it.
"""

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from hockey_edge.snapshot import fixtures, lineups, storage
from hockey_edge.snapshot.odds.base import OddsProvider
from hockey_edge.snapshot.odds.null_provider import NullOddsProvider

LOG_DIR = Path(__file__).resolve().parents[3] / "logs"

# OddsPapi free tier is 250 req/mo; this leaves headroom for a bug not to
# blow the whole tier overnight. Currently moot with NullOddsProvider (zero
# real requests ever recorded) but enforced regardless so it's already
# correct once a real provider is wired.
ODDS_MONTHLY_CEILING = 200


def _configure_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("hockey_edge.snapshot")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        file_handler = logging.FileHandler(LOG_DIR / "snapshot_job.log")
        file_handler.setFormatter(fmt)
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
    return logger


def _now_iso() -> str:
    # Must match fixtures._iso_utc's format ('Z'-suffixed, no microseconds):
    # this value gets compared via plain SQL string ordering against
    # capture_windows.due_at and discovered_fixtures.start_utc (both in that
    # format) in storage.get_due_windows/mark_missed_windows — a mismatched
    # suffix (e.g. Python's default '+00:00' isoformat()) sorts differently
    # at exact-tie instants and would silently misjudge windows at the
    # boundary.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def capture_odds(
    provider: OddsProvider,
    conn: sqlite3.Connection,
    logger: logging.Logger,
    *,
    book: str = "pinnacle",
) -> list:
    """Call the provider once, record an api_usage row (skipped for a stub
    provider — see OddsProvider.is_stub), and write any snapshots. Returns
    the snapshots so the caller can decide which due windows they satisfy."""
    from hockey_edge.snapshot.odds.oddspapi import LIIGA_TOURNAMENT_ID

    requested_at = _now_iso()
    try:
        snapshots = provider.fetch_odds(tournament_ref=LIIGA_TOURNAMENT_ID, book=book)
    except Exception:
        logger.critical(
            "odds capture FAILED (provider=%s) — this poll's data is unrecoverable",
            provider.name,
            exc_info=True,
        )
        if not provider.is_stub:
            storage.insert_api_usage(
                conn, provider=provider.name, endpoint="odds-by-tournaments",
                requested_at=requested_at, http_status=None, outcome="failure",
            )
        return []

    if not provider.is_stub:
        storage.insert_api_usage(
            conn, provider=provider.name, endpoint="odds-by-tournaments",
            requested_at=requested_at, http_status=200,
            outcome="success_content" if snapshots else "success_empty",
        )

    for snapshot in snapshots:
        storage.insert_odds_snapshot(conn, snapshot)

    logger.info(
        "odds capture ok (provider=%s): %d snapshot(s) written",
        provider.name,
        len(snapshots),
    )
    return snapshots


def capture_lineups(conn: sqlite3.Connection, logger: logging.Logger) -> int:
    """One game_detail(+game_preview) fetch per distinct due (season,
    game_id), writing a LineupSnapshot per team-side and marking that game's
    due windows satisfied. A single game's fetch failure is logged at
    CRITICAL (missed capture is unrecoverable, same as odds) and does not
    stop the other due games this pass. Returns the number of windows
    marked satisfied."""
    now_iso = _now_iso()
    due = storage.get_due_windows(conn, now_iso=now_iso)
    if not due:
        logger.info("lineup capture: no windows currently due")
        return 0

    games: dict[tuple[int, int], list] = {}
    for row in due:
        games.setdefault((row["season"], row["game_id"]), []).append(row)

    satisfied = 0
    for (season, game_id), rows in games.items():
        window_label = ",".join(sorted({row["window"] for row in rows}))
        try:
            snapshots = lineups.fetch_lineups(season, game_id, window=window_label)
        except Exception:
            logger.critical(
                "lineup capture FAILED for season=%s game_id=%s (window=%s) — "
                "this poll's lineup data is unrecoverable",
                season, game_id, window_label, exc_info=True,
            )
            continue

        for snapshot in snapshots:
            storage.insert_lineup_snapshot(conn, snapshot)

        window_ids = [row["id"] for row in rows]
        storage.mark_windows_satisfied(conn, window_ids, now_iso=_now_iso())
        satisfied += len(window_ids)
        logger.info(
            "lineup capture ok: season=%s game_id=%s (window=%s) — %d snapshot(s) "
            "written, %d window(s) marked satisfied",
            season, game_id, window_label, len(snapshots), len(window_ids),
        )

    return satisfied


def run_dry_run(conn: sqlite3.Connection, logger: logging.Logger) -> None:
    """Zero HTTP requests, zero DB writes: report which windows are due (and
    which would be recorded missed) against whatever's already in
    snapshots.db from a prior --once pass. This is how the schedule gets
    verified without spending any budget."""
    now_iso = _now_iso()
    logger.info("dry-run: evaluating capture schedule as of %s (no HTTP, no writes)", now_iso)

    due = storage.get_due_windows(conn, now_iso=now_iso)
    if due:
        logger.info("dry-run: %d window(s) currently due:", len(due))
        for row in due:
            logger.info(
                "  would attempt: season=%s game_id=%s window=%s due_at=%s game_start=%s",
                row["season"], row["game_id"], row["window"], row["due_at"], row["game_start_utc"],
            )
    else:
        logger.info("dry-run: no windows currently due")

    conn.row_factory = sqlite3.Row
    would_miss = conn.execute(
        """
        SELECT cw.*, latest.start_utc AS game_start_utc
        FROM capture_windows cw
        JOIN (
            SELECT season, game_id, MAX(start_utc) AS start_utc
            FROM discovered_fixtures GROUP BY season, game_id
        ) latest ON latest.season = cw.season AND latest.game_id = cw.game_id
        WHERE cw.status = 'pending' AND cw.due_at <= ? AND latest.start_utc <= ?
        """,
        (now_iso, now_iso),
    ).fetchall()
    if would_miss:
        logger.info("dry-run: %d window(s) would be recorded MISSED on the next --once pass:", len(would_miss))
        for row in would_miss:
            logger.info(
                "  would miss: season=%s game_id=%s window=%s due_at=%s game_start=%s",
                row["season"], row["game_id"], row["window"], row["due_at"], row["game_start_utc"],
            )

    used = storage.count_api_usage_this_month(conn, provider="oddspapi", now_iso=now_iso)
    logger.info("dry-run: oddspapi usage this month: %d / %d ceiling", used, ODDS_MONTHLY_CEILING)


def run_once(conn: sqlite3.Connection, logger: logging.Logger) -> bool:
    """Normal pass. Returns True on success (no unhandled failure)."""
    now_iso = _now_iso()
    ok = True

    try:
        fixtures.discover_fixtures(conn)
    except Exception:
        logger.critical("fixture discovery FAILED", exc_info=True)
        ok = False

    now_iso = _now_iso()
    missed = storage.mark_missed_windows(conn, now_iso=now_iso)
    for row in missed:
        logger.warning(
            "capture window MISSED (genuinely passed uncaptured): "
            "season=%s game_id=%s window=%s due_at=%s game_start=%s",
            row["season"], row["game_id"], row["window"], row["due_at"], row["game_start_utc"],
        )

    due = storage.get_due_windows(conn, now_iso=now_iso)
    if due:
        used = storage.count_api_usage_this_month(conn, provider="oddspapi", now_iso=now_iso)
        if used >= ODDS_MONTHLY_CEILING:
            logger.critical(
                "oddspapi monthly ceiling reached (%d/%d) — skipping odds capture for "
                "%d due window(s) this pass", used, ODDS_MONTHLY_CEILING, len(due),
            )
        else:
            provider = NullOddsProvider()
            snapshots = capture_odds(provider, conn, logger)
            if snapshots:
                storage.mark_windows_satisfied(conn, [row["id"] for row in due], now_iso=now_iso)
                logger.info("marked %d window(s) satisfied by this poll", len(due))
            else:
                logger.info(
                    "%d window(s) due but no odds captured this poll (provider=%s) — "
                    "left pending", len(due), provider.name,
                )
    else:
        logger.info("no capture windows due this pass")

    try:
        capture_lineups(conn, logger)
    except Exception:
        logger.critical("lineup capture FAILED", exc_info=True)
        ok = False

    return ok


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Run a normal single pass.")
    mode.add_argument(
        "--dry-run", action="store_true",
        help="Zero HTTP requests, zero writes: report which windows are due.",
    )
    args = parser.parse_args()

    load_dotenv()
    logger = _configure_logging()
    conn = storage.get_connection()
    try:
        if args.dry_run:
            run_dry_run(conn, logger)
        else:
            ok = run_once(conn, logger)
            if not ok:
                sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
