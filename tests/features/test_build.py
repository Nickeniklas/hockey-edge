"""Full rebuild: idempotent, read-only on its sources, checked season format.

Run: python -m unittest discover -s tests
"""

import json
import sqlite3
import unittest

from hockey_edge.features import FEATURE_VERSION
from hockey_edge.features import build as feature_build
from hockey_edge.features import db as feature_db
from hockey_edge.features.season_format import SeasonFormatError

from .support import FeatureTest, rows


class BuildTest(FeatureTest):
    def test_rebuild_is_idempotent(self):
        path = self.dir / "again.features.db"
        for _ in range(2):  # the second build replaces the first
            feature_build.build(path, self.hockey, self.snapshots)
        again = feature_db.connect(path, self.hockey, self.snapshots)
        self.addCleanup(again.close)
        for table in feature_db.TABLES:
            self.assertEqual(rows(again, table), rows(self.full, table), table)
        self.assertEqual(again.execute("SELECT COUNT(*) FROM build_meta").fetchone()[0], 1)

    def test_build_meta(self):
        meta = self.full.execute("SELECT * FROM build_meta").fetchone()
        self.assertEqual(meta["code_version"], FEATURE_VERSION)
        self.assertEqual(meta["snapshots_max_captured_at"], "2026-10-07T15:30:38.891377+00:00")
        counts = json.loads(meta["row_counts"])
        self.assertEqual(set(counts), set(feature_db.TABLES))
        self.assertEqual(counts["game_spine"], 1778)
        self.assertEqual(counts["features_home"], counts["features_schedule"])

    def test_sources_are_read_only(self):
        for statement in ("DELETE FROM hockey.games", "DELETE FROM snapshots.odds_snapshots"):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.OperationalError):
                self.full.execute(statement)

    def test_season_format(self):
        formats = {r["season"]: tuple(r)[1:] for r in self.full.execute("SELECT * FROM season_format")}
        self.assertEqual(formats[2022], (15, 60, 1, "none", 448, 448, 10))
        self.assertEqual(formats[2025], (16, 60, 1, "playout_qualification", 480, 480, 12))
        self.assertEqual(formats[2027][:4], (17, 64, 1, "direct_bottom_three"))
        self.assertEqual(formats[2027][4], 544)

    def test_games_per_team_are_counted(self):
        spread = {
            r[0]: (r[1], r[2]) for r in self.full.execute(
                "SELECT season, MIN(games_scheduled), MAX(games_scheduled) FROM team_season GROUP BY season"
            )
        }
        self.assertEqual(spread, {2022: (58, 60), 2025: (60, 60), 2027: (64, 64)})

    def test_first_liiga_seasons(self):
        # 2022 is the first season in the fixture, so its teams are not
        # first-season cases; K-Espoo and Jokerit joined later.
        first = self.full.execute(
            "SELECT season, team_name FROM team_season WHERE is_first_liiga_season = 1 ORDER BY season"
        ).fetchall()
        self.assertEqual([tuple(r) for r in first], [(2025, "K-Espoo"), (2027, "Jokerit")])

    def test_failed_build_keeps_the_previous_file(self):
        path = self.dir / "keep.features.db"
        feature_build.build(path, self.hockey, self.snapshots)
        # A season with no curated format must stop the build.
        hockey = self.edited_hockey("season2030", ("UPDATE games SET season = 2030 WHERE season = 2027", ()))
        with self.assertRaises(SeasonFormatError):
            feature_build.build(path, hockey, self.snapshots)
        self.assertFalse(path.with_name(path.name + ".building").exists())
        conn = sqlite3.connect(path)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM game_spine").fetchone()[0], 1778)

    def test_wrong_team_count_stops_the_build(self):
        # Dropping one team's regular season leaves 2025 a team short.
        hockey = self.edited_hockey("short", (
            "DELETE FROM games WHERE season = 2025 AND phase = 'RUNKOSARJA' "
            "AND '1368624751:k-espoo' IN (home_team_id, away_team_id)", (),
        ))
        with self.assertRaises(SeasonFormatError):
            feature_build.build(self.dir / "short.features.db", hockey, self.snapshots)


if __name__ == "__main__":
    unittest.main()
