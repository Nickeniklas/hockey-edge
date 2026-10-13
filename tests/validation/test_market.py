"""Market benchmark: the closing-line filter, books kept apart, log loss.

Run: python -m unittest discover -s tests
"""

import math
import shutil
import sqlite3
import unittest
from pathlib import Path

import numpy as np

from features.support import FeatureTest
from hockey_edge.models import versions
from hockey_edge.validation import harness, market, report

from .test_harness import CONFIG


class MarketTest(FeatureTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        data = harness.load(cls.full)
        cls.backtest = harness.connect(cls.dir / "backtest.db")
        cls.addClassCleanup(cls.backtest.close)
        cls.run_id = harness.store(cls.backtest, data, CONFIG, harness.backtest(data, CONFIG))

    def edited_features(self, name, *statements):
        """A copy of the built features.db with SQL statements applied."""
        path = self.dir / f"{name}.features.db"
        shutil.copy(self.dir / "full.features.db", path)
        conn = sqlite3.connect(path)
        self.addCleanup(conn.close)
        for sql, params in statements:
            conn.execute(sql, params)
        conn.commit()
        return conn

    def test_closing_lines_are_at_most_sixty_minutes_out(self):
        # The fixture has odds for four games. 2701332 has only a row from two
        # hours out (Pinnacle went inactive) and 2701368 one from two days out.
        self.assertEqual(self.full.execute("SELECT COUNT(*) FROM benchmark_odds").fetchone()[0], 4)
        lines = market.closing_lines(self.full)
        self.assertEqual([(line.season, line.game_id) for line in lines], [(2027, 2701309), (2027, 2701319)])
        self.assertLessEqual(max(line.minutes_before_start for line in lines), 60)

    def test_sixty_is_in_and_sixty_one_is_out(self):
        first, second = [(line.season, line.game_id) for line in market.closing_lines(self.full)[:2]]
        edited = self.edited_features(
            "edge",
            ("UPDATE benchmark_odds SET minutes_before_start = 60 WHERE season = ? AND game_id = ?", first),
            ("UPDATE benchmark_odds SET minutes_before_start = 61 WHERE season = ? AND game_id = ?", second),
        )
        kept = {(line.season, line.game_id) for line in market.closing_lines(edited)}
        self.assertIn(first, kept)
        self.assertNotIn(second, kept)
        self.assertEqual(len(kept), len(market.closing_lines(self.full)) - 1)

    def test_market_log_loss_by_hand(self):
        # 2027:2701319 HPK - Lukko 1-3. Pinnacle 2.56 / 3.74 / 2.42, 15 minutes out.
        overround = 1 / 2.56 + 1 / 3.74 + 1 / 2.42
        expected = -math.log((1 / 2.42) / overround)
        self.assertAlmostEqual(expected, 0.952573, places=6)
        (pinnacle,) = market.compare(self.backtest, self.full, self.run_id)
        at = pinnacle.games.index((2027, 2701319))
        self.assertAlmostEqual(pinnacle.loss[market.MARKET][at], expected, places=9)

    def test_same_games_for_every_model(self):
        (pinnacle,) = market.compare(self.backtest, self.full, self.run_id)
        self.assertEqual((pinnacle.book, pinnacle.is_fallback), ("pinnacle", 0))
        self.assertEqual(
            set(pinnacle.loss),
            {market.MARKET, harness.ELO, harness.ELO_FLAT_ENTRY, harness.BASE_RATE, harness.GD_RATING},
        )
        for loss in pinnacle.loss.values():
            self.assertEqual(len(loss), len(pinnacle.games))
        # Only played regular-season games with a stored prediction.
        played = {
            tuple(r) for r in self.full.execute(
                "SELECT season, game_id FROM game_spine WHERE in_eval_sample = 1 AND ended = 1"
            )
        }
        self.assertTrue(set(pinnacle.games) <= played)
        stored = self.backtest.execute(
            "SELECT p_home, p_draw, p_away, result FROM backtest_prediction "
            "WHERE run_id = ? AND model = 'elo' AND season = 2027 AND game_id = 2701319", (self.run_id,)
        ).fetchone()
        at = pinnacle.games.index((2027, 2701319))
        self.assertEqual(stored["result"], "A")
        self.assertAlmostEqual(pinnacle.loss[harness.ELO][at], -math.log(stored["p_away"]), places=12)

    def test_books_are_never_pooled(self):
        (pinnacle,) = market.compare(self.backtest, self.full, self.run_id)
        self.assertEqual(pinnacle.games, [(2027, 2701309), (2027, 2701319)])
        # Make the first game one that only bet365 priced.
        edited = self.edited_features("fallback", (
            "UPDATE benchmark_odds SET book = 'bet365', is_fallback = 1 WHERE season = 2027 AND game_id = 2701309", ()
        ))
        primary, fallback = market.compare(self.backtest, edited, self.run_id)
        self.assertEqual((primary.book, fallback.book, fallback.is_fallback), ("pinnacle", "bet365", 1))
        self.assertEqual(fallback.games, [(2027, 2701309)])
        self.assertEqual(primary.games, [(2027, 2701319)])
        np.testing.assert_array_equal(primary.loss[market.MARKET], pinnacle.loss[market.MARKET][1:])
        np.testing.assert_array_equal(fallback.loss[market.MARKET], pinnacle.loss[market.MARKET][:1])
        text = "\n".join(market.render(self.backtest, edited, self.run_id, report.LABELS))
        self.assertIn("**pinnacle**, 1 game\n", text)
        self.assertIn("**bet365** (fallback: games Pinnacle did not price), 1 game\n", text)
        # One game per book: no paired difference can be stated for any model.
        self.assertEqual(text.count("| n/a |"), 8)
        self.assertNotIn("Per-game SD", text)

    def test_games_needed(self):
        # 2 SE = 0.01 with SD 0.15: n = (2 * 0.15 / 0.01)^2.
        self.assertEqual(market.games_needed(0.15), 900)
        self.assertEqual(market.games_needed(0.1, 0.02), 100)

    def test_report_has_the_market_and_version_sections(self):
        text = report.render(self.backtest, self.full, self.run_id)
        self.assertIn("## Market benchmark", text)
        self.assertIn("| Market (pinnacle) |", text)
        self.assertIn("Per-game SD of Elo − market", text)
        self.assertIn("## Tagged version: `elo-1`", text)
        # The fixture's last fold is a two-config grid, not the tagged config.
        self.assertIn("**DOES NOT match**", text)

    def test_no_closing_lines_is_not_an_error(self):
        empty = self.edited_features("empty", ("DELETE FROM benchmark_odds", ()))
        self.assertEqual(market.compare(self.backtest, empty, self.run_id), [])
        self.assertIn("No game has both", "\n".join(market.render(self.backtest, empty, self.run_id, report.LABELS)))


class TaggedVersionTest(unittest.TestCase):
    def test_elo_1(self):
        self.assertEqual(versions.ELO_1_VERSION, "elo-1")
        self.assertEqual(
            (versions.ELO_1_PARAMS.k, versions.ELO_1_PARAMS.hfa, versions.ELO_1_PARAMS.r), (10, 0.0, 0.5)
        )
        self.assertTrue(versions.ELO_1_PARAMS.margin)
        self.assertIsNone(versions.ELO_1_PARAMS.cold_delta)
        self.assertLess(versions.ELO_1_LINK.c1, versions.ELO_1_LINK.c2)
        self.assertEqual(versions.ELO_1_PARAMS.hfa, harness.FIXED_PARAMS["hfa"])

    def test_only_market_reads_odds(self):
        for module in (report, versions):
            source = Path(module.__file__).read_text(encoding="utf-8")
            for banned in ("benchmark_odds", "odds_snapshots", "snapshots."):
                self.assertNotIn(banned, source, module.__name__)
        self.assertIn("benchmark_odds", Path(market.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
