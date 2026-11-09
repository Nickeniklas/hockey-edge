"""F3a: penalty_events_resolved, the penalised team corrected (D1).

Runs on the real rows in fixtures/features/mini.sql: penalty events, goal
events and the rosters of penalised players and sufferers for five games
picked per case (scripts/make_feature_fixture.py, PENALTY_GAMES), and for
the ended competitive games of 2027 and of 2025 from 2025-01-22 to
2025-02-23. 2018 game 1 and 2023 game 1, where FEATURE_AUDIT item 7 checked
the raw responses, have no bench penalty and their seasons are not in the
fixture; the per-season swapped share on the real build covers them.

Run: python -m unittest discover -s tests
"""

import unittest

from hockey_edge.features import history, penalty_report

from .support import FeatureTest

KOOKOO, HIFK = "461765763:kookoo", "168761288:hifk"
JUKURIT, HPK = "292293444:jukurit", "55786244:hpk"
ILVES, SAIPA = "951626834:ilves", "933686567:saipa"
LUKKO = "624554857:lukko"


class PenaltyTest(FeatureTest):
    def resolved(self, conn, season, game_id):
        """{event_id: (player_id, stored, team, source, was_swapped)}"""
        return {
            r["event_id"]: (r["player_id"], r["stored_team_id"], r["team_id"], r["team_source"], r["was_swapped"])
            for r in conn.execute(
                "SELECT * FROM penalty_events_resolved WHERE season = ? AND game_id = ?", (season, game_id)
            )
        }


class ResolvedTeamTest(PenaltyTest):
    def test_swapped_game_with_bench_penalty(self):
        # 2022 is stored the wrong way round in every game: KooKoo players'
        # penalties sit in HIFK's array, and so does KooKoo's bench penalty,
        # served by a KooKoo player.
        self.assertEqual(self.resolved(self.full, 2022, 152), {
            191: (0, HIFK, KOOKOO, "sufferer_roster", 1),
            257: (32033595, HIFK, KOOKOO, "roster", 1),
            447: (26590031, HIFK, KOOKOO, "roster", 1),
        })

    def test_correct_game_with_bench_penalty(self):
        self.assertEqual(self.resolved(self.full, 2027, 2701274), {
            14: (0, HPK, HPK, "sufferer_roster", 0),
            27: (60960736, JUKURIT, JUKURIT, "roster", 0),
            94: (60659010, HPK, HPK, "roster", 0),
        })

    def test_bench_penalty_does_not_follow_the_player_penalties(self):
        # 2025:231: six player penalties stored right and one stray row
        # (event 151, an Ilves player stored under SaiPa). The bench
        # penalty is stored under SaiPa too, but an Ilves player served it:
        # in 2025 liiga.fi stores bench penalties under the other team.
        rows = self.resolved(self.full, 2025, 231)
        self.assertEqual(rows[151], (60905049, SAIPA, ILVES, "roster", 1))
        self.assertEqual(rows[267], (0, SAIPA, ILVES, "sufferer_roster", 1))
        self.assertEqual(len(rows), 8)

    def test_player_on_both_rosters_takes_the_game_direction(self):
        on_both = self.full.execute(
            "SELECT COUNT(*) FROM hockey.game_rosters WHERE season = 2025 AND game_id = 399 AND player_id = 31539993"
        ).fetchone()[0]
        self.assertEqual(on_both, 2)
        rows = self.resolved(self.full, 2025, 399)
        self.assertEqual(rows[355], (31539993, JUKURIT, JUKURIT, "game_direction", 0))
        self.assertEqual(rows[138], (60351426, LUKKO, LUKKO, "roster", 0))

    def test_game_with_only_a_bench_penalty_resolves_by_sufferer(self):
        # 2022:35 has no player penalty, so no direction; the sufferer, a
        # Tappara player, decides.
        self.assertEqual(
            [r[2:] for r in self.resolved(self.full, 2022, 35).values()],
            [("362185137:tappara", "sufferer_roster", 1)],
        )

    def test_goal_shortened_penalties_name_the_team_that_conceded(self):
        # A two-minute penalty ended early at the second of a goal was ended
        # by that goal, so the team that did not score took it. Real games
        # of 2025 and 2027; on the full data it holds for all 335 bench
        # penalties (the game's direction alone: 224) and 6,947 player ones.
        checked = {}
        for r in penalty_report.goal_shortened(self.full):
            key = ("bench" if r["kind"] == "bench" else "player", r["team_source"])
            right, wrong = checked.get(key, (0, 0))
            checked[key] = (right + r["right"], wrong + r["wrong"])
        self.assertEqual(sum(w for _, w in checked.values()), 0, checked)
        self.assertGreaterEqual(checked[("bench", "sufferer_roster")][0], 10, checked)
        self.assertGreaterEqual(checked[("player", "roster")][0], 200, checked)

    def test_one_row_per_stored_competitive_penalty(self):
        stored = self.full.execute(
            "SELECT COUNT(*) FROM hockey.game_penalty_events p JOIN game_spine s USING (game_id, season) "
            "WHERE s.is_liiga_competitive = 1"
        ).fetchone()[0]
        self.assertGreater(stored, 1000)
        self.assertEqual(self.full.execute("SELECT COUNT(*) FROM penalty_events_resolved").fetchone()[0], stored)

    def test_resolved_team_is_one_of_the_game_teams(self):
        bad = self.full.execute(
            "SELECT COUNT(*) FROM penalty_events_resolved r JOIN game_spine s USING (game_id, season) "
            "WHERE r.team_id IS NOT NULL AND r.team_id NOT IN (s.home_team_id, s.away_team_id)"
        ).fetchone()[0]
        self.assertEqual(bad, 0)

    def test_hockey_db_keeps_the_stored_team(self):
        # The correction lives in features.db only.
        self.assertEqual(
            self.full.execute(
                "SELECT team_id FROM hockey.game_penalty_events WHERE season = 2022 AND game_id = 152 AND event_id = 257"
            ).fetchone()[0],
            HIFK,
        )

    def test_read_through_history_before(self):
        def events(season, game_id):
            return {
                (r["season"], r["game_id"]) for r in history.history_before(
                    self.full, season, game_id, "p.season, p.game_id", per_game="penalty_events_resolved"
                )
            }
        # 2027:2701274 was played on 2026-09-01.
        self.assertNotIn((2027, 2701274), events(2027, 2701274))
        later = self.full.execute(
            "SELECT game_id FROM game_spine WHERE season = 2027 AND start_date > '2026-09-01' "
            "AND is_liiga_competitive = 1 ORDER BY start_utc LIMIT 1"
        ).fetchone()[0]
        self.assertIn((2027, 2701274), events(2027, later))


class UnresolvedTest(PenaltyTest):
    """Real games edited into the two unresolved cases the data rarely has."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        hockey = cls.edited_hockey(
            "unresolved",
            # A penalised player missing from both rosters, and a bench
            # penalty with no sufferer in the same game.
            ("DELETE FROM game_rosters WHERE season = 2027 AND game_id = 2701274 AND player_id = 60960736", ()),
            ("UPDATE game_penalty_events SET sufferer_player_id = NULL "
             "WHERE season = 2027 AND game_id = 2701274 AND event_id = 14", ()),
            # One of 2022:152's two player penalties moved to the right team:
            # one kept, one swapped, so the game has no direction; and its
            # bench penalty loses its sufferer.
            ("UPDATE game_penalty_events SET team_id = ? WHERE season = 2022 AND game_id = 152 AND event_id = 257",
             (KOOKOO,)),
            ("UPDATE game_penalty_events SET sufferer_player_id = NULL "
             "WHERE season = 2022 AND game_id = 152 AND event_id = 191", ()),
        )
        cls.edited = cls.build_features("unresolved", hockey)

    def test_player_in_neither_roster(self):
        rows = self.resolved(self.edited, 2027, 2701274)
        self.assertEqual(rows[27], (60960736, JUKURIT, None, "unresolved", None))
        # Without a sufferer the bench penalty takes the game's direction,
        # which still comes from event 94.
        self.assertEqual(rows[14], (0, HPK, HPK, "game_direction", 0))

    def test_tied_direction_leaves_the_bench_penalty_unresolved(self):
        self.assertEqual(self.resolved(self.edited, 2022, 152), {
            191: (0, HIFK, None, "unresolved", None),
            257: (32033595, KOOKOO, KOOKOO, "roster", 0),
            447: (26590031, HIFK, KOOKOO, "roster", 1),
        })

    def test_a_third_team_stops_the_build(self):
        hockey = self.edited_hockey("third_team", (
            "UPDATE game_penalty_events SET team_id = '238306801:jokerit' "
            "WHERE season = 2025 AND game_id = 399 AND event_id = 138", (),
        ))
        with self.assertRaisesRegex(ValueError, "outside their game"):
            self.build_features("third_team", hockey)


if __name__ == "__main__":
    unittest.main()
