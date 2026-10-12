"""No leakage: features at a game use only games from earlier dates.

Truncation: features built from the full fixture equal features built from
a copy with every game at or after the target's start deleted. Poison:
absurd results on and after the target's date change nothing at the target.

Run: python -m unittest discover -s tests
"""

import re
import unittest
from pathlib import Path

from hockey_edge.features import history, home, schedule, season_format, spine

from .support import UNPLAYED, FeatureTest, rows

FEATURE_TABLES = ("features_home", "features_schedule")

# (season, game_id). 2025:302 has seven games starting earlier the same day.
TARGETS = ((2025, 302), (2025, 52409), (2027, 2701363))
SAME_DAY_TARGET = (2025, 302)

# Every played game from the target's date on, the target included.
POISON = (
    "UPDATE games SET home_goals = 99, away_goals = 0, "
    "finished_type = 'ENDED_DURING_REGULAR_GAME_TIME' "
    "WHERE ended = 1 AND substr(start_utc, 1, 10) >= ?"
)


class PointInTimeTest(FeatureTest):
    def start(self, season, game_id):
        return self.full.execute(
            "SELECT start_utc FROM game_spine WHERE season = ? AND game_id = ?", (season, game_id)
        ).fetchone()[0]

    def assert_same_at(self, other, season, game_id):
        for table in FEATURE_TABLES:
            expected = rows(self.full, table, "season = ? AND game_id = ?", (season, game_id))
            self.assertEqual(len(expected), 1)
            self.assertEqual(rows(other, table, "season = ? AND game_id = ?", (season, game_id)), expected, table)

    def test_truncation(self):
        for season, game_id in TARGETS:
            with self.subTest(game=(season, game_id)):
                start = self.start(season, game_id)
                hockey = self.edited_hockey(
                    f"trunc{season}_{game_id}",
                    ("DELETE FROM games WHERE start_utc >= ? AND NOT (season = ? AND game_id = ?)",
                     (start, season, game_id)),
                    (f"UPDATE games SET {UNPLAYED} WHERE season = ? AND game_id = ?", (season, game_id)),
                )
                truncated = self.build_features(f"trunc{season}_{game_id}", hockey)
                self.assert_same_at(truncated, season, game_id)
                # The spine target of every earlier game is unchanged too.
                self.assertEqual(
                    rows(truncated, "game_spine", "start_utc < ?", (start,)),
                    rows(self.full, "game_spine", "start_utc < ?", (start,)),
                )

    def test_poison(self):
        for season, game_id in TARGETS:
            with self.subTest(game=(season, game_id)):
                day = self.start(season, game_id)[:10]
                hockey = self.edited_hockey(f"poison{season}_{game_id}", (POISON, (day,)))
                poisoned = self.build_features(f"poison{season}_{game_id}", hockey)
                self.assert_same_at(poisoned, season, game_id)
                self.assertEqual(
                    rows(poisoned, "game_spine", "start_date < ?", (day,)),
                    rows(self.full, "game_spine", "start_date < ?", (day,)),
                )
                # The poison is real: the next game day's features do change.
                later = "season = ? AND game_id IN (SELECT game_id FROM game_spine WHERE season = ? AND start_date > ?)"
                self.assertNotEqual(
                    rows(poisoned, "features_home", later, (season, season, day)),
                    rows(self.full, "features_home", later, (season, season, day)),
                )

    def test_same_day_earlier_games_are_not_history(self):
        season, game_id = SAME_DAY_TARGET
        start = self.start(season, game_id)
        earlier_today = self.full.execute(
            "SELECT COUNT(*) FROM game_spine WHERE start_date = ? AND start_utc < ? AND ended = 1",
            (start[:10], start),
        ).fetchone()[0]
        self.assertGreater(earlier_today, 0)
        history_rows = history.history_before(self.full, season, game_id)
        self.assertGreater(len(history_rows), 100)
        self.assertLess(max(r["start_date"] for r in history_rows), start[:10])
        self.assertTrue(all(r["ended"] for r in history_rows))

    def test_league_window_counts_only_earlier_days(self):
        season, game_id = SAME_DAY_TARGET
        expected = self.full.execute(
            "SELECT COUNT(*) FROM hockey.games WHERE ended = 1 AND season = 2025 "
            "AND phase = 'RUNKOSARJA' AND start_utc < '2025-01-04'"
        ).fetchone()[0]
        league_n = self.full.execute(
            "SELECT league_n FROM features_home WHERE season = ? AND game_id = ?", (season, game_id)
        ).fetchone()[0]
        self.assertEqual(league_n, expected)

    def test_unplayed_earlier_game_is_not_history(self):
        season, game_id = SAME_DAY_TARGET
        hockey = self.edited_hockey(
            "postponed", (f"UPDATE games SET {UNPLAYED} WHERE season = 2025 AND game_id = 1", ())
        )
        postponed = self.build_features("postponed", hockey)
        league_n = "SELECT league_n FROM features_home WHERE season = ? AND game_id = ?"
        self.assertEqual(
            postponed.execute(league_n, (season, game_id)).fetchone()[0],
            self.full.execute(league_n, (season, game_id)).fetchone()[0] - 1,
        )

    def test_first_game_has_no_history(self):
        # 2022 game 1 is the first competitive game in the fixture.
        self.assertEqual(
            rows(self.full, "features_home", "season = 2022 AND game_id = 1"),
            [(1, 2022, 0, None, None, None, None, 0, 0, None, 0.0)],
        )
        self.assertEqual(
            rows(self.full, "features_schedule", "season = 2022 AND game_id = 1"),
            [(1, 2022, None, None, 0, 0)],
        )

    def test_rest_days(self):
        # 2025 PLAYOUT game 2 was played the day after game 1.
        self.assertEqual(
            rows(self.full, "features_schedule", "season = 2025 AND game_id = 51170"),
            [(51170, 2025, 1, 1, 1, 1)],
        )
        # A season opener carries no rest from the season before.
        self.assertEqual(
            rows(self.full, "features_schedule", "season = 2025 AND game_id = 1"),
            [(1, 2025, None, None, 0, 0)],
        )

    def test_unplayed_games_get_features(self):
        missing = self.full.execute(
            "SELECT COUNT(*) FROM game_spine s WHERE is_liiga_competitive = 1 AND NOT EXISTS ("
            "SELECT 1 FROM features_home f WHERE f.season = s.season AND f.game_id = s.game_id)"
        ).fetchone()[0]
        self.assertEqual(missing, 0)


class SourceRulesTest(unittest.TestCase):
    """Static checks on the feature code itself."""

    @staticmethod
    def source(module):
        return Path(module.__file__).read_text(encoding="utf-8")

    def test_feature_modules_read_games_only_through_history(self):
        for module in (home, schedule):
            with self.subTest(module=module.__name__):
                source = self.source(module).replace("hockey_edge.", "")
                self.assertNotRegex(source, r"\b(FROM|JOIN)\b")
                self.assertNotIn("hockey.", source)
                self.assertNotIn("snapshots.", source)
                self.assertNotIn(".execute(", source)

    def test_model_inputs_never_touch_odds(self):
        for module in (spine, history, season_format, home, schedule):
            with self.subTest(module=module.__name__):
                source = self.source(module).lower()
                self.assertNotIn("odds", source)
                self.assertNotIn("benchmark", source)


if __name__ == "__main__":
    unittest.main()
