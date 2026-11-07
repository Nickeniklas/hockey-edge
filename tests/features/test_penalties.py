"""F3a: penalty_events_resolved, the penalised team corrected (D1).

Runs on the real rows in fixtures/features/mini.sql: penalty events and
rosters for five games, one per case (scripts/make_feature_fixture.py,
PENALTY_GAMES). 2018 game 1 and 2023 game 1, where FEATURE_AUDIT item 7
checked the raw responses, have no bench penalty and their seasons are not
in the fixture; the per-season swapped share on the real build covers them.

Run: python -m unittest discover -s tests
"""

import unittest

from hockey_edge.features import history

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
        # penalties sit in HIFK's array, and so does KooKoo's bench penalty.
        self.assertEqual(self.resolved(self.full, 2022, 152), {
            191: (0, HIFK, KOOKOO, "game_direction", 1),
            257: (32033595, HIFK, KOOKOO, "roster", 1),
            447: (26590031, HIFK, KOOKOO, "roster", 1),
        })

    def test_correct_game_with_bench_penalty(self):
        self.assertEqual(self.resolved(self.full, 2027, 2701274), {
            14: (0, HPK, HPK, "game_direction", 0),
            27: (60960736, JUKURIT, JUKURIT, "roster", 0),
            94: (60659010, HPK, HPK, "roster", 0),
        })

    def test_mixed_game_follows_the_majority(self):
        # One stray row (event 151, an Ilves player stored under SaiPa) among
        # six stored right; the bench penalty keeps its stored team.
        rows = self.resolved(self.full, 2025, 231)
        self.assertEqual(rows[151], (60905049, SAIPA, ILVES, "roster", 1))
        self.assertEqual(rows[267], (0, SAIPA, SAIPA, "game_direction", 0))
        self.assertEqual(sum(r[4] for r in rows.values()), 1)
        self.assertEqual(len(rows), 8)

    def test_player_on_both_rosters_takes_the_game_direction(self):
        on_both = self.full.execute(
            "SELECT COUNT(*) FROM hockey.game_rosters WHERE season = 2025 AND game_id = 399 AND player_id = 31539993"
        ).fetchone()[0]
        self.assertEqual(on_both, 2)
        rows = self.resolved(self.full, 2025, 399)
        self.assertEqual(rows[355], (31539993, JUKURIT, JUKURIT, "game_direction", 0))
        self.assertEqual(rows[138], (60351426, LUKKO, LUKKO, "roster", 0))

    def test_game_with_only_a_bench_penalty_is_unresolved(self):
        self.assertEqual(
            [r[2:] for r in self.resolved(self.full, 2022, 35).values()], [(None, "unresolved", None)]
        )

    def test_one_row_per_stored_competitive_penalty(self):
        stored = self.full.execute(
            "SELECT COUNT(*) FROM hockey.game_penalty_events p JOIN game_spine s USING (game_id, season) "
            "WHERE s.is_liiga_competitive = 1"
        ).fetchone()[0]
        self.assertEqual(stored, 20)
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
            # A penalised player missing from both rosters.
            ("DELETE FROM game_rosters WHERE season = 2027 AND game_id = 2701274 AND player_id = 60960736", ()),
            # One of 2022:152's two player penalties moved to the right team:
            # one kept, one swapped, so the game has no direction.
            ("UPDATE game_penalty_events SET team_id = ? WHERE season = 2022 AND game_id = 152 AND event_id = 257",
             (KOOKOO,)),
        )
        cls.edited = cls.build_features("unresolved", hockey)

    def test_player_in_neither_roster(self):
        rows = self.resolved(self.edited, 2027, 2701274)
        self.assertEqual(rows[27], (60960736, JUKURIT, None, "unresolved", None))
        # The game's direction still comes from event 94.
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
