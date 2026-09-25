"""scripts/repair_game_stats.py: undo the stripped game_stats reparses, keep
only puck-control periods that carry a value.

Run: python -m unittest discover -s tests

Fixtures are the two real game_stats responses for season 2024 game 2:
the 2026-07 original and the 2026-09-25 refetch, which has the same row
counts in every period table but stripped values (time on ice 0, faceoffs
null, power-play lists empty) and two more puck-control periods.
"""

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hockey_edge.ingest import db
from hockey_edge.ingest.liiga import parsers

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import repair_game_stats  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "liiga" / "game_stats"
ORIGINAL = json.loads((FIXTURES / "2024_game2_original.json").read_text(encoding="utf-8"))
STRIPPED = json.loads((FIXTURES / "2024_game2_refetch_stripped.json").read_text(encoding="utf-8"))
GAME_ID, SEASON = 2, 2024
KEY = (SEASON, GAME_ID)


def rows(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return conn.execute(f"SELECT * FROM {table} WHERE game_id = ? AND season = ? ORDER BY id", (GAME_ID, SEASON)).fetchall()


def build(path: Path) -> None:
    conn = db.get_connection(path)
    # Real games row for 2024:2 (copied from data/hockey.db).
    conn.execute(
        "INSERT INTO games (game_id, season, phase, start_utc, home_team_id, home_team_name, away_team_id, "
        "away_team_name, home_goals, away_goals, started, ended) VALUES "
        "(2, 2024, 'RUNKOSARJA', '2023-09-13T15:30:00Z', '292293444:jukurit', 'Jukurit', '461765763:kookoo', 'KooKoo', 1, 4, 1, 1)"
    )
    parsers.upsert_game_stats(conn, GAME_ID, SEASON, parsers.parse_game_stats(ORIGINAL, GAME_ID, SEASON))
    conn.commit()
    conn.close()


class RepairTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.backup, self.main = tmp / "backup.db", tmp / "hockey.db"
        build(self.backup)
        shutil.copy(self.backup, self.main)

    def tearDown(self):
        self._tmp.cleanup()

    def _simulate_run(self):
        conn = sqlite3.connect(self.main)
        parsers.upsert_game_stats(conn, GAME_ID, SEASON, parsers.parse_game_stats(STRIPPED, GAME_ID, SEASON))
        conn.commit()
        conn.close()

    def _repair(self, outcome: str, apply: bool = True) -> dict:
        conn = repair_game_stats.open_db(self.main)
        try:
            with mock.patch.object(repair_game_stats, "load_latest_raw", return_value=(STRIPPED, None)):
                return repair_game_stats.repair(conn, self.backup, {KEY: outcome}, apply)
        finally:
            conn.close()

    def _tables(self, path: Path) -> dict[str, list[tuple]]:
        conn = sqlite3.connect(path)
        try:
            return {t: rows(conn, t) for t in repair_game_stats.TABLES}
        finally:
            conn.close()

    def test_fixture_is_the_guards_blind_spot(self):
        before = parsers.parse_game_stats(ORIGINAL, GAME_ID, SEASON)
        after = parsers.parse_game_stats(STRIPPED, GAME_ID, SEASON)
        for k in ("team", "player", "goalie"):
            self.assertEqual(len(before[k]), len(after[k]))
            self.assertNotEqual(sorted(before[k], key=repr), sorted(after[k], key=repr))
        self.assertEqual((len(before["puck"]), len(after["puck"])), (1, 3))

    def test_reparsed_game_is_restored_and_gains_puck_periods(self):
        original = self._tables(self.backup)
        self._simulate_run()
        summary = self._repair("reparsed")
        self.assertEqual(summary["problems"], [])
        self.assertTrue(summary["applied"])
        self.assertEqual(summary["per_game"][KEY], ("added", 2))

        repaired = self._tables(self.main)
        for t in repair_game_stats.PERIOD_TABLES:
            self.assertEqual(repaired[t], original[t])  # ids included
        puck = [r[1:] for r in repaired["game_puck_control"]]
        self.assertEqual(puck[0], original["game_puck_control"][0][1:])  # stored row kept as is
        self.assertEqual([r[2] for r in puck], [2, 1, 3])
        self.assertTrue(all(r[3] is not None for r in puck))

    def test_refused_game_keeps_its_stats_and_gains_puck_periods(self):
        original = self._tables(self.backup)
        summary = self._repair("shrink_guarded")
        self.assertEqual(summary["per_game"][KEY], ("added", 2))
        repaired = self._tables(self.main)
        for t in repair_game_stats.PERIOD_TABLES:
            self.assertEqual(repaired[t], original[t])
        self.assertEqual(len(repaired["game_puck_control"]), 3)

    def test_conflicting_stored_row_blocks_additions(self):
        for path in (self.backup, self.main):
            conn = sqlite3.connect(path)
            conn.execute("UPDATE game_puck_control SET home_control_seconds = 1.0 WHERE game_id = 2 AND season = 2024")
            conn.commit()
            conn.close()
        summary = self._repair("shrink_guarded")
        self.assertEqual(summary["per_game"][KEY], ("conflict", 0))
        self.assertEqual(len(self._tables(self.main)["game_puck_control"]), 1)

    def test_dry_run_writes_nothing(self):
        self._simulate_run()
        damaged = self._tables(self.main)
        summary = self._repair("reparsed", apply=False)
        self.assertEqual(summary["problems"], [])
        self.assertFalse(summary["applied"])
        self.assertEqual(self._tables(self.main), damaged)

    def test_all_null_placeholder_rows_are_not_added(self):
        # Season 2021 game 1, as liiga.fi returns it: every duration null,
        # before (period 2 only) and after the refetch (periods 1-3).
        stored = {(1, 2021, 2, None, None, None)}
        parsed = [(1, 2021, p, None, None, None) for p in (1, 2, 3)]
        self.assertEqual(repair_game_stats.plan_puck_additions(stored, parsed), ("nothing_to_add", []))


if __name__ == "__main__":
    unittest.main()
