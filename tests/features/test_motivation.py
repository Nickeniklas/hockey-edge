"""F3d: standings before each game and motivation (Family 7).

Runs on the real games in fixtures/features/mini.sql: 2022 (no relegation,
10 playoff spots), 2025 (12 spots, bottom 2 to the playout) and 2027 (12
spots, bottom 3 down directly; regular season in progress).

Run: python -m unittest discover -s tests
"""

import unittest

from hockey_edge.features import motivation, season_format

from .support import UNPLAYED, FeatureTest, rows

SAIPA, JYP = "933686567:saipa", "219244634:jyp"
JUKURIT = "292293444:jukurit"
# Regular season: early 2025, Jukurit doomed on 2025-02-26, 2027 mid-table.
TARGETS = ((2025, 302), (2025, 423), (2027, 2701363))
VALUES = (
    "games_played", "points", "goal_diff", "table_rank", "games_remaining", "playoff_gap",
    "playoff_clinched", "playoff_eliminated", "relegation_in_force", "relegation_gap",
    "relegation_safe", "relegation_doomed",
)
# Every played game from the target's date on: a 99-0 regulation home win.
POISON = (
    "UPDATE games SET home_goals = 99, away_goals = 0, "
    "finished_type = 'ENDED_DURING_REGULAR_GAME_TIME' "
    "WHERE ended = 1 AND substr(start_utc, 1, 10) >= ?"
)


def _game(conn, season, game_id):
    return conn.execute("SELECT * FROM game_spine WHERE season = ? AND game_id = ?", (season, game_id)).fetchone()


def _row(conn, season, game_id, team_id):
    return conn.execute(
        "SELECT * FROM features_motivation WHERE season = ? AND game_id = ? AND team_id = ?",
        (season, game_id, team_id),
    ).fetchone()


class SeasonFormatTest(FeatureTest):
    def test_curated_lines(self):
        self.assertEqual(season_format.SEASON_FORMAT[2026], (16, 60, True, 12, "none", 0))
        self.assertEqual(season_format.SEASON_FORMAT[2025][3:], (12, "playout_qualification", 2))
        self.assertEqual(season_format.SEASON_FORMAT[2027][3:], (12, "direct_bottom_three", 3))
        self.assertEqual(season_format.SEASON_FORMAT[2020][2:], (False, 10, "none", 0))
        spots = {r[0]: tuple(r)[1:] for r in self.full.execute(
            "SELECT season, playoff_teams, playoff_spots, relegation_spots FROM season_format"
        )}
        self.assertEqual(spots, {2022: (10, 10, 0), 2025: (12, 12, 2), 2027: (None, 12, 3)})

    def test_playoff_teams_are_the_top_of_the_final_table(self):
        for season in (2022, 2025):
            games = self.full.execute(
                "SELECT home_team_id AS home, away_team_id AS away, home_goals, away_goals, end_type "
                "FROM game_spine WHERE season = ? AND phase = 'RUNKOSARJA' AND ended = 1", (season,),
            ).fetchall()
            ranked = motivation.table(season_format.season_teams(self.full, season), games)
            spots, zone = self.full.execute(
                "SELECT playoff_spots, relegation_spots FROM season_format WHERE season = ?", (season,)
            ).fetchone()
            phase_teams = lambda phase: {r[0] for r in self.full.execute(
                "SELECT home_team_id FROM game_spine WHERE season = :s AND phase = :p "
                "UNION SELECT away_team_id FROM game_spine WHERE season = :s AND phase = :p",
                {"s": season, "p": phase},
            )}
            self.assertEqual({r["team_id"] for r in ranked[:spots]}, phase_teams("PLAYOFFS"))
            if zone:
                self.assertEqual({r["team_id"] for r in ranked[-zone:]}, phase_teams("PLAYOUT"))

    def test_playoff_teams_off_the_curated_spots_stop_the_build(self):
        # Without one team's playoff games, 2022 has fewer than 10 playoff teams.
        team = self.full.execute(
            "SELECT home_team_id FROM game_spine WHERE season = 2022 AND phase = 'PLAYOFFS' ORDER BY start_utc LIMIT 1"
        ).fetchone()[0]
        hockey = self.edited_hockey("noPlayoffTeam", (
            "DELETE FROM games WHERE season = 2022 AND phase = 'PLAYOFFS' AND ? IN (home_team_id, away_team_id)",
            (team,),
        ))
        with self.assertRaises(season_format.SeasonFormatError):
            self.build_features("noPlayoffTeam", hockey)

    def test_playoffs_in_progress_do_not_stop_the_build(self):
        # The live build in March: cut at 2025's first PLAYOUT game, only the
        # playoff games played so far exist and 2025 is the last season.
        start = self.full.execute(
            "SELECT MIN(start_utc) FROM game_spine WHERE season = 2025 AND phase = 'PLAYOUT'"
        ).fetchone()[0]
        hockey = self.edited_hockey("livePlayoffs", ("DELETE FROM games WHERE start_utc >= ?", (start,)))
        live = self.build_features("livePlayoffs", hockey)
        teams, spots = live.execute(
            "SELECT playoff_teams, playoff_spots FROM season_format WHERE season = 2025"
        ).fetchone()
        self.assertGreater(teams, 0)
        self.assertLess(teams, spots)


class PointsTest(unittest.TestCase):
    def test_points(self):
        self.assertEqual(motivation.game_points("REG", 3, 1), 3)
        self.assertEqual(motivation.game_points("OT", 3, 2), 2)
        self.assertEqual(motivation.game_points("SO", 2, 3), 1)
        self.assertEqual(motivation.game_points("REG", 0, 4), 0)
        with self.assertRaises(ValueError):
            motivation.game_points("REG", 2, 2)


class MotivationTest(FeatureTest):
    def test_2027_pinned_by_hand(self):
        # 2027:2701363 SaiPa-JYP, 2026-10-08. Before that date SaiPa had 10
        # games: 5 regulation wins, 2 OT/SO wins, 1 OT/SO loss = 20 points,
        # goal difference +11, 4th. JYP 8 games, 6 regulation wins = 18, +9,
        # 6th. 12th KalPa and 13th HPK both had 13 points, the bottom three
        # (HIFK 9, Jokerit 8, KooKoo 1) the best in the zone HIFK's 9.
        self.assertEqual(
            [_row(self.full, 2027, 2701363, SAIPA)[c] for c in VALUES],
            [10, 20, 11, 4, 54, 20 - 13, 0, 0, 1, 20 - 9, 0, 0],
        )
        self.assertEqual(
            [_row(self.full, 2027, 2701363, JYP)[c] for c in VALUES],
            [8, 18, 9, 6, 56, 18 - 13, 0, 0, 1, 18 - 9, 0, 0],
        )

    def test_points_add_up(self):
        # Points over a team's played games = its next game's points.
        for season in (2022, 2025):
            last = self.full.execute("""
                SELECT m.team_id, m.points, m.games_played FROM features_motivation m
                JOIN game_spine s USING (game_id, season)
                WHERE m.season = ? AND s.phase = 'RUNKOSARJA' ORDER BY s.start_utc DESC LIMIT 1
            """, (season,)).fetchone()
            games = self.full.execute(
                "SELECT home_team_id AS home, away_team_id AS away, home_goals, away_goals, end_type "
                "FROM game_spine WHERE season = ? AND phase = 'RUNKOSARJA' AND ended = 1 AND start_date < "
                "(SELECT MAX(start_date) FROM game_spine WHERE season = ? AND phase = 'RUNKOSARJA')",
                (season, season),
            ).fetchall()
            ranked = motivation.table(season_format.season_teams(self.full, season), games)
            me = next(r for r in ranked if r["team_id"] == last["team_id"])
            self.assertEqual((me["points"], me["games_played"]), (last["points"], last["games_played"]))

    def test_other_phases_are_null(self):
        for season, game_id in ((2025, 51169), (2025, 51139), (2022, 49294)):
            for r in self.full.execute(
                "SELECT * FROM features_motivation WHERE season = ? AND game_id = ?", (season, game_id)
            ):
                self.assertEqual([r[c] for c in VALUES], [None] * len(VALUES), (season, game_id))

    def test_no_relegation_season_is_null(self):
        # 2026 has none either (SEASON_FORMAT, SeasonFormatTest); 2022 is
        # the no-relegation season in the fixture.
        counts = self.full.execute("""
            SELECT COUNT(*), SUM(relegation_in_force), COUNT(relegation_gap), COUNT(relegation_safe),
                   COUNT(relegation_doomed), COUNT(playoff_gap)
            FROM features_motivation m JOIN game_spine s USING (game_id, season)
            WHERE m.season = 2022 AND s.phase = 'RUNKOSARJA'
        """).fetchone()
        self.assertEqual(tuple(counts)[1:5], (0, 0, 0, 0))
        self.assertEqual(counts[5], counts[0])

    def test_first_game_of_the_season(self):
        g = self.full.execute(
            "SELECT * FROM game_spine WHERE season = 2025 AND phase = 'RUNKOSARJA' ORDER BY start_utc LIMIT 1"
        ).fetchone()
        r = _row(self.full, 2025, g["game_id"], g["home_team_id"])
        self.assertEqual([r[c] for c in VALUES[:2]] + [r[c] for c in VALUES[4:]],
                         [0, 0, 60, 0, 0, 0, 1, 0, 0, 0])

    def test_flags_hold_at_the_end(self):
        # Conservative bounds: a clinched team finishes in the top spots, an
        # eliminated one outside, a safe one above the zone, a doomed one in it.
        for season in (2022, 2025):
            games = self.full.execute(
                "SELECT home_team_id AS home, away_team_id AS away, home_goals, away_goals, end_type "
                "FROM game_spine WHERE season = ? AND phase = 'RUNKOSARJA' AND ended = 1", (season,),
            ).fetchall()
            final = {r["team_id"]: r["table_rank"] for r in motivation.table(
                season_format.season_teams(self.full, season), games)}
            n, spots, zone = self.full.execute(
                "SELECT n_teams, playoff_spots, relegation_spots FROM season_format WHERE season = ?", (season,)
            ).fetchone()
            checked = 0
            for r in self.full.execute(
                "SELECT * FROM features_motivation WHERE season = ? AND games_played IS NOT NULL", (season,)
            ):
                if r["playoff_clinched"]:
                    self.assertLessEqual(final[r["team_id"]], spots)
                if r["playoff_eliminated"]:
                    self.assertGreater(final[r["team_id"]], spots)
                if r["relegation_safe"]:
                    self.assertLessEqual(final[r["team_id"]], n - zone)
                if r["relegation_doomed"]:
                    self.assertGreater(final[r["team_id"]], n - zone)
                checked += r["playoff_clinched"] + r["playoff_eliminated"] + (r["relegation_safe"] or 0) + (
                    r["relegation_doomed"] or 0)
            self.assertGreater(checked, 40, season)

    def test_jukurit_doomed_in_2025(self):
        r = _row(self.full, 2025, 423, JUKURIT)
        self.assertEqual((r["relegation_doomed"], r["playoff_eliminated"], r["games_remaining"]), (1, 1, 8))
        self.assertLess(r["relegation_gap"], -3 * r["games_remaining"])


class MotivationPointInTimeTest(FeatureTest):
    def test_truncation(self):
        for season, game_id in TARGETS:
            with self.subTest(game=(season, game_id)):
                start = _game(self.full, season, game_id)["start_utc"]
                hockey = self.edited_hockey(
                    f"mtrunc{season}_{game_id}",
                    ("DELETE FROM games WHERE start_utc >= ? AND NOT (season = ? AND game_id = ?)",
                     (start, season, game_id)),
                    (f"UPDATE games SET {UNPLAYED} WHERE season = ? AND game_id = ?", (season, game_id)),
                )
                truncated = self.build_features(f"mtrunc{season}_{game_id}", hockey)
                where = ("season = ? AND game_id = ?", (season, game_id))
                expected = rows(self.full, "features_motivation", *where)
                self.assertEqual(len(expected), 2)
                self.assertEqual(rows(truncated, "features_motivation", *where), expected)

    def test_poison(self):
        for season, game_id in TARGETS:
            with self.subTest(game=(season, game_id)):
                day = _game(self.full, season, game_id)["start_date"]
                hockey = self.edited_hockey(f"mpoison{season}_{game_id}", (POISON, (day,)))
                poisoned = self.build_features(f"mpoison{season}_{game_id}", hockey)
                where = ("season = ? AND game_id = ?", (season, game_id))
                self.assertEqual(
                    rows(poisoned, "features_motivation", *where), rows(self.full, "features_motivation", *where)
                )
                later = "season = ? AND game_id IN (SELECT game_id FROM game_spine WHERE season = ? AND start_date > ?)"
                self.assertNotEqual(
                    rows(poisoned, "features_motivation", later, (season, season, day)),
                    rows(self.full, "features_motivation", later, (season, season, day)),
                )


if __name__ == "__main__":
    unittest.main()
