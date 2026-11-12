"""F2a: actual starters, the resolver, and goalie features (Family 1).

Runs on the real rows in fixtures/features/mini.sql: goalie stats and
goalkeeper events for 2025 and 2027, and every 2027 lineup snapshot.

Run: python -m unittest discover -s tests
"""

import importlib.util
import sqlite3
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from hockey_edge.features import goalie, goalie_report, history, starters

from .support import UNPLAYED, FeatureTest, rows

REPO = Path(__file__).resolve().parents[2]

TARGETS = ((2025, 302), (2025, 52409), (2027, 2701363))
HISTORY_ONLY = "team_role, league_sv, expected_starts_n, expected_sv"
STARTER_QUALITY = ("shots_short", "sv_short", "shots_long", "sv_long", "starter_sv", "gsax_n", "gsax_per_game")

# Every played game's goalies concede every shot from the target's date on.
GOALIE_POISON = (
    "UPDATE game_goalie_period_stats SET goals_allowed = shots_on_goal, saves = 0 "
    "WHERE (game_id, season) IN (SELECT game_id, season FROM games "
    "WHERE ended = 1 AND substr(start_utc, 1, 10) >= ?)"
)

# 2027:2701312 away (TPS): the snapshot named Ruusu, Rämö started.
SNAPSHOT_MISS = (2027, 2701312, "away", 29857823, 30117507)
# 2027:2701274 was played 2026-09-01, before the job captured lineups.
NO_SNAPSHOT_ENDED = (2027, 2701274)
# 2027:2701589 (HIFK-Tappara, 2027-01-02) is unplayed in the fixture and
# has no snapshot.
UNPLAYED_NO_SNAPSHOT = (2027, 2701589)


def _game(conn, season, game_id):
    return conn.execute(
        "SELECT * FROM game_spine WHERE season = ? AND game_id = ?", (season, game_id)
    ).fetchone()


def _goalie_rows(conn, season, game_id):
    return {
        r["team_role"]: r for r in conn.execute(
            "SELECT * FROM features_goalie WHERE season = ? AND game_id = ?", (season, game_id)
        )
    }


class GoaliePointInTimeTest(FeatureTest):
    def assert_same_history(self, other, season, game_id):
        where = ("season = ? AND game_id = ?", (season, game_id))
        self.assertEqual(
            [tuple(r) for r in other.execute(f"SELECT {HISTORY_ONLY} FROM features_goalie WHERE {where[0]} ORDER BY 1", where[1])],
            [tuple(r) for r in self.full.execute(f"SELECT {HISTORY_ONLY} FROM features_goalie WHERE {where[0]} ORDER BY 1", where[1])],
        )
        # The full build's starter, rated from the other build's history.
        g, history_other = _game(other, season, game_id), goalie.GoalieHistory(other)
        for role, row in _goalie_rows(self.full, season, game_id).items():
            with self.subTest(role=role):
                quality = history_other.goalie(g, row["starter_player_id"])
                self.assertEqual({c: quality[c] for c in STARTER_QUALITY}, {c: row[c] for c in STARTER_QUALITY})

    def test_truncation(self):
        for season, game_id in TARGETS:
            with self.subTest(game=(season, game_id)):
                start = _game(self.full, season, game_id)["start_utc"]
                hockey = self.edited_hockey(
                    f"gtrunc{season}_{game_id}",
                    ("DELETE FROM games WHERE start_utc >= ? AND NOT (season = ? AND game_id = ?)",
                     (start, season, game_id)),
                    (f"UPDATE games SET {UNPLAYED} WHERE season = ? AND game_id = ?", (season, game_id)),
                )
                truncated = self.build_features(f"gtrunc{season}_{game_id}", hockey)
                self.assert_same_history(truncated, season, game_id)

    def test_truncated_target_without_snapshot_uses_the_fallback(self):
        # The live path: an unplayed game with no snapshot.
        season, game_id = TARGETS[0]
        start = _game(self.full, season, game_id)["start_utc"]
        hockey = self.edited_hockey(
            "gtrunc_live",
            ("DELETE FROM games WHERE start_utc >= ? AND NOT (season = ? AND game_id = ?)",
             (start, season, game_id)),
            (f"UPDATE games SET {UNPLAYED} WHERE season = ? AND game_id = ?", (season, game_id)),
        )
        truncated = self.build_features("gtrunc_live", hockey)
        for role, row in _goalie_rows(truncated, season, game_id).items():
            with self.subTest(role=role):
                self.assertEqual(row["starter_source"], starters.UNKNOWN)
                self.assertIsNone(row["starter_player_id"])
                self.assertEqual(row["goalie_sv"], row["expected_sv"])
                self.assertEqual(row["expected_sv"], _goalie_rows(self.full, season, game_id)[role]["expected_sv"])

    def test_poison(self):
        for season, game_id in TARGETS:
            with self.subTest(game=(season, game_id)):
                day = _game(self.full, season, game_id)["start_date"]
                hockey = self.edited_hockey(f"gpoison{season}_{game_id}", (GOALIE_POISON, (day,)))
                poisoned = self.build_features(f"gpoison{season}_{game_id}", hockey)
                self.assertEqual(
                    rows(poisoned, "features_goalie", "season = ? AND game_id = ?", (season, game_id)),
                    rows(self.full, "features_goalie", "season = ? AND game_id = ?", (season, game_id)),
                )
                # The poison is real: later games in the season do change.
                later = "season = ? AND game_id IN (SELECT game_id FROM game_spine WHERE season = ? AND start_date > ?)"
                self.assertNotEqual(
                    rows(poisoned, "features_goalie", later, (season, season, day)),
                    rows(self.full, "features_goalie", later, (season, season, day)),
                )

    def test_history_joins_only_derived_tables(self):
        with self.assertRaises(ValueError):
            history.history_before(self.full, 2025, 302, "COUNT(*)", per_game="hockey.games")


class ResolverTest(FeatureTest):
    def test_snapshot_beats_the_actual_starter(self):
        season, game_id, role, inferred, actual = SNAPSHOT_MISS
        self.assertEqual(
            starters.resolve_starter(self.full, season, game_id, role), (inferred, starters.SNAPSHOT_INFERRED)
        )
        row = self.full.execute(
            "SELECT actual_player_id, actual_reason FROM game_starters "
            "WHERE season = ? AND game_id = ? AND team_role = ?", (season, game_id, role),
        ).fetchone()
        self.assertEqual(tuple(row), (actual, "ok"))

    def test_actual_starter_without_a_snapshot(self):
        season, game_id = NO_SNAPSHOT_ENDED
        for role in ("home", "away"):
            with self.subTest(role=role):
                row = self.full.execute(
                    "SELECT * FROM game_starters WHERE season = ? AND game_id = ? AND team_role = ?",
                    (season, game_id, role),
                ).fetchone()
                self.assertIsNone(row["snapshot_player_id"])
                self.assertIsNotNone(row["actual_player_id"])
                self.assertEqual(
                    starters.resolve_starter(self.full, season, game_id, role),
                    (row["actual_player_id"], starters.ACTUAL_POSTGAME),
                )

    def test_unplayed_without_a_snapshot_is_unknown(self):
        season, game_id = UNPLAYED_NO_SNAPSHOT
        for role, row in _goalie_rows(self.full, season, game_id).items():
            with self.subTest(role=role):
                self.assertEqual(starters.resolve_starter(self.full, season, game_id, role), (None, starters.UNKNOWN))
                self.assertIsNone(row["starter_sv"])
                self.assertEqual(row["goalie_sv"], row["expected_sv"])
                self.assertEqual(row["expected_starts_n"], goalie.EXPECTED_GAMES)

    def test_snapshot_captured_after_the_start_is_ignored(self):
        season, game_id, role, _, actual = SNAPSHOT_MISS
        start = datetime.fromisoformat(_game(self.full, season, game_id)["start_utc"].replace("Z", "+00:00"))
        snapshots = self.edited_snapshots("late", (
            "UPDATE lineup_snapshots SET captured_at = ? WHERE season = ? AND game_id = ?",
            ((start + timedelta(seconds=1)).isoformat(), season, game_id),
        ))
        late = self.build_features("late", snapshots=snapshots)
        self.assertEqual(starters.resolve_starter(late, season, game_id, role), (actual, starters.ACTUAL_POSTGAME))

    def test_goalie_sv_follows_the_source(self):
        mismatched = self.full.execute(
            "SELECT COUNT(*) FROM features_goalie WHERE NOT ("
            "(starter_source = 'unknown' AND goalie_sv IS expected_sv) OR "
            "(starter_source <> 'unknown' AND goalie_sv IS starter_sv))"
        ).fetchone()[0]
        self.assertEqual(mismatched, 0)


class GoalieFeatureTest(FeatureTest):
    def test_league_sv(self):
        # Earlier rated games of 2023-2025 (the fixture has goalie stats for 2025 only).
        saves, shots = self.full.execute(
            "SELECT SUM(p.shots_on_goal - p.goals_allowed), SUM(p.shots_on_goal) "
            "FROM hockey.game_goalie_period_stats p JOIN game_spine s USING (game_id, season) "
            "WHERE s.feeds_ratings = 1 AND s.ended = 1 AND s.season BETWEEN 2023 AND 2025 "
            "AND s.start_date < '2025-01-04'"
        ).fetchone()
        for row in _goalie_rows(self.full, 2025, 302).values():
            self.assertAlmostEqual(row["league_sv"], saves / shots)

    def test_long_window_holds_all_shots_when_short_of_it(self):
        for season, game_id in TARGETS:
            for role, row in _goalie_rows(self.full, season, game_id).items():
                with self.subTest(game=(season, game_id), role=role):
                    total = self.full.execute(
                        "SELECT COALESCE(SUM(p.shots_on_goal), 0) "
                        "FROM hockey.game_goalie_period_stats p JOIN game_spine s USING (game_id, season) "
                        "WHERE s.feeds_ratings = 1 AND s.ended = 1 AND p.player_id = ? "
                        "AND s.season >= ? AND s.start_date < ?",
                        (row["starter_player_id"], season - goalie.GOALIE_SEASONS + 1,
                         _game(self.full, season, game_id)["start_date"]),
                    ).fetchone()[0]
                    self.assertLessEqual(row["shots_short"], row["shots_long"])
                    if total < goalie.LONG_SHOTS:
                        self.assertEqual(row["shots_long"], total)
                    else:
                        self.assertGreaterEqual(row["shots_long"], goalie.LONG_SHOTS)

    def test_starter_sv_is_shrunk_toward_the_league(self):
        for season, game_id in TARGETS:
            for row in _goalie_rows(self.full, season, game_id).values():
                saves = row["sv_long"] * row["shots_long"]
                self.assertAlmostEqual(
                    row["starter_sv"],
                    (saves + goalie.SV_K * row["league_sv"]) / (row["shots_long"] + goalie.SV_K),
                )

    def test_expected_goalie_fallback(self):
        # Rebuilt from game_starters, not from game_goalies.
        season, game_id = UNPLAYED_NO_SNAPSHOT
        g = _game(self.full, season, game_id)
        goalies = goalie.GoalieHistory(self.full)
        for role, row in _goalie_rows(self.full, season, game_id).items():
            with self.subTest(role=role):
                starts = [r[0] for r in self.full.execute(
                    "SELECT st.actual_player_id FROM game_starters st JOIN game_spine s USING (game_id, season) "
                    "WHERE st.team_id = ? AND st.actual_player_id IS NOT NULL AND s.feeds_ratings = 1 "
                    "AND s.season >= ? AND s.start_date < ? ORDER BY s.start_utc DESC LIMIT ?",
                    (row["team_id"], season - 1, g["start_date"], goalie.EXPECTED_GAMES),
                )]
                self.assertEqual(len(starts), row["expected_starts_n"])
                weighted = [goalie.starter_sv_at(goalies, g, pid) for pid in starts]
                self.assertAlmostEqual(row["expected_sv"], sum(weighted) / len(weighted))

    def test_whole_game_and_opponent_xg(self):
        # 2025:302 Tappara-KalPa: one goalie a side faced every shot.
        goalies = {
            r["player_id"]: r for r in self.full.execute(
                "SELECT * FROM game_goalies WHERE season = 2025 AND game_id = 302"
            )
        }
        self.assertEqual({p: r["whole_game"] for p, r in goalies.items()},
                         {32935444: 1, 22558763: 0, 60760868: 1, 34264401: 0})
        self.assertEqual(goalies[32935444]["opponent_xg"], 2.32)  # KalPa's xG, Tappara's goalie
        self.assertEqual(goalies[60760868]["opponent_xg"], 1.59)
        self.assertEqual((goalies[32935444]["shots_against"], goalies[32935444]["goals_against"]), (16, 4))


class StarterLogicTest(FeatureTest):
    def test_verify_starters_output_is_unchanged(self):
        # Pinned from `python scripts/verify_starters.py` on the real
        # databases, 2026-11-06, before the logic moved to starters.py;
        # re-pinned 2026-11-12 with the 2027 games to 2026-11-11 (14 more
        # team-games: 13 agree, 2701441 home has two period-1 goalies).
        spec = importlib.util.spec_from_file_location("verify_starters", REPO / "scripts" / "verify_starters.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        snap, hockey = script._readonly_connection(self.snapshots), script._readonly_connection(self.hockey)
        self.addCleanup(snap.close)
        self.addCleanup(hockey.close)
        agree, disagree, undetermined = 0, [], []
        captured = script._latest_captured_starters(snap, season=None)
        for row in captured:
            team_id = script._team_id_for_role(hockey, row["season"], row["game_id"], row["team_role"])
            actual, reason = script.derive_actual_starter(hockey, row["season"], row["game_id"], team_id)
            if actual is None:
                undetermined.append((row["game_id"], row["team_role"], reason))
            elif actual == row["starter_player_id"]:
                agree += 1
            else:
                disagree.append((row["game_id"], row["team_role"], actual))
        self.assertEqual(len(captured), 268)
        self.assertEqual(agree, 262)
        self.assertEqual(disagree, [(2701312, "away", 30117507)])
        self.assertEqual(sorted(undetermined), [
            (2701322, "home", "ambiguous_period1_stats (2 candidates)"),
            (2701331, "home", "ambiguous_period1_stats (0 candidates)"),
            (2701341, "away", "ambiguous_period1_stats (2 candidates)"),
            (2701432, "home", "ambiguous_period1_stats (2 candidates)"),
            (2701441, "home", "ambiguous_period1_stats (2 candidates)"),
        ])

    def derive_with_event(self, name, player_id, begin_time):
        """2025:302 KalPa with one extra period-1 goalkeeper event."""
        hockey = self.edited_hockey(name, (
            "INSERT INTO game_goalkeeper_events (game_id, season, team_id, event_id, player_id, period, "
            "game_time_seconds, begin_time, end_time, empty_net) "
            "VALUES (302, 2025, '859884935:kalpa', 9001, ?, 1, ?, ?, 1200, 0)",
            (player_id, begin_time, begin_time),
        ))
        conn = sqlite3.connect(hockey)
        self.addCleanup(conn.close)
        return starters.derive_actual_starter(conn, 2025, 302, "859884935:kalpa")

    def test_opening_assignment_is_not_an_early_change(self):
        # 2015-2024 log the starter's own opening row at begin_time 0.
        self.assertEqual(self.derive_with_event("opening", 60760868, 0), (60760868, "ok"))

    def test_other_goalie_early_is_flagged(self):
        self.assertEqual(self.derive_with_event("early", 34264401, 120), (None, "flagged_early_change"))

    def test_skew_report_on_2027(self):
        skew = goalie_report.skew_report(self.full)
        self.assertEqual(skew["with_snapshot"], 268)
        self.assertEqual(skew["both"], 263)
        self.assertEqual(skew["snapshot_but_actual_undetermined"], 5)
        season, game_id, role, inferred, actual = SNAPSHOT_MISS
        self.assertEqual(
            [(m["game_id"], m["team_role"], m["inferred"], m["actual"]) for m in skew["mismatches"]],
            [(game_id, role, inferred, actual)],
        )


if __name__ == "__main__":
    unittest.main()
