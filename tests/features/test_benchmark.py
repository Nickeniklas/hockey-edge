"""benchmark_odds: vig removal and "closing = last parsed row before start".

Run: python -m unittest discover -s tests
"""

import shutil
import sqlite3
import unittest

from hockey_edge.features.benchmark import remove_vig

from .support import FeatureTest

COLUMNS = "book, is_fallback, odds_snapshot_id, home_odds, draw_odds, away_odds, vig_method"


class BenchmarkTest(FeatureTest):
    def one(self, conn, game_id, columns=COLUMNS):
        return tuple(conn.execute(
            f"SELECT {columns} FROM benchmark_odds WHERE season = 2027 AND game_id = ?", (game_id,)
        ).fetchone())

    def test_vig_removal_on_a_real_row(self):
        # Pinnacle, 2701319, 2026-09-22 15:14 UTC: 2.56 / 3.74 / 2.42.
        p_home, p_draw, p_away, overround = remove_vig(2.56, 3.74, 2.42)
        self.assertAlmostEqual(overround, 1 / 2.56 + 1 / 3.74 + 1 / 2.42)
        self.assertAlmostEqual(overround, 1.0712278, places=6)
        self.assertAlmostEqual(p_home, 0.3646517, places=6)
        self.assertAlmostEqual(p_draw, 0.2496011, places=6)
        self.assertAlmostEqual(p_away, 0.3857472, places=6)
        self.assertAlmostEqual(p_home + p_draw + p_away, 1.0)
        stored = self.one(self.full, 2701319, "p_home, p_draw, p_away, overround")
        for got, want in zip(stored, (p_home, p_draw, p_away, overround)):
            self.assertAlmostEqual(got, want)

    def test_row_captured_after_start_is_ignored(self):
        # Snapshot 431 is a parsed Pinnacle row captured 12 s after the start.
        late = self.full.execute(
            "SELECT book, parsed, captured_at, start_utc FROM snapshots.odds_snapshots WHERE id = 431"
        ).fetchone()
        self.assertEqual(tuple(late), ("pinnacle", 1, "2026-09-22T15:30:12.226467+00:00", "2026-09-22T15:30:00Z"))
        self.assertEqual(
            self.one(self.full, 2701319), ("pinnacle", 0, 405, 2.56, 3.74, 2.42, "proportional")
        )
        captured_at, minutes = self.one(self.full, 2701319, "captured_at, minutes_before_start")
        self.assertEqual(captured_at, "2026-09-22T15:14:58.484009+00:00")
        self.assertAlmostEqual(minutes, 15.03, places=2)

    def test_unparsed_closing_row_falls_back_to_last_parsed(self):
        # Pinnacle was inactive on the 13:40 poll before the 14:00 start, so
        # its last parsed row is the 12:00 one.
        self.assertEqual(self.one(self.full, 2701332, "book, odds_snapshot_id"), ("pinnacle", 829))
        self.assertAlmostEqual(self.one(self.full, 2701332, "minutes_before_start")[0], 119.78, places=2)

    def test_bet365_is_used_only_without_pinnacle(self):
        self.assertEqual(
            self.full.execute("SELECT DISTINCT book, is_fallback FROM benchmark_odds").fetchall()[0][:],
            ("pinnacle", 0),
        )
        snapshots = self.dir / "no_pinnacle.snapshots.db"
        shutil.copy(self.snapshots, snapshots)
        conn = sqlite3.connect(snapshots)
        conn.execute("DELETE FROM odds_snapshots WHERE book = 'pinnacle' AND game_id = 2701309")
        conn.commit()
        conn.close()
        fallback = self.build_features("no_pinnacle", snapshots=snapshots)
        book, is_fallback, snapshot_id = self.one(fallback, 2701309, "book, is_fallback, odds_snapshot_id")
        self.assertEqual((book, is_fallback), ("bet365", 1))
        last_bet365 = fallback.execute(
            "SELECT id FROM snapshots.odds_snapshots WHERE game_id = 2701309 AND book = 'bet365' "
            "AND market = '1x2_regulation' AND parsed = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        self.assertEqual(snapshot_id, last_bet365)
        # The other games keep their Pinnacle row.
        self.assertEqual(self.one(fallback, 2701319, "book")[0], "pinnacle")

    def test_one_row_per_game_with_odds(self):
        games = [r[0] for r in self.full.execute("SELECT game_id FROM benchmark_odds ORDER BY game_id")]
        self.assertEqual(games, [2701309, 2701319, 2701332, 2701368])


if __name__ == "__main__":
    unittest.main()
