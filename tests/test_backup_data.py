"""scripts/backup_data.py: online SQLite copy, zip contents, verification,
retention.

Run: python -m unittest discover -s tests

Every test builds its own data/ and destination folders in a temp directory,
with the real schemas from ingest/db.py and snapshot/storage.py, a real games
row (season 2024 game 2, copied from data/hockey.db) and real saved liiga.fi
responses as raw files. Nothing touches data/ or the Drive folder, and there
is no HTTP.
"""

import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest import mock

from hockey_edge.ingest import db
from hockey_edge.snapshot import storage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backup_data  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "liiga"
GAME_ROW = (
    "INSERT INTO games (game_id, season, phase, start_utc, home_team_id, home_team_name, away_team_id, "
    "away_team_name, home_goals, away_goals, started, ended) VALUES "
    "(2, 2024, 'RUNKOSARJA', '2023-09-13T15:30:00Z', '292293444:jukurit', 'Jukurit', '461765763:kookoo', "
    "'KooKoo', 1, 4, 1, 1)"
)
TODAY = date(2026, 10, 18)


def build_data_dir(data: Path) -> None:
    conn = db.get_connection(data / "hockey.db")
    conn.execute(GAME_ROW)
    conn.commit()
    conn.close()
    storage.get_connection(data / "snapshots.db").close()

    for rel in ("game_stats/2024_game2_original.json", "game_stats/2024_game2_refetch_stripped.json"):
        target = data / "raw" / "liiga" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / rel, target)
    (data / "recovery").mkdir()
    shutil.copy(FIXTURES / "game_stats" / "2024.json", data / "recovery" / "2024.json")

    # Present in the real data/ and must stay out of the zip.
    (data / "backups").mkdir()
    shutil.copy(data / "hockey.db", data / "backups" / "hockey_pre_recovery.db")
    for name in ("features.db", "backtest.db", "snapshots.pre-kind-migration.db"):
        shutil.copy(data / "hockey.db", data / name)


class BackupTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.data = root / "data"
        self.dest = root / "drive"
        self.staging = root / "staging"
        for d in (self.data, self.dest, self.staging):
            d.mkdir()
        build_data_dir(self.data)

    def tearDown(self):
        self._tmp.cleanup()

    def run_backup(self, **kw):
        kw.setdefault("today", TODAY)
        return backup_data.run_backup(self.data, self.dest, tmp_parent=self.staging, **kw)


class OnlineCopyTest(BackupTestCase):
    def test_copy_while_another_connection_holds_a_write_transaction(self):
        writer = sqlite3.connect(self.data / "hockey.db", isolation_level=None)
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE games SET home_goals = 9 WHERE game_id = 2 AND season = 2024")
            copy = self.staging / "copy.db"
            count = backup_data.backup_database(self.data / "hockey.db", copy, "games")
        finally:
            writer.execute("ROLLBACK")
            writer.close()

        self.assertEqual(count, 1)
        conn = sqlite3.connect(copy)
        try:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            # The copy holds the committed state, not the open transaction's.
            self.assertEqual(conn.execute("SELECT home_goals FROM games").fetchone()[0], 1)
        finally:
            conn.close()


class ZipContentsTest(BackupTestCase):
    def test_zip_holds_the_plan_and_nothing_else(self):
        result = self.run_backup()
        final = self.dest / "hockey-edge-data-2026-10-18.zip"
        self.assertEqual(result["path"], final)
        with zipfile.ZipFile(final) as zf:
            names = set(zf.namelist())
        self.assertEqual(names, {
            "data/hockey.db",
            "data/snapshots.db",
            "data/raw/liiga/game_stats/2024_game2_original.json",
            "data/raw/liiga/game_stats/2024_game2_refetch_stripped.json",
            "data/recovery/2024.json",
        })
        for excluded in ("backups/", "features.db", "backtest.db", "pre-kind-migration"):
            self.assertFalse(any(excluded in n for n in names), excluded)
        # Only the finished zip is left in the destination, no .partial.
        self.assertEqual([p.name for p in self.dest.iterdir()], [final.name])

    def test_restored_database_matches_the_source(self):
        self.run_backup()
        out = self.staging / "restore"
        with zipfile.ZipFile(self.dest / "hockey-edge-data-2026-10-18.zip") as zf:
            zf.extractall(out)
        restored = sqlite3.connect(out / "data" / "hockey.db")
        source = sqlite3.connect(self.data / "hockey.db")
        try:
            q = "SELECT * FROM games ORDER BY season, game_id"
            self.assertEqual(restored.execute(q).fetchall(), source.execute(q).fetchall())
        finally:
            restored.close()
            source.close()
        self.assertEqual(
            (out / "data/raw/liiga/game_stats/2024_game2_original.json").read_bytes(),
            (FIXTURES / "game_stats/2024_game2_original.json").read_bytes(),
        )

    def test_missing_recovery_folder_is_fine(self):
        shutil.rmtree(self.data / "recovery")
        self.run_backup()
        with zipfile.ZipFile(self.dest / "hockey-edge-data-2026-10-18.zip") as zf:
            self.assertFalse(any(n.startswith("data/recovery/") for n in zf.namelist()))

    def test_dry_run_writes_nothing(self):
        result = self.run_backup(dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(list(self.dest.iterdir()), [])
        self.assertEqual(list(self.staging.iterdir()), [])


class RetentionTest(BackupTestCase):
    def test_keeps_four_newest_and_never_touches_other_files(self):
        dated = [f"hockey-edge-data-2026-09-{d:02d}.zip" for d in (6, 13, 20, 27)] + [
            "hockey-edge-data-2026-10-04.zip", "hockey-edge-data-2026-10-11.zip",
        ]
        others = [
            "notes.txt",
            "hockey-edge-data-latest.zip",
            "hockey-edge-data-2026-08-01.zip.bak",
            "old-hockey-edge-data-2026-08-02.zip",
            ".hockey-edge-data-2026-08-03.zip.partial",
            "desktop.ini",
        ]
        for name in dated + others:
            (self.dest / name).write_bytes(b"x")
        (self.dest / "hockey-edge-data-2026-08-04.zip").mkdir()  # a folder, not a file

        deleted = backup_data.apply_retention(self.dest, keep=4)

        self.assertEqual(deleted, dated[:2])
        remaining = {p.name for p in self.dest.iterdir()}
        self.assertEqual(remaining, set(dated[2:]) | set(others) | {"hockey-edge-data-2026-08-04.zip"})

    def test_new_backup_counts_toward_retention(self):
        for d in (20, 27):
            (self.dest / f"hockey-edge-data-2026-09-{d}.zip").write_bytes(b"x")
        (self.dest / "hockey-edge-data-2026-10-11.zip").write_bytes(b"x")
        result = self.run_backup(keep=2)
        self.assertEqual(result["deleted"], ["hockey-edge-data-2026-09-20.zip", "hockey-edge-data-2026-09-27.zip"])
        self.assertEqual(
            sorted(p.name for p in self.dest.iterdir()),
            ["hockey-edge-data-2026-10-11.zip", "hockey-edge-data-2026-10-18.zip"],
        )

    def test_keep_below_one_is_refused(self):
        with self.assertRaises(backup_data.BackupError):
            backup_data.apply_retention(self.dest, keep=0)


class FailureTest(BackupTestCase):
    def test_missing_destination_fails_loudly(self):
        missing = self.dest / "not-here"
        with self.assertRaisesRegex(backup_data.BackupError, "does not exist"):
            backup_data.run_backup(self.data, missing, today=TODAY, tmp_parent=self.staging)
        self.assertFalse(missing.exists())

    def test_missing_destination_exits_non_zero(self):
        missing = self.dest / "not-here"
        with mock.patch.object(sys, "argv", ["backup_data.py", "--dest", str(missing)]), \
                mock.patch.object(backup_data, "DATA_DIR", self.data):
            with self.assertRaises(SystemExit) as cm:
                backup_data._main()
        self.assertEqual(cm.exception.code, 1)

    def test_corrupted_zip_is_never_moved_into_the_destination(self):
        real_build = backup_data.build_zip

        def build_then_corrupt(members, zip_path):
            real_build(members, zip_path)
            with zipfile.ZipFile(zip_path) as zf:
                info = zf.getinfo("data/hockey.db")
            # Flip bytes inside hockey.db's compressed data, past its local header.
            offset = info.header_offset + 30 + len(info.filename) + len(info.extra) + 100
            with open(zip_path, "r+b") as f:
                f.seek(offset)
                chunk = f.read(64)
                f.seek(offset)
                f.write(bytes(b ^ 0xFF for b in chunk))

        with mock.patch.object(backup_data, "build_zip", build_then_corrupt):
            with self.assertRaises(backup_data.BackupError):
                self.run_backup()
        self.assertEqual(list(self.dest.iterdir()), [])
        self.assertEqual(list(self.staging.iterdir()), [])  # temp folder cleaned up

    def test_member_list_mismatch_fails_verification(self):
        real_build = backup_data.build_zip

        def build_without_last(members, zip_path):
            real_build(members[:-1], zip_path)

        with mock.patch.object(backup_data, "build_zip", build_without_last):
            with self.assertRaisesRegex(backup_data.BackupError, "member list"):
                self.run_backup()
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_missing_database_fails(self):
        (self.data / "snapshots.db").unlink()
        with self.assertRaisesRegex(backup_data.BackupError, "database missing"):
            self.run_backup()


if __name__ == "__main__":
    unittest.main()
