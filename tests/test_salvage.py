"""R5 grow-from-zero salvage, against the real season=2024 game_id=1 payloads.

Run: python -m unittest discover -s tests

fixtures/liiga/game_detail/2024_game1_original.json is the 2026-07-12 fetch
(47 roster rows, 9 penalties, 7 goalkeeper events);
2024_game1_refetch_goalkeeper_regression.json is the 2026-08-24 refetch of
the same game (47 / 9 / 1) -- the payload the uniform guard refuses.
"""

import json
import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hockey_edge.ingest import db
from hockey_edge.ingest.liiga import parsers, resync

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "liiga" / "game_detail"
ORIGINAL = FIXTURES / "2024_game1_original.json"
REGRESSION = FIXTURES / "2024_game1_refetch_goalkeeper_regression.json"
GAME_ID, SEASON = 1, 2024
TABLES = resync.GUARDED_TABLES["game_detail"]


def rows(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return conn.execute(f"SELECT * FROM {table} WHERE game_id = ? AND season = ? ORDER BY id", (GAME_ID, SEASON)).fetchall()


def parse(path: Path) -> dict:
    return parsers.parse_game_detail(json.loads(path.read_text(encoding="utf-8")), GAME_ID, SEASON)


class SalvageTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.conn = db.get_connection(self.tmp / "hockey.db")
        # Real games row for 2024:1 (copied from data/hockey.db).
        self.conn.execute(
            "INSERT INTO games (game_id, season, phase, start_utc, home_team_id, home_team_name, away_team_id, "
            "away_team_name, home_goals, away_goals, started, ended) VALUES "
            "(1, 2024, 'RUNKOSARJA', '2023-09-12T15:30:00Z', '624554857:lukko', 'Lukko', '55786244:hpk', 'HPK', 1, 2, 1, 1)"
        )
        parsers.upsert_game_detail(self.conn, GAME_ID, SEASON, parse(ORIGINAL))
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)
        self.conn.close()
        self._tmp.cleanup()

    def _upsert(self, path: Path):
        parsed = parse(path)
        return lambda: parsers.upsert_game_detail(self.conn, GAME_ID, SEASON, parsed)

    def _damage_penalties(self):
        # The bad-fetch-window state: rosters and goalkeeper events present, penalties zero.
        self.conn.execute("DELETE FROM game_penalty_events WHERE game_id = ? AND season = ?", (GAME_ID, SEASON))
        self.conn.commit()

    def test_uniform_guard_refuses_the_regression_payload(self):
        self._damage_penalties()
        applied = resync.reparse_with_shrink_guard(
            self.conn, game_id=GAME_ID, season=SEASON, endpoint_name="game_detail", do_upsert=self._upsert(REGRESSION),
        )
        self.assertFalse(applied)
        self.assertEqual(len(rows(self.conn, "game_penalty_events")), 0)  # the lost recovery R5 exists for

    def test_salvage_takes_penalties_and_keeps_everything_else_exactly(self):
        self._damage_penalties()
        kept = {t: rows(self.conn, t) for t in ("game_rosters", "game_goalkeeper_events")}
        accepted = resync.salvage_grow_from_zero(
            self.conn, game_id=GAME_ID, season=SEASON, endpoint_name="game_detail", do_upsert=self._upsert(REGRESSION),
        )
        self.assertEqual(accepted, {"game_penalty_events": (0, 9)})
        for table, before in kept.items():
            self.assertEqual(rows(self.conn, table), before, table)  # row-for-row, ids included
        self.assertEqual(len(rows(self.conn, "game_goalkeeper_events")), 7)  # not the regression's 1

    def test_salvage_accepts_nothing_when_no_table_was_empty(self):
        before = {t: rows(self.conn, t) for t in TABLES}
        accepted = resync.salvage_grow_from_zero(
            self.conn, game_id=GAME_ID, season=SEASON, endpoint_name="game_detail", do_upsert=self._upsert(REGRESSION),
        )
        self.assertEqual(accepted, {})
        self.assertEqual({t: rows(self.conn, t) for t in TABLES}, before)

    def test_salvage_game_detail_reads_saved_raw_and_checks_roster(self):
        self._damage_penalties()
        # Point raw_responses at the fixture, the way raw_cache records a fetch.
        self.conn.execute(
            "INSERT INTO raw_responses (endpoint, entity_id, season, url, fetched_at, http_status, content_hash, file_path) "
            "VALUES ('game_detail', '2024:1', 2024, 'x', '2026-08-24T00:00:00Z', 200, 'f02fa9e4', ?)",
            (str(REGRESSION),),
        )
        self.conn.commit()

        dry = resync.salvage_game_detail(self.conn, SEASON, GAME_ID, dry_run=True)
        self.assertEqual(dry["outcome"], "would_salvage")
        self.assertEqual(len(rows(self.conn, "game_penalty_events")), 0)  # dry run wrote nothing

        result = resync.salvage_game_detail(self.conn, SEASON, GAME_ID, dry_run=False)
        self.assertEqual(result["outcome"], "salvaged")
        self.assertEqual(result["missing_roster_players"], [])  # every real penalty taker was dressed

    def test_roster_check_reports_missing_player(self):
        penalty_player = self.conn.execute(
            "SELECT player_id FROM game_penalty_events WHERE game_id = ? AND season = ? AND player_id IS NOT NULL LIMIT 1",
            (GAME_ID, SEASON),
        ).fetchone()[0]
        self.conn.execute(
            "DELETE FROM game_rosters WHERE game_id = ? AND season = ? AND player_id = ?", (GAME_ID, SEASON, penalty_player),
        )
        missing = resync.penalty_players_missing_from_roster(self.conn, GAME_ID, SEASON)
        self.assertTrue(missing)
        self.assertEqual({m[2] for m in missing}, {penalty_player})


if __name__ == "__main__":
    unittest.main()
