"""capture_windows per-kind migration and odds fixture resolution.

Run: python -m unittest discover -s tests
"""

import json
import logging
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hockey_edge.snapshot import job, storage
from hockey_edge.snapshot.odds.oddspapi import load_team_map, parse_fixture

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "oddspapi"
LOGGER = logging.getLogger("test")

OLD_SCHEMA = """
CREATE TABLE odds_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, league TEXT NOT NULL, book TEXT NOT NULL,
    market TEXT, fixture_ref TEXT NOT NULL, captured_at TEXT NOT NULL, home_odds REAL,
    draw_odds REAL, away_odds REAL, parsed INTEGER NOT NULL, raw_payload TEXT NOT NULL
);
CREATE TABLE capture_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league TEXT NOT NULL DEFAULT 'liiga',
    season INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    window TEXT NOT NULL CHECK (window IN ('opening', 'mid', 'closing')),
    due_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'satisfied', 'missed')),
    satisfied_at TEXT,
    missed_recorded_at TEXT,
    UNIQUE (league, season, game_id, window)
);
CREATE INDEX idx_capture_windows_due ON capture_windows (status, due_at);
"""


class TempDbTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "snapshots.db"

    def tearDown(self):
        self._tmp.cleanup()


class MigrationTest(TempDbTest):
    def _old_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(OLD_SCHEMA)
        conn.executemany(
            "INSERT INTO capture_windows (season, game_id, window, due_at, status, satisfied_at, missed_recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (2027, 1, "opening", "2026-09-01T15:30:00Z", "satisfied", "2026-09-01T15:31:00Z", None),
                (2027, 1, "closing", "2026-09-02T15:05:00Z", "missed", None, "2026-09-02T15:30:00Z"),
                (2027, 2, "mid", "2026-09-20T12:30:00Z", "pending", None, None),
            ],
        )
        conn.commit()
        conn.close()

    def test_migrates_old_schema(self):
        self._old_db()
        conn = storage.get_connection(self.db_path)
        rows = conn.execute(
            "SELECT id, game_id, kind, window, status, satisfied_at, missed_recorded_at "
            "FROM capture_windows ORDER BY kind, id"
        ).fetchall()
        self.assertEqual(rows, [
            (1, 1, "lineups", "opening", "satisfied", "2026-09-01T15:31:00Z", None),
            (2, 1, "lineups", "closing", "missed", None, "2026-09-02T15:30:00Z"),
            (3, 2, "lineups", "mid", "pending", None, None),
            (4, 2, "odds", "mid", "pending", None, None),
        ])
        odds_cols = {r[1] for r in conn.execute("PRAGMA table_info(odds_snapshots)")}
        self.assertLessEqual({"season", "game_id", "home_participant_id", "away_participant_id", "start_utc"}, odds_cols)
        self.assertTrue((self.db_path.parent / storage.MIGRATION_BACKUP_NAME).exists())
        conn.close()

        # Idempotent: a second connection changes nothing.
        conn = storage.get_connection(self.db_path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM capture_windows").fetchone()[0], 4)
        conn.close()

    def test_fresh_db_needs_no_migration(self):
        conn = storage.get_connection(self.db_path)
        self.assertIn("kind", {r[1] for r in conn.execute("PRAGMA table_info(capture_windows)")})
        self.assertFalse((self.db_path.parent / storage.MIGRATION_BACKUP_NAME).exists())
        conn.close()

    def test_due_windows_are_per_kind(self):
        conn = storage.get_connection(self.db_path)
        _discover(conn, 2027, 5, "2026-09-18T15:30:00Z", "a", "b")
        for kind in storage.CAPTURE_KINDS:
            storage.upsert_capture_window(conn, league="liiga", season=2027, game_id=5, kind=kind,
                                          window="mid", due_at="2026-09-18T12:30:00Z")
        now = "2026-09-18T13:00:00Z"
        odds = storage.get_due_windows(conn, kind="odds", now_iso=now)
        storage.mark_windows_satisfied(conn, [r["id"] for r in odds], now_iso=now)
        # The bug this schema change fixes: satisfying odds must not hide lineups.
        self.assertEqual(len(storage.get_due_windows(conn, kind="lineups", now_iso=now)), 1)
        self.assertEqual(len(storage.get_due_windows(conn, kind="odds", now_iso=now)), 0)
        conn.close()


def _discover(conn, season, game_id, start_utc, home_team_id, away_team_id):
    payload = {"homeTeam": {"teamId": home_team_id}, "awayTeam": {"teamId": away_team_id}}
    storage.insert_discovered_fixture(
        conn, league="liiga", season=season, game_id=game_id, serie="RUNKOSARJA",
        start_utc=start_utc, home_team="h", away_team="a", started=False, ended=False,
        captured_at="2026-09-17T12:00:00Z", source="test", raw_payload=json.dumps(payload, ensure_ascii=False),
    )


class ResolveTest(TempDbTest):
    def setUp(self):
        super().setUp()
        self.conn = storage.get_connection(self.db_path)
        self.team_map = load_team_map()
        board = json.loads((FIXTURES / "odds-by-tournaments_134_bet365_2026-09-17.json").read_text(encoding="utf-8"))
        self.fixtures = {f["fixtureId"]: f for f in board}
        # Real season-2027 game ids for the bet365 board (hockey.db, 2026-09-17).
        games = {
            "2701309": (3824, 3844, "2026-09-18T15:30:00Z"),
            "2701310": (5568, 3841, "2026-09-18T15:30:00Z"),
            "2701311": (3822, 3845, "2026-09-18T16:30:00Z"),
            "2701315": (3845, 3822, "2026-09-19T14:00:00Z"),  # back-to-back, home/away swapped
            "2701317": (3846, 552575, "2026-09-19T14:00:00Z"),
        }
        for game_id, (home, away, start) in games.items():
            _discover(self.conn, 2027, int(game_id), start, self.team_map[home], self.team_map[away])
        self.expected = {
            fid: next(int(g) for g, (h, a, s) in games.items()
                      if (h, a) == (f["participant1Id"], f["participant2Id"]) and s[:13] == f["startTime"][:13])
            for fid, f in self.fixtures.items()
        }

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def _snaps(self, fixture):
        return parse_fixture(fixture, book="bet365", captured_at=datetime(2026, 9, 17, tzinfo=timezone.utc))

    def test_every_bet365_fixture_resolves_to_its_game(self):
        for fid, fixture in self.fixtures.items():
            with self.subTest(fixture=fid):
                resolved = job.resolve_snapshots(self.conn, self._snaps(fixture), self.team_map, LOGGER)
                self.assertEqual({(s.season, s.game_id) for s in resolved}, {(2027, self.expected[fid])})
                self.assertEqual(resolved[0].start_utc[-1], "Z")

    def test_unknown_participant_stays_unresolved(self):
        fixture = dict(next(iter(self.fixtures.values())), participant1Id=3839)  # HIFK: not mapped yet
        resolved = job.resolve_snapshots(self.conn, self._snaps(fixture), self.team_map, LOGGER)
        self.assertTrue(all(s.game_id is None for s in resolved))

    def test_start_too_far_off_stays_unresolved(self):
        fixture = dict(next(iter(self.fixtures.values())), startTime="2026-09-25T15:30:00.000Z")
        resolved = job.resolve_snapshots(self.conn, self._snaps(fixture), self.team_map, LOGGER)
        self.assertTrue(all(s.game_id is None for s in resolved))

    def test_team_map_is_utf8_and_unique(self):
        self.assertEqual(self.team_map[3846], "679171680:ässät")
        self.assertEqual(len(set(self.team_map.values())), len(self.team_map))


if __name__ == "__main__":
    unittest.main()
