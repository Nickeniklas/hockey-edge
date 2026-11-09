"""F3b: special teams (Family 3) on resolved penalty teams.

Runs on the real rows in fixtures/features/mini.sql: penalty and goal
events for the ended competitive games of 2027 and of 2025 from 2025-01-22
to 2025-02-23. Other games of 2022/2025 have no penalty rows in the
fixture, so they look like games without penalty data (NULL, skipped).

Run: python -m unittest discover -s tests
"""

import unittest

from hockey_edge.features import history, special_teams

from .support import UNPLAYED, FeatureTest, rows

KESPOO, KALPA = "1368624751:k-espoo", "859884935:kalpa"
TARGETS = ((2025, 412), (2027, 2701363), (2027, 2701437))

# From the target's date on: every real goal a power-play goal and every
# penalty a five-minute major.
_FROM_DAY = (
    "(game_id, season) IN (SELECT game_id, season FROM games "
    "WHERE ended = 1 AND substr(start_utc, 1, 10) >= ?)"
)
GOAL_POISON = f"UPDATE game_goal_events SET goal_types = '[\"YV\"]' WHERE {_FROM_DAY}"
PENALTY_POISON = (
    "UPDATE game_penalty_events SET penalty_minutes = 5, penalty_end_time = penalty_begin_time + 300 "
    f"WHERE {_FROM_DAY}"
)
COUNTS = "pp_opportunities, pk_opportunities, pp_goals, pp_goals_against, penalties_taken, penalties_drawn"


def _start(conn, season, game_id, column="start_utc"):
    return conn.execute(
        f"SELECT {column} FROM game_spine WHERE season = ? AND game_id = ?", (season, game_id)
    ).fetchone()[0]


def counts(conn, season, game_id, team_id):
    return tuple(conn.execute(
        f"SELECT {COUNTS} FROM game_team_special_teams WHERE season = ? AND game_id = ? AND team_id = ?",
        (season, game_id, team_id),
    ).fetchone())


class GameCountsTest(FeatureTest):
    def test_coincident_penalties_give_no_power_play(self):
        # 2027:2701275 K-Espoo-KalPa. K-Espoo took six counted penalties
        # (one a bench minor), KalPa four plus a 0-minute penalty-shot award.
        # Two pairs start at the same second (581, 2116) and cancel, leaving
        # KalPa four power plays and K-Espoo two. K-Espoo's only labelled
        # power-play goal (3419) ends KalPa's last minor.
        self.assertEqual(counts(self.full, 2027, 2701275, KESPOO), (2, 4, 1, 0, 6, 4))
        self.assertEqual(counts(self.full, 2027, 2701275, KALPA), (4, 2, 0, 1, 4, 6))

    def test_a_missed_penalty_shot_is_not_a_goal(self):
        # 2027:2701276 Kärpät-Ässät: Ässät's RL0 at 3036 (a missed penalty
        # shot after a 0-minute award) is neither a goal nor a penalty.
        # One pair cancels at 334.
        home, away = self.full.execute(
            "SELECT home_team_id, away_team_id FROM game_spine WHERE season = 2027 AND game_id = 2701276"
        ).fetchone()
        self.assertEqual(counts(self.full, 2027, 2701276, home), (7, 4, 1, 0, 5, 8))
        self.assertEqual(counts(self.full, 2027, 2701276, away), (4, 7, 0, 1, 8, 5))

    def test_sides_mirror(self):
        bad = self.full.execute("""
            SELECT COUNT(*) FROM game_team_special_teams a JOIN game_team_special_teams b
            ON a.season = b.season AND a.game_id = b.game_id AND a.team_id = b.opponent_id
            WHERE a.pp_opportunities <> b.pk_opportunities OR a.pp_goals <> b.pp_goals_against
               OR a.penalties_taken <> b.penalties_drawn
        """).fetchone()[0]
        self.assertEqual(bad, 0)

    def test_game_without_penalty_rows_is_missing(self):
        # 2025 before 2025-01-22 has no penalty rows in the fixture.
        row = self.full.execute(
            f"SELECT {COUNTS} FROM game_team_special_teams t JOIN game_spine s USING (game_id, season) "
            "WHERE s.season = 2025 AND s.start_utc < '2025-01-01' LIMIT 1"
        ).fetchone()
        self.assertEqual(tuple(row), (None,) * 6)

    def test_only_listed_per_game_tables(self):
        with self.assertRaises(ValueError):
            history.history_before(self.full, 2025, 302, "COUNT(*)", per_game="hockey.game_penalty_events")
        self.assertGreater(
            history.history_before(
                self.full, 2027, 2701437, "COUNT(*) AS n", per_game="game_team_special_teams"
            )[0]["n"], 100,
        )


class WindowTest(FeatureTest):
    def history(self, season, game_id, team_id):
        """The team's earlier rated games with penalty data, newest first."""
        return self.full.execute("""
            SELECT t.* FROM game_team_special_teams t JOIN game_spine h USING (game_id, season)
            WHERE t.team_id = ? AND h.feeds_ratings = 1 AND t.pp_opportunities IS NOT NULL
              AND h.season >= ? - 1 AND h.start_date < (
                  SELECT start_date FROM game_spine WHERE season = ? AND game_id = ?)
            ORDER BY h.start_utc DESC
        """, (team_id, season, season, game_id)).fetchall()

    def test_windows_sum_the_most_recent_games(self):
        for season, game_id in TARGETS[1:]:
            for f in self.full.execute(
                "SELECT * FROM features_special_teams WHERE season = ? AND game_id = ?", (season, game_id)
            ):
                past = self.history(season, game_id, f["team_id"])
                for n in special_teams.WINDOWS:
                    with self.subTest(game=(season, game_id), team=f["team_id"], n=n):
                        w = past[:n]
                        self.assertEqual(f[f"st_n_{n}"], len(w))
                        self.assertEqual(f[f"pp_opp_{n}"], sum(r["pp_opportunities"] for r in w))
                        self.assertEqual(f[f"pp_goals_{n}"], sum(r["pp_goals"] for r in w))
                        self.assertEqual(f[f"pk_opp_{n}"], sum(r["pk_opportunities"] for r in w))
                        self.assertEqual(f[f"pk_ga_{n}"], sum(r["pp_goals_against"] for r in w))
                        self.assertAlmostEqual(
                            f[f"penalties_taken_{n}"], sum(r["penalties_taken"] for r in w) / len(w)
                        )

    def test_rates_are_shrunk_toward_the_league(self):
        k = special_teams.PP_K
        for f in self.full.execute("SELECT * FROM features_special_teams WHERE league_pp_pct IS NOT NULL"):
            league = f["league_pp_pct"]
            for n in special_teams.WINDOWS:
                self.assertAlmostEqual(f[f"pp_pct_{n}"], (f[f"pp_goals_{n}"] + k * league) / (f[f"pp_opp_{n}"] + k))
                self.assertAlmostEqual(f[f"pk_pct_{n}"], 1 - (f[f"pk_ga_{n}"] + k * league) / (f[f"pk_opp_{n}"] + k))

    def test_league_rate_counts_only_earlier_days(self):
        f = self.full.execute(
            "SELECT league_pp_pct FROM features_special_teams WHERE season = 2027 AND game_id = 2701437"
        ).fetchone()[0]
        goals, opps = self.full.execute("""
            SELECT SUM(t.pp_goals), SUM(t.pp_opportunities) FROM game_team_special_teams t
            JOIN game_spine h USING (game_id, season)
            WHERE h.feeds_ratings = 1 AND h.season >= 2026 AND t.pp_opportunities IS NOT NULL
              AND h.start_date < (SELECT start_date FROM game_spine WHERE season = 2027 AND game_id = 2701437)
        """).fetchone()
        self.assertAlmostEqual(f, goals / opps)


class SpecialTeamsPointInTimeTest(FeatureTest):
    def test_truncation(self):
        for season, game_id in TARGETS:
            with self.subTest(game=(season, game_id)):
                start = _start(self.full, season, game_id)
                hockey = self.edited_hockey(
                    f"sttrunc{season}_{game_id}",
                    ("DELETE FROM games WHERE start_utc >= ? AND NOT (season = ? AND game_id = ?)",
                     (start, season, game_id)),
                    (f"UPDATE games SET {UNPLAYED} WHERE season = ? AND game_id = ?", (season, game_id)),
                )
                truncated = self.build_features(f"sttrunc{season}_{game_id}", hockey)
                where = ("season = ? AND game_id = ?", (season, game_id))
                expected = rows(self.full, "features_special_teams", *where)
                self.assertEqual(len(expected), 2)
                self.assertEqual(rows(truncated, "features_special_teams", *where), expected)

    def test_poison(self):
        for season, game_id in TARGETS:
            with self.subTest(game=(season, game_id)):
                day = _start(self.full, season, game_id, "start_date")
                hockey = self.edited_hockey(
                    f"stpoison{season}_{game_id}", (GOAL_POISON, (day,)), (PENALTY_POISON, (day,))
                )
                poisoned = self.build_features(f"stpoison{season}_{game_id}", hockey)
                where = ("season = ? AND game_id = ?", (season, game_id))
                self.assertEqual(
                    rows(poisoned, "features_special_teams", *where), rows(self.full, "features_special_teams", *where)
                )
                later = "season = ? AND game_id IN (SELECT game_id FROM game_spine WHERE season = ? AND start_date > ?)"
                self.assertNotEqual(
                    rows(poisoned, "features_special_teams", later, (season, season, day)),
                    rows(self.full, "features_special_teams", later, (season, season, day)),
                )

    def test_gap_game_is_skipped_not_zero(self):
        # 2027:2701275 with its penalty events gone: missing, so K-Espoo's
        # next game's windows hold one game fewer instead of a 0-for-0 game.
        hockey = self.edited_hockey(
            "stgap", ("DELETE FROM game_penalty_events WHERE season = 2027 AND game_id = 2701275", ())
        )
        gapped = self.build_features("stgap", hockey)
        self.assertEqual(counts(gapped, 2027, 2701275, KESPOO), (None,) * 6)
        nxt = self.full.execute("""
            SELECT s.game_id, CASE WHEN s.home_team_id = :t THEN 'home' ELSE 'away' END AS role
            FROM game_spine s WHERE s.season = 2027 AND :t IN (s.home_team_id, s.away_team_id)
              AND s.start_date > (SELECT start_date FROM game_spine WHERE season = 2027 AND game_id = 2701275)
            ORDER BY s.start_utc LIMIT 1
        """, {"t": KESPOO}).fetchone()
        where = "season = 2027 AND game_id = ? AND team_role = ?"
        full = self.full.execute(f"SELECT * FROM features_special_teams WHERE {where}", tuple(nxt)).fetchone()
        gap = gapped.execute(f"SELECT * FROM features_special_teams WHERE {where}", tuple(nxt)).fetchone()
        self.assertEqual(gap["st_n_season"], full["st_n_season"] - 1)
        past = [
            r for r in WindowTest.history(self, 2027, nxt["game_id"], KESPOO)
            if r["game_id"] != 2701275
        ]
        for n in special_teams.WINDOWS:
            self.assertEqual(gap[f"st_n_{n}"], len(past[:n]))
            self.assertEqual(gap[f"pp_opp_{n}"], sum(r["pp_opportunities"] for r in past[:n]))


if __name__ == "__main__":
    unittest.main()
