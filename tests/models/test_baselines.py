"""Baselines: base rates and the goal-difference rating.

Run: python -m unittest discover -s tests
"""

import unittest

import numpy as np

from features.support import FeatureTest
from hockey_edge.models import baselines, elo

HIFK = "168761288:hifk"


class BaseRateTest(unittest.TestCase):
    def test_base_rates(self):
        rates = baselines.base_rates(["H", "H", "D", "A", "H", "A", "H", "D"])
        np.testing.assert_allclose(rates, [0.5, 0.25, 0.25])


class GoalDiffTest(FeatureTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.games = elo.load_games(cls.full)
        cls.gaps = dict(zip(((g.season, g.game_id) for g in cls.games), baselines.goal_diff_gaps(cls.games)))

    def test_one_gap_per_game(self):
        self.assertEqual(len(self.gaps), len(self.games))

    def test_first_stored_season_starts_level(self):
        # 2022:1, and the five games of the next day: nobody has a result
        # from an earlier date except Lukko and TPS, who are not playing.
        for game_id in (1, 2, 3, 4, 5, 6):
            self.assertEqual(self.gaps[2022, game_id], 0.0)

    def test_rating_by_hand(self):
        # 2022:12 Lukko - Ässät. Lukko had lost 3-5, Ässät had won 4-1:
        # -2 and +3 goals over one game, each with 10 games of a zero prior.
        self.assertAlmostEqual(self.gaps[2022, 12], -2 / 11 - 3 / 11)

    def test_prior_is_half_of_last_season(self):
        # 2025:1 HIFK - K-Espoo opens 2025. K-Espoo is new, so the gap is
        # HIFK's prior: half its 2022 goal difference per game.
        last = self.full.execute(
            "SELECT AVG(CASE WHEN home_team_id = :t THEN home_goals - away_goals "
            "ELSE away_goals - home_goals END) FROM game_spine WHERE season = 2022 "
            "AND feeds_ratings = 1 AND ended = 1 AND :t IN (home_team_id, away_team_id)", {"t": HIFK},
        ).fetchone()[0]
        self.assertNotEqual(last, 0)
        self.assertAlmostEqual(self.gaps[2025, 1], 0.5 * last)

    def test_later_results_do_not_change_a_gap(self):
        date = next(g.start_date for g in self.games if (g.season, g.game_id) == (2025, 302))
        poisoned = baselines.goal_diff_gaps([
            g._replace(home_goals=99, away_goals=0, result="H") if g.ended and g.start_date >= date else g
            for g in self.games
        ])
        changed = 0
        for g, gap in zip(self.games, poisoned):
            if g.start_date <= date:
                self.assertEqual(gap, self.gaps[g.season, g.game_id])
            else:
                changed += gap != self.gaps[g.season, g.game_id]
        self.assertGreater(changed, 100)


if __name__ == "__main__":
    unittest.main()
