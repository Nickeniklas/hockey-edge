"""F2b: shot attempts (Corsi only, D4) and team xG (Family 2).

Runs on the real rows in fixtures/features/mini.sql: shot_events for 2027
and for 2025 from 2025-01-22 to 2025-02-23, which runs into the 2025
shot_events gap. 2025 games before 2025-01-22 have no shot rows in the
fixture, so they look like gap games here too.

Run: python -m unittest discover -s tests
"""

import unittest

from hockey_edge.features import history, team_shots

from .support import UNPLAYED, FeatureTest, rows

# 2025:412 KalPa-Kärpät is played inside the gap; 2027:2701437 is the last
# game before the November break.
TARGETS = ((2025, 412), (2027, 2701363), (2027, 2701437))

# From the target's date on: every attempt becomes a home goal and every
# home xG 99.
SHOT_POISON = (
    "UPDATE shot_events SET event_type = 'GOAL', shooting_team_id = ("
    "SELECT substr(g.home_team_id, 1, instr(g.home_team_id, ':') - 1) FROM games g "
    "WHERE g.season = shot_events.season AND g.game_id = shot_events.game_id) "
    "WHERE (game_id, season) IN (SELECT game_id, season FROM games "
    "WHERE ended = 1 AND substr(start_utc, 1, 10) >= ?)"
)
XG_POISON = "UPDATE games SET home_expected_goals = 99 WHERE ended = 1 AND substr(start_utc, 1, 10) >= ?"

CORSI_COLUMNS = ", ".join(
    f"{prefix}{name}_{n}"
    for n in (10, 25, 40)
    for prefix, name in (("", "corsi_n"), ("", "corsi_for"), ("", "corsi_against"), ("", "corsi_share"),
                         ("ev_", "corsi_for"), ("ev_", "corsi_against"), ("ev_", "corsi_share"))
)


def _start(conn, season, game_id, column="start_utc"):
    return conn.execute(
        f"SELECT {column} FROM game_spine WHERE season = ? AND game_id = ?", (season, game_id)
    ).fetchone()[0]


class ShotPointInTimeTest(FeatureTest):
    def test_truncation(self):
        for season, game_id in TARGETS:
            with self.subTest(game=(season, game_id)):
                start = _start(self.full, season, game_id)
                hockey = self.edited_hockey(
                    f"strunc{season}_{game_id}",
                    ("DELETE FROM games WHERE start_utc >= ? AND NOT (season = ? AND game_id = ?)",
                     (start, season, game_id)),
                    (f"UPDATE games SET {UNPLAYED} WHERE season = ? AND game_id = ?", (season, game_id)),
                )
                truncated = self.build_features(f"strunc{season}_{game_id}", hockey)
                where = ("season = ? AND game_id = ?", (season, game_id))
                expected = rows(self.full, "features_shots", *where)
                self.assertEqual(len(expected), 2)
                self.assertEqual(rows(truncated, "features_shots", *where), expected)

    def test_poison(self):
        for season, game_id in TARGETS:
            with self.subTest(game=(season, game_id)):
                day = _start(self.full, season, game_id, "start_date")
                hockey = self.edited_hockey(f"spoison{season}_{game_id}", (SHOT_POISON, (day,)), (XG_POISON, (day,)))
                poisoned = self.build_features(f"spoison{season}_{game_id}", hockey)
                where = ("season = ? AND game_id = ?", (season, game_id))
                self.assertEqual(rows(poisoned, "features_shots", *where), rows(self.full, "features_shots", *where))
                # The poison is real: later games in the season do change.
                later = "season = ? AND game_id IN (SELECT game_id FROM game_spine WHERE season = ? AND start_date > ?)"
                self.assertNotEqual(
                    rows(poisoned, "features_shots", later, (season, season, day)),
                    rows(self.full, "features_shots", later, (season, season, day)),
                )


class GameTeamShotsTest(FeatureTest):
    def one(self, season, game_id, team_id):
        return self.full.execute(
            "SELECT * FROM game_team_shots WHERE season = ? AND game_id = ? AND team_id = ?",
            (season, game_id, team_id),
        ).fetchone()

    def test_team_id_is_normalised(self):
        self.assertEqual(team_shots.team_number("933686567:saipa"), "933686567")
        # shot_events carries the bare number; game_team_shots the full id.
        bare = self.full.execute(
            "SELECT COUNT(*) FROM hockey.shot_events WHERE season = 2027 AND game_id = 2701363 "
            "AND shooting_team_id = '933686567'"
        ).fetchone()[0]
        self.assertEqual(bare, 44)
        self.assertEqual(self.one(2027, 2701363, "933686567:saipa")["corsi_for"], 44)

    def test_corsi_counts_all_four_result_types(self):
        by_type = dict(self.full.execute(
            "SELECT event_type, COUNT(*) FROM hockey.shot_events WHERE season = 2027 AND game_id = 2701363 "
            "AND shooting_team_id = '933686567' GROUP BY event_type"
        ).fetchall())
        self.assertEqual(by_type, {"GOAL": 1, "GOALIE_BLOCKED": 18, "MISSED": 14, "PLAYER_BLOCKED": 11})
        row = self.one(2027, 2701363, "933686567:saipa")
        self.assertEqual(row["corsi_for"], sum(by_type.values()))
        self.assertEqual(row["ev_corsi_for"], self.full.execute(
            "SELECT COUNT(*) FROM hockey.shot_events WHERE season = 2027 AND game_id = 2701363 "
            "AND shooting_team_id = '933686567' AND strength_type = 'EvenStrengthShot'"
        ).fetchone()[0])

    def test_for_and_against_mirror_each_other(self):
        asymmetric = self.full.execute(
            "SELECT COUNT(*) FROM game_team_shots a JOIN game_team_shots b "
            "ON b.season = a.season AND b.game_id = a.game_id AND b.team_id = a.opponent_id "
            "WHERE a.corsi_for IS NOT b.corsi_against OR a.ev_corsi_for IS NOT b.ev_corsi_against "
            "OR a.xg_for IS NOT b.xg_against"
        ).fetchone()[0]
        self.assertEqual(asymmetric, 0)

    def test_gap_game_is_missing_not_zero(self):
        # 2025:393 KalPa-Sport is in the 2025 shot_events gap; its xG exists.
        for team in ("859884935:kalpa", "626537494:sport"):
            row = self.one(2025, 393, team)
            self.assertEqual(
                (row["corsi_for"], row["corsi_against"], row["ev_corsi_for"], row["ev_corsi_against"]),
                (None, None, None, None),
            )
            self.assertIsNotNone(row["xg_for"])


class ShotFeatureTest(FeatureTest):
    def features(self, season, game_id, role, columns="*"):
        return self.full.execute(
            f"SELECT {columns} FROM features_shots WHERE season = ? AND game_id = ? AND team_role = ?",
            (season, game_id, role),
        ).fetchone()

    def test_gap_game_is_skipped_in_windows(self):
        # KalPa's game after 393 (2025:407, also in the gap): the Corsi
        # windows are unchanged, the xG windows (full at 10) move on by 393.
        at_393 = self.features(2025, 393, "home")
        at_407 = self.features(2025, 407, "home")
        self.assertEqual(at_393["team_id"], at_407["team_id"])
        self.assertEqual(
            tuple(self.features(2025, 393, "home", CORSI_COLUMNS)),
            tuple(self.features(2025, 407, "home", CORSI_COLUMNS)),
        )
        self.assertEqual((at_393["xg_n_10"], at_407["xg_n_10"]), (10, 10))
        self.assertNotEqual(
            (at_393["xg_for_10"], at_393["xg_against_10"]), (at_407["xg_for_10"], at_407["xg_against_10"])
        )

    def test_windows_match_a_direct_count(self):
        season, game_id = 2027, 2701437
        for role in ("home", "away"):
            with self.subTest(role=role):
                row = self.features(season, game_id, role)
                for n in (10, 25, 40):
                    games = self.full.execute(
                        "SELECT p.corsi_for, p.corsi_against, p.ev_corsi_for, p.ev_corsi_against "
                        "FROM game_team_shots p JOIN game_spine s USING (game_id, season) "
                        "WHERE p.team_id = ? AND s.feeds_ratings = 1 AND s.ended = 1 AND s.season >= ? "
                        "AND s.start_date < ? AND p.corsi_for IS NOT NULL ORDER BY s.start_utc DESC LIMIT ?",
                        (row["team_id"], season - 1, _start(self.full, season, game_id, "start_date"), n),
                    ).fetchall()
                    cf, ca = sum(g[0] for g in games), sum(g[1] for g in games)
                    self.assertEqual(row[f"corsi_n_{n}"], len(games))
                    self.assertAlmostEqual(row[f"corsi_for_{n}"], cf / len(games))
                    self.assertAlmostEqual(row[f"corsi_against_{n}"], ca / len(games))
                    self.assertAlmostEqual(row[f"corsi_share_{n}"], cf / (cf + ca))
                    ev_cf, ev_ca = sum(g[2] for g in games), sum(g[3] for g in games)
                    self.assertAlmostEqual(row[f"ev_corsi_share_{n}"], ev_cf / (ev_cf + ev_ca))
                # Fewer than 40 games of 2027 so far: the 40 window reaches
                # 2026, which the fixture does not hold.
                self.assertEqual(row["corsi_n_40"], row["corsi_n_season"])

    def test_no_history_is_null_not_zero(self):
        # 2022 game 1 is the first competitive game in the fixture.
        for role in ("home", "away"):
            row = self.features(2022, 1, role)
            self.assertEqual((row["corsi_n_10"], row["corsi_for_10"], row["corsi_share_10"]), (0, None, None))
            self.assertEqual((row["xg_available"], row["xg_n_10"], row["xg_for_10"]), (0, 0, None))

    def test_xg_is_available_from_game_xg(self):
        # 2022 has game xG but no shot_events in the fixture.
        row = self.features(2022, 100, "home")
        self.assertEqual(row["xg_available"], 1)
        self.assertEqual(row["xg_n_10"], 10)
        self.assertEqual(row["corsi_n_10"], 0)

    def test_history_joins_only_derived_tables(self):
        self.assertIn("game_team_shots", history.PER_GAME_TABLES)
        with self.assertRaises(ValueError):
            history.history_before(self.full, 2025, 302, "COUNT(*)", per_game="hockey.shot_events")


if __name__ == "__main__":
    unittest.main()
