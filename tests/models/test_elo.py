"""Elo engine: update math, date batching, no leakage, carry-over, cold start.

Runs on the feature fixture (seasons 2022, 2025, 2027), so every game is a
real one. Run: python -m unittest discover -s tests
"""

import math
import unittest
from dataclasses import replace

import numpy as np

from features.support import FeatureTest
from hockey_edge.features.history import history_before
from hockey_edge.models import elo

K_ESPOO = "1368624751:k-espoo"
JOKERIT = "238306801:jokerit"
SPORT = "626537494:sport"

# (season, game_id), as in tests/features/test_point_in_time.py.
TARGETS = ((2025, 302), (2025, 52409), (2027, 2701363))


class EloTest(FeatureTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.games = elo.load_games(cls.full)
        cls.teams = elo.load_team_seasons(cls.full)
        cls.params = elo.EloParams()
        cls.result = elo.run(cls.games, cls.teams, cls.params)

    @staticmethod
    def by_key(result):
        return {(r.season, r.game_id): r for r in result.games}

    def season_edges(self, result, season):
        """(start, end) rating per team, from its first and last played game."""
        start, end = {}, {}
        for g, r in zip(self.games, result.games):
            if g.season == season and g.ended:
                start.setdefault(g.home, r.home_pre)
                start.setdefault(g.away, r.away_pre)
                end[g.home], end[g.away] = r.home_post, r.away_post
        return start, end


class UpdateMathTest(EloTest):
    def test_two_games_by_hand(self):
        rated = self.by_key(self.result)
        # 2022:1 Lukko - TPS 3-5, both at 1500. K = 8, home edge 40.
        expected = 1 / (1 + 10 ** (-40 / 400))
        first = rated[2022, 1]
        self.assertEqual((first.home_pre, first.away_pre, first.gap), (1500.0, 1500.0, 0.1))
        self.assertAlmostEqual(first.home_post, 1500 - 8 * expected, places=9)
        self.assertAlmostEqual(first.home_post, 1495.5415, places=4)
        self.assertAlmostEqual(first.away_post, 1504.4585, places=4)
        # 2022:12 Lukko - Ässät 5-1 two days later; Ässät had won at home.
        second = rated[2022, 12]
        self.assertAlmostEqual(second.home_pre, 1495.5415, places=4)
        self.assertAlmostEqual(second.away_pre, 1503.5415, places=4)
        self.assertAlmostEqual(second.gap, 32 / 400, places=9)
        self.assertAlmostEqual(second.home_post, 1495.5415 + 8 * (1 - 1 / (1 + 10 ** (-32 / 400))), places=4)
        self.assertAlmostEqual(second.home_post, 1499.1741, places=4)
        self.assertAlmostEqual(second.away_post, 1499.9089, places=4)

    def test_zero_sum_without_cold_start(self):
        for params in (self.params, replace(self.params, margin=True, s_ot=0.15)):
            with self.subTest(params=params):
                result = elo.run(self.games, self.teams, params)
                played = [r for r in result.games if r.season == 2022 and r.home_post is not None]
                self.assertGreater(len(played), 400)
                for r in played:
                    self.assertAlmostEqual(r.home_post - r.home_pre, r.away_pre - r.away_post, places=9)
                _, end = self.season_edges(result, 2022)
                self.assertEqual(len(end), 15)
                self.assertAlmostEqual(sum(end.values()), 15 * elo.START_RATING, places=6)

    def test_actual_score(self):
        self.assertEqual(elo.actual_score("H", 3, 1, 0.15), 1.0)
        self.assertEqual(elo.actual_score("A", 1, 3, 0.15), 0.0)
        self.assertEqual(elo.actual_score("D", 2, 1, 0.15), 0.65)
        self.assertEqual(elo.actual_score("D", 1, 2, 0.15), 0.35)
        self.assertEqual(elo.actual_score("D", 2, 1, 0.0), 0.5)
        # 2022:49294, the tied regular-time game: no OT/SO winner.
        self.assertEqual(elo.actual_score("D", 2, 2, 0.15), 0.5)

    def test_overtime_winner_gets_s_ot(self):
        # 2022:3 Jukurit - KalPa 2-1 in overtime, both at 1500.
        expected = 1 / (1 + 10 ** (-40 / 400))
        for s_ot, score in ((0.0, 0.5), (0.15, 0.65)):
            with self.subTest(s_ot=s_ot):
                game = self.by_key(elo.run(self.games, self.teams, replace(self.params, s_ot=s_ot)))[2022, 3]
                self.assertAlmostEqual(game.home_post, 1500 + 8 * (score - expected), places=9)

    def test_margin_multiplier(self):
        self.assertAlmostEqual(elo.margin_multiplier(1, 0), math.log(2))
        self.assertAlmostEqual(elo.margin_multiplier(3, 0), math.log(4))
        # Capped at three goals, either sign.
        self.assertEqual(elo.margin_multiplier(7, 0), elo.margin_multiplier(3, 0))
        self.assertEqual(elo.margin_multiplier(-7, 0), elo.margin_multiplier(3, 0))
        # A favourite's win counts for less than an underdog's.
        self.assertLess(elo.margin_multiplier(2, 200), elo.margin_multiplier(2, 0))
        self.assertGreater(elo.margin_multiplier(2, -200), elo.margin_multiplier(2, 0))
        self.assertAlmostEqual(elo.margin_multiplier(2, 200), math.log(3) * 2.2 / 2.4)

    def test_margin_switch_on_real_games(self):
        plain = self.by_key(self.result)
        scaled = self.by_key(elo.run(self.games, self.teams, replace(self.params, margin=True)))
        # 2022:1 Lukko - TPS 3-5: the away side won by two as a 40-point underdog.
        step = plain[2022, 1].home_post - 1500
        self.assertAlmostEqual(
            scaled[2022, 1].home_post - 1500, step * math.log(3) * 2.2 / (2.2 - 0.040), places=9
        )
        # 2022:3 went to overtime: a D game always uses M = 1.
        self.assertEqual(scaled[2022, 3].home_post, plain[2022, 3].home_post)


class DateBatchTest(EloTest):
    def test_same_date_games_do_not_see_each_other(self):
        # 2022:2-6 were all played on 2021-09-10, the second game day.
        # Every one starts from 1500 except nobody: Lukko and TPS, who
        # played the day before, are not among them.
        rated = self.by_key(self.result)
        for game_id in (2, 3, 4, 5, 6):
            self.assertEqual((rated[2022, game_id].home_pre, rated[2022, game_id].away_pre), (1500.0, 1500.0))
            self.assertEqual(rated[2022, game_id].applied_before, 1)
        self.assertEqual(rated[2022, 7].applied_before, 6)

    def test_pre_rating_is_the_post_rating_of_the_teams_previous_game(self):
        last = {}
        for g, r in zip(self.games, self.result.games):
            if g.season != 2022:
                break
            for team, pre, post in ((g.home, r.home_pre, r.home_post), (g.away, r.away_pre, r.away_post)):
                self.assertEqual(pre, last.get(team, 1500.0))
                if post is not None:
                    last[team] = post

    def test_applied_games_equal_history_before(self):
        applied = self.result.applied
        seen: set[tuple[int, int]] = set()
        n_seen = 0
        for r in self.result.games:
            seen.update(applied[n_seen:r.applied_before])
            n_seen = r.applied_before
            history = {
                (h["season"], h["game_id"]) for h in history_before(
                    self.full, r.season, r.game_id, "h.season, h.game_id", "h.feeds_ratings = 1"
                )
            }
            if history != seen:
                self.fail(f"{r.season}:{r.game_id}: pass had {len(seen)} games, history_before {len(history)}")
        self.assertGreater(n_seen, 1000)

    def test_a_team_twice_on_one_date_stops_the_pass(self):
        # Move 2022:7 (Kärpät - JYP) a day back, onto JYP's game against HIFK.
        moved = [g._replace(start_date="2021-09-10") if (g.season, g.game_id) == (2022, 7) else g for g in self.games]
        moved.sort(key=lambda g: (g.start_date, g.season, g.game_id))
        with self.assertRaisesRegex(ValueError, "twice on 2021-09-10"):
            elo.run(moved, self.teams, self.params)

    def test_games_out_of_date_order_stop_the_pass(self):
        with self.assertRaisesRegex(ValueError, "date order"):
            elo.run([self.games[10], *self.games[:10]], self.teams, self.params)


class NoLeakageTest(EloTest):
    def date_of(self, season, game_id):
        return next(g.start_date for g in self.games if (g.season, g.game_id) == (season, game_id))

    def assert_same_before(self, result, date):
        """Predictions up to and including `date` match the full pass."""
        full = self.by_key(self.result)
        checked = 0
        for g, r in zip(self.games, result.games):
            if g.start_date > date:
                break
            self.assertEqual((r.season, r.game_id), (g.season, g.game_id))
            self.assertEqual(r[:5], full[r.season, r.game_id][:5])
            checked += 1
        self.assertGreater(checked, 100)

    def test_truncation(self):
        for season, game_id in TARGETS:
            with self.subTest(game=(season, game_id)):
                date = self.date_of(season, game_id)
                kept = [
                    g._replace(ended=0, home_goals=None, away_goals=None, result=None)
                    if g.start_date == date else g
                    for g in self.games if g.start_date <= date
                ]
                self.assert_same_before(elo.run(kept, self.teams, self.params), date)

    def test_poison(self):
        for season, game_id in TARGETS:
            with self.subTest(game=(season, game_id)):
                date = self.date_of(season, game_id)
                poisoned = elo.run([
                    g._replace(home_goals=99, away_goals=0, result="H")
                    if g.start_date >= date and g.ended else g
                    for g in self.games
                ], self.teams, replace(self.params, margin=True))
                full = elo.run(self.games, self.teams, replace(self.params, margin=True))
                target = self.by_key(poisoned)[season, game_id]
                self.assertEqual(target[:5], self.by_key(full)[season, game_id][:5])
                # The poison is real: later predictions do change.
                later = [
                    (p.gap, f.gap) for g, p, f in zip(self.games, poisoned.games, full.games)
                    if g.start_date > date
                ]
                self.assertGreater(sum(p != f for p, f in later), len(later) // 2)


class CarryOverTest(EloTest):
    def test_returning_teams_move_toward_their_mean(self):
        _, end_2022 = self.season_edges(self.result, 2022)
        start_2025, _ = self.season_edges(self.result, 2025)
        mean = sum(end_2022.values()) / len(end_2022)
        self.assertEqual(len(end_2022), 15)
        for team, rating in end_2022.items():
            self.assertAlmostEqual(start_2025[team], rating + 0.35 * (mean - rating), places=9)
        returning = [start_2025[t] for t in end_2022]
        self.assertAlmostEqual(sum(returning) / len(returning), mean, places=9)

    def test_r_one_resets_and_r_zero_keeps(self):
        _, end_2022 = self.season_edges(self.result, 2022)
        mean = sum(end_2022.values()) / len(end_2022)
        kept, _ = self.season_edges(elo.run(self.games, self.teams, replace(self.params, r=0.0)), 2025)
        reset, _ = self.season_edges(elo.run(self.games, self.teams, replace(self.params, r=1.0)), 2025)
        for team, rating in end_2022.items():
            self.assertAlmostEqual(kept[team], rating, places=9)
            self.assertAlmostEqual(reset[team], mean, places=9)

    def test_a_leaving_team_does_not_move_the_mean(self):
        # Take Sport out of the league after 2022.
        games = [g for g in self.games if g.season == 2022 or SPORT not in (g.home, g.away)]
        teams = {
            season: {t: first for t, first in members.items() if season == 2022 or t != SPORT}
            for season, members in self.teams.items()
        }
        result = elo.run(games, teams, self.params)
        end_2022 = {}
        start_2025 = {}
        for g, r in zip(games, result.games):
            if g.season == 2022:
                end_2022[g.home], end_2022[g.away] = r.home_post, r.away_post
            elif g.season == 2025:
                start_2025.setdefault(g.home, r.home_pre)
                start_2025.setdefault(g.away, r.away_pre)
        self.assertNotAlmostEqual(end_2022[SPORT], 1500.0, places=1)
        stayed = {t: v for t, v in end_2022.items() if t != SPORT}
        mean = sum(stayed.values()) / len(stayed)
        for team, rating in stayed.items():
            self.assertAlmostEqual(start_2025[team], rating + 0.35 * (mean - rating), places=9)
        self.assertNotIn(SPORT, start_2025)
        self.assertNotIn(SPORT, result.ratings)

    def test_a_team_without_a_rating_or_a_flag_stops_the_pass(self):
        teams = {**self.teams, 2025: {**self.teams[2025], K_ESPOO: False}}
        with self.assertRaisesRegex(ValueError, "k-espoo"):
            elo.run(self.games, teams, self.params)


class ColdStartTest(EloTest):
    def step_ratio(self, result, season, game_id, team_is_home):
        """New team's rating change over its opponent's, as a positive ratio."""
        r = self.by_key(result)[season, game_id]
        home, away = r.home_post - r.home_pre, r.away_post - r.away_pre
        return -home / away if team_is_home else -away / home

    def test_first_stored_season_has_no_cold_start(self):
        self.assertEqual([(c.season, c.team_id) for c in self.result.cold_starts], [(2025, K_ESPOO), (2027, JOKERIT)])

    def test_no_earlier_case_enters_at_the_lowest_returning_rating(self):
        k_espoo = self.result.cold_starts[0]
        start_2025, _ = self.season_edges(self.result, 2025)
        returning = [v for t, v in start_2025.items() if t != K_ESPOO]
        self.assertEqual((k_espoo.path, k_espoo.n_cases), ("lowest_returning", 0))
        self.assertEqual(len(returning), 15)
        self.assertAlmostEqual(k_espoo.entry, min(returning), places=9)
        self.assertEqual(start_2025[K_ESPOO], k_espoo.entry)

    def test_one_earlier_case_gives_the_delta_path(self):
        jokerit = self.result.cold_starts[1]
        self.assertEqual((jokerit.path, jokerit.n_cases), ("delta", 1))
        # The one earlier case: K-Espoo's 2025 goal difference per game.
        goal_diff = self.full.execute(
            "SELECT AVG(CASE WHEN home_team_id = :t THEN home_goals - away_goals "
            "ELSE away_goals - home_goals END) FROM game_spine WHERE season = 2025 "
            "AND feeds_ratings = 1 AND ended = 1 AND :t IN (home_team_id, away_team_id)", {"t": K_ESPOO},
        ).fetchone()[0]
        self.assertLess(goal_diff, 0)
        self.assertAlmostEqual(jokerit.mean_goal_diff, goal_diff, places=9)
        # Slope of final margin on pre-game rating gap, every game before 2027.
        earlier = [(g, r) for g, r in zip(self.games, self.result.games) if g.season < 2027 and g.ended]
        slope = np.polyfit(
            [r.home_pre - r.away_pre for _, r in earlier], [g.home_goals - g.away_goals for g, _ in earlier], 1
        )[0]
        self.assertGreater(slope, 0)
        self.assertAlmostEqual(jokerit.slope, slope, places=9)
        self.assertAlmostEqual(jokerit.delta, -goal_diff / slope, places=6)

        start_2027, _ = self.season_edges(self.result, 2027)
        returning = [v for t, v in start_2027.items() if t != JOKERIT]
        self.assertEqual(len(returning), 16)
        self.assertAlmostEqual(jokerit.returning_mean, sum(returning) / 16, places=9)
        self.assertAlmostEqual(start_2027[JOKERIT], jokerit.returning_mean - jokerit.delta, places=9)

    def test_k_multiplier_lasts_n_games(self):
        # K-Espoo's 15th game was 2025:115 (away), its 16th 2025:119 (home).
        self.assertAlmostEqual(self.step_ratio(self.result, 2025, 1, False), 2.0, places=9)
        self.assertAlmostEqual(self.step_ratio(self.result, 2025, 115, False), 2.0, places=9)
        self.assertAlmostEqual(self.step_ratio(self.result, 2025, 119, True), 1.0, places=9)
        # Jokerit's first game, 2027:2701280 at Sport.
        self.assertAlmostEqual(self.step_ratio(self.result, 2027, 2701280, False), 2.0, places=9)
        shorter = elo.run(self.games, self.teams, replace(self.params, cold_m=3.0, cold_n=1))
        self.assertAlmostEqual(self.step_ratio(shorter, 2025, 1, False), 3.0, places=9)
        self.assertAlmostEqual(self.step_ratio(shorter, 2025, 13, True), 1.0, places=9)

    def test_fixed_delta_for_the_sensitivity_run(self):
        flat = elo.run(self.games, self.teams, replace(self.params, cold_delta=0.0, cold_m=1.0))
        for case in flat.cold_starts:
            self.assertEqual((case.path, case.delta), ("fixed", 0.0))
            self.assertEqual(case.entry, case.returning_mean)
        self.assertAlmostEqual(self.step_ratio(flat, 2025, 1, False), 1.0, places=9)
        # Nothing before the first cold start depends on these settings.
        full = self.by_key(self.result)
        for r in flat.games:
            if r.season == 2022:
                self.assertEqual(r, full[r.season, r.game_id])


if __name__ == "__main__":
    unittest.main()
