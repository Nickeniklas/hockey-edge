"""game_spine: which games count, and the regulation 1X2 target.

Run: python -m unittest discover -s tests
"""

import unittest

from hockey_edge.features.spine import RATINGS_EXCLUSIONS

from .support import FeatureTest

FLAGS = "phase, is_liiga_competitive, has_non_liiga_team, feeds_ratings, in_eval_sample"
TARGET = "end_type, regulation_result, home_goals, away_goals, end_type_anomaly"


class SpineTest(FeatureTest):
    def one(self, columns, season, game_id):
        return tuple(self.full.execute(
            f"SELECT {columns} FROM game_spine WHERE season = ? AND game_id = ?", (season, game_id)
        ).fetchone())

    def test_every_game_has_a_row(self):
        self.assertEqual(
            self.full.execute("SELECT COUNT(*) FROM game_spine").fetchone()[0],
            self.full.execute("SELECT COUNT(*) FROM hockey.games").fetchone()[0],
        )

    def test_preseason_phases_are_excluded(self):
        counts = self.full.execute(
            "SELECT COUNT(*), SUM(is_liiga_competitive + feeds_ratings + in_eval_sample) "
            "FROM game_spine WHERE phase IN ('PRACTICE', 'PITSITURNAUS')"
        ).fetchone()
        self.assertGreater(counts[0], 100)
        self.assertEqual(counts[1], 0)
        self.assertEqual(self.one("phase, is_liiga_competitive", 2027, 2705869), ("PITSITURNAUS", 0))

    def test_foreign_friendly_is_flagged_by_team_id(self):
        # HC Davos - HIFK
        self.assertEqual(self.one(FLAGS, 2025, 1072), ("PRACTICE", 0, 1, 0, 0))

    def test_jokerit_qualification_series_does_not_feed_ratings(self):
        for game_id in (51139, 51140, 51141, 51142, 51143):
            with self.subTest(game_id=game_id):
                self.assertEqual(self.one(FLAGS, 2025, game_id), ("QUALIFICATIONS", 1, 1, 0, 0))

    def test_playout_series_feeds_ratings_but_is_not_evaluated(self):
        for game_id in (51169, 51170, 51171, 51172, 51174):
            with self.subTest(game_id=game_id):
                self.assertEqual(self.one(FLAGS, 2025, game_id), ("PLAYOUT", 1, 0, 1, 0))

    def test_regular_season_and_playoffs(self):
        self.assertEqual(self.one(FLAGS, 2025, 302), ("RUNKOSARJA", 1, 0, 1, 1))
        self.assertEqual(self.one(FLAGS, 2025, 52409), ("PLAYOFFS", 1, 0, 1, 0))

    def test_regulation_result(self):
        cases = {
            (2025, 51169): ("REG", "H", 2, 1, 0),  # Pelicans - Jukurit 2-1
            (2025, 51171): ("REG", "A", 3, 8, 0),  # Pelicans - Jukurit 3-8
            (2025, 51170): ("OT", "D", 2, 1, 0),  # Jukurit - Pelicans 2-1 in overtime
            (2027, 2701323): ("SO", "D", 1, 2, 0),  # SaiPa - Ässät 1-2 in a shootout
        }
        for (season, game_id), expected in cases.items():
            with self.subTest(game=(season, game_id)):
                self.assertEqual(self.one(TARGET, season, game_id), expected)

    def test_tied_regular_time_playoff_game_is_a_flagged_draw(self):
        # 2022:49294 HPK - Lukko 2-2, stored as ended in regular time.
        self.assertEqual(self.one(TARGET, 2022, 49294), ("REG", "D", 2, 2, 1))
        self.assertEqual(self.one(FLAGS, 2022, 49294), ("PLAYOFFS", 1, 0, 1, 0))
        anomalies = self.full.execute(
            "SELECT season, game_id FROM game_spine WHERE end_type_anomaly = 1 AND is_liiga_competitive = 1"
        ).fetchall()
        self.assertEqual([tuple(r) for r in anomalies], [(2022, 49294)])

    def test_sudden_death_tiebreak_does_not_feed_ratings(self):
        # 2022:49298 KooKoo - Pelicans 1-0, 2,101 seconds, right after 49296.
        self.assertEqual(self.one(FLAGS, 2022, 49298), ("PLAYOFFS", 1, 0, 0, 0))
        self.assertEqual(self.one(TARGET, 2022, 49298), ("REG", "H", 1, 0, 0))
        excluded = self.full.execute(
            "SELECT season, game_id, feeds_ratings, ratings_exclusion_reason FROM game_spine "
            "WHERE ratings_exclusion_reason IS NOT NULL"
        ).fetchall()
        self.assertEqual([tuple(r) for r in excluded], [(2022, 49298, 0, RATINGS_EXCLUSIONS[2022, 49298])])
        self.assertIn("tiebreak", RATINGS_EXCLUSIONS[2022, 49298])

    def test_exclusion_changes_only_the_one_flag(self):
        # Every other competitive all-Liiga game still feeds ratings.
        counts = self.full.execute(
            "SELECT SUM(is_liiga_competitive AND NOT has_non_liiga_team), SUM(feeds_ratings) FROM game_spine"
        ).fetchone()
        self.assertEqual(counts[1], counts[0] - len(RATINGS_EXCLUSIONS))

    def test_unplayed_game_has_no_result(self):
        unplayed = self.full.execute(
            "SELECT COUNT(*), COUNT(home_goals), COUNT(away_goals), COUNT(end_type), "
            "COUNT(regulation_result) FROM game_spine WHERE ended = 0"
        ).fetchone()
        self.assertGreater(unplayed[0], 100)
        self.assertEqual(tuple(unplayed)[1:], (0, 0, 0, 0))
        played = self.full.execute(
            "SELECT COUNT(*) FROM game_spine WHERE ended = 1 AND regulation_result IS NULL"
        ).fetchone()[0]
        self.assertEqual(played, 0)


if __name__ == "__main__":
    unittest.main()
