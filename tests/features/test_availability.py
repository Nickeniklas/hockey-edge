"""F3c: skater ice time, the lineup resolver and roster availability (Family 6).

Runs on the real rows in fixtures/features/mini.sql: skater period stats
(keys and ice time) and their roster rows for the ended competitive games
of 2027 and for Pelicans', Jukurit's and Jokerit's 2025 games from
2025-02-01 (the run-in, then the ice-time gap of the PLAYOUT and
QUALIFICATIONS series), and every 2027 lineup snapshot with its roster.
2022 and the rest of 2025 have no skater stats in the fixture, so they
resolve to 'unknown' with no fallback.

Run: python -m unittest discover -s tests
"""

import shutil
import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest import mock

from hockey_edge.features import availability, availability_report, history, lineups, pregame

from .support import UNPLAYED, FeatureTest, rows

LUKKO, ASSAT, PELICANS = "624554857:lukko", "679171680:ässät", "875886777:pelicans"

# 2027:2701306 Lukko (away at HIFK, 2026-09-17), snapshot lineup. Jami
# Krannila, a forward, is not in it; he played 626 + 770 + 616 = 2,012 s in
# Lukko's three earlier games (2701277, 2701289, 2701295), whose skaters
# total 18,009 + 17,415 + 17,650 = 53,074 s, forwards 10,992 + 10,427 +
# 10,656 = 32,075 s. Every other window skater dressed.
ABSENCE = (2027, 2701306, LUKKO)
KRANNILA = 32194874
# 2027 player 40032907, a defenceman: Jukurit, then two games on loan to
# Ässät (2701351: 571 s, 2701355: 457 s), then back to Jukurit, for whom he
# dressed against Ässät in 2701361.
LOANED, LOAN_GAME = 40032907, (2027, 2701361)
# 2025 PLAYOUT, Pelicans-Jukurit: no skater ice time in any of the five games.
GAP_GAMES = (51169, 51170, 51171, 51172, 51174)
# Unplayed in the fixture, no snapshot.
UNPLAYED_NO_SNAPSHOT = (2027, 2701477)

# Snapshot, actual (the 2026-10-09 capture gap), actual in a gap series.
TARGETS = ((2027, 2701306), (2027, 2701368), (2025, 51170))
WINDOW_COLUMNS = ("n_window_games_5", "n_window_games_10", "missing_share_10_fallback")
VALUE_COLUMNS = (
    "n_window_games_5", "missing_share_5", "missing_share_f_5", "missing_share_d_5",
    "n_window_games_10", "missing_share_10", "missing_share_f_10", "missing_share_d_10",
)

# Absurd ice time from the target's date on. Who played is unchanged, so a
# target resolved by its actual lineup keeps it.
TOI_POISON = (
    "UPDATE game_player_period_stats SET time_on_ice_seconds = (player_id % 997) * 7 + 1 "
    "WHERE (game_id, season) IN (SELECT game_id, season FROM games "
    "WHERE ended = 1 AND substr(start_utc, 1, 10) >= ?)"
)


def _game(conn, season, game_id):
    return conn.execute("SELECT * FROM game_spine WHERE season = ? AND game_id = ?", (season, game_id)).fetchone()


def _row(conn, season, game_id, team_id):
    return conn.execute(
        "SELECT * FROM features_availability WHERE season = ? AND game_id = ? AND team_id = ?",
        (season, game_id, team_id),
    ).fetchone()


def window_games(conn, season, game_id, team_id, n):
    """Independent of availability.py: the team's last n earlier-dated
    rated games this season with ice time, newest first."""
    return [r[0] for r in conn.execute("""
        SELECT h.game_id FROM game_spine h
        WHERE h.season = :season AND h.ended = 1 AND h.feeds_ratings = 1
          AND h.start_date < (SELECT start_date FROM game_spine WHERE season = :season AND game_id = :game)
          AND EXISTS (SELECT 1 FROM game_skater_toi t WHERE t.season = h.season AND t.game_id = h.game_id
                      AND t.team_id = :team AND t.toi_s IS NOT NULL)
        ORDER BY h.start_utc DESC LIMIT :n
    """, {"season": season, "game": game_id, "team": team_id, "n": n})]


def expected_shares(conn, season, game_id, team_id, lineup, n):
    games = window_games(conn, season, game_id, team_id, n)
    toi, pos = {}, {}
    for gid in games:
        for pid, p, t in conn.execute(
            "SELECT player_id, pos, toi_s FROM game_skater_toi WHERE season = ? AND game_id = ? AND team_id = ?",
            (season, gid, team_id),
        ):
            toi[pid] = toi.get(pid, 0) + t
            if pos.get(pid) is None:
                pos[pid] = p

    def share(players):
        total = sum(toi[p] for p in players)
        return sum(toi[p] for p in players if p not in lineup) / total if total else None

    if not games:
        return len(games), None, None, None
    return (
        len(games), share(list(toi)),
        share([p for p in toi if pos[p] == "F"]), share([p for p in toi if pos[p] == "D"]),
    )


class SkaterToiTest(FeatureTest):
    def test_toi_is_summed_over_periods(self):
        season, game_id, team_id = ABSENCE
        for gid, toi in ((2701277, 626), (2701289, 770), (2701295, 616)):
            self.assertEqual(
                self.full.execute(
                    "SELECT pos, toi_s FROM game_skater_toi WHERE season = 2027 AND game_id = ? "
                    "AND team_id = ? AND player_id = ?", (gid, team_id, KRANNILA),
                ).fetchone()[:], ("F", toi),
            )
        totals = self.full.execute("""
            SELECT t.game_id, SUM(t.toi_s), (
                SELECT SUM(p.time_on_ice_seconds) FROM hockey.game_player_period_stats p
                WHERE p.season = t.season AND p.game_id = t.game_id AND p.team_id = t.team_id)
            FROM game_skater_toi t WHERE t.season = 2027 AND t.team_id = ?
            GROUP BY t.game_id
        """, (team_id,)).fetchall()
        self.assertGreater(len(totals), 10)
        for gid, ours, source in totals:
            self.assertEqual(ours, source, gid)

    def test_one_row_per_skater_with_stats_no_goalies(self):
        goalies = self.full.execute("""
            SELECT COUNT(*) FROM game_skater_toi t JOIN game_goalies g
            ON g.season = t.season AND g.game_id = t.game_id AND g.player_id = t.player_id
        """).fetchone()[0]
        self.assertEqual(goalies, 0)
        per_team = self.full.execute(
            "SELECT MIN(n), MAX(n) FROM (SELECT COUNT(*) AS n FROM game_skater_toi "
            "WHERE season = 2027 GROUP BY game_id, team_id)"
        ).fetchone()
        self.assertEqual(tuple(per_team), (18, 20))

    def test_gap_team_game_keeps_its_rows_without_toi(self):
        for game_id in GAP_GAMES:
            n, with_toi = self.full.execute(
                "SELECT COUNT(*), COUNT(toi_s) FROM game_skater_toi WHERE season = 2025 AND game_id = ? AND team_id = ?",
                (game_id, PELICANS),
            ).fetchone()
            self.assertGreaterEqual(n, 18, game_id)
            self.assertEqual(with_toi, 0, game_id)

    def test_position_from_role(self):
        for role, pos in (
            ("LEFT_DEFENSEMAN", "D"), ("DEFENSEMAN", "D"), ("SEVENTH_DEFENSEMAN", "D"),
            ("EIGHTH_DEFENSEMAN", "D"), ("CENTER", "F"), ("RIGHT_WING", "F"), ("STRIKER", "F"),
            ("THIRTEENTH_STRIKER", "F"), ("GOALIE", None), (None, None), ("13. H", None),
        ):
            self.assertEqual(lineups.position(role), pos, role)
        # role NULL: game_rosters.role_code by its letter; role comes first.
        for role, code, pos in (
            (None, "13. H", "F"), (None, "14. H", "F"), (None, "H", "F"), (None, "KH", "F"),
            (None, "P", "D"), (None, "8. P", "D"), (None, "KP", None), (None, "MV", None),
            (None, None, None), ("LEFT_WING", "P", "F"), ("GOALIE", "H", None),
        ):
            self.assertEqual(lineups.position(role, code), pos, (role, code))
        unmapped = availability_report.unmapped_positions(self.full)
        self.assertEqual((unmapped["roster"], unmapped["no_roster_row"], unmapped["snapshot"]), ([], 0, {}))

    def test_gap_date_nulls_every_team_game_that_day(self):
        # The fixture has no 2016, so name a 2027 day instead: 2026-09-16,
        # when Jukurit's 40032907 played 0 s at HIFK.
        day = "2026-09-16"
        with mock.patch.object(lineups, "GAP_DATES", (day,)):
            gapped = self.build_features("avgapdate")
        on_day = "season = 2027 AND game_id IN (SELECT game_id FROM game_spine WHERE start_date = ?)"
        n, with_toi = gapped.execute(f"SELECT COUNT(*), COUNT(toi_s) FROM game_skater_toi WHERE {on_day}", (day,)).fetchone()
        self.assertGreater(n, 100)
        self.assertEqual(with_toi, 0)
        self.assertEqual(
            self.full.execute(f"SELECT COUNT(toi_s) FROM game_skater_toi WHERE {on_day}", (day,)).fetchone()[0], n
        )
        others = "NOT (" + on_day + ")"
        self.assertEqual(rows(gapped, "game_skater_toi", others, (day,)), rows(self.full, "game_skater_toi", others, (day,)))

    def test_history_accepts_the_table(self):
        n = history.history_before(self.full, 2027, 2701437, "COUNT(*) AS n", per_game="game_skater_toi")[0]["n"]
        self.assertGreater(n, 1000)


class ResolverTest(FeatureTest):
    def test_snapshot_lineup(self):
        season, game_id, team_id = ABSENCE
        lineup, source = lineups.resolve_lineup(self.full, season, game_id, team_id)
        self.assertEqual(source, lineups.SNAPSHOT)
        self.assertEqual(len(lineup), 20)
        self.assertNotIn(KRANNILA, lineup)
        # The confirmed lineup is who played.
        self.assertEqual(set(lineup), set(lineups.actual_lineup(self.full, season, game_id, team_id)))

    def test_snapshot_lineups_match_actual_in_2027(self):
        skew = availability_report.skew_report(self.full)
        self.assertEqual(skew["both"], 254)
        self.assertEqual(skew["mismatches"], [])
        self.assertTrue(all(d["differ"] == 0 for d in skew["value_diffs"].values()))

    def test_actual_without_a_snapshot(self):
        # 2027:2701274 was played 2026-09-01, before lineup capture began.
        g = _game(self.full, 2027, 2701274)
        for role in ("home", "away"):
            lineup, source = lineups.resolve_lineup(self.full, 2027, 2701274, g[f"{role}_team_id"])
            self.assertEqual(source, lineups.ACTUAL)
            self.assertGreaterEqual(len(lineup), 18)

    def test_unplayed_without_a_snapshot_is_unknown(self):
        season, game_id = UNPLAYED_NO_SNAPSHOT
        g = _game(self.full, season, game_id)
        self.assertEqual(lineups.resolve_lineup(self.full, season, game_id, g["home_team_id"]), (None, lineups.UNKNOWN))

    def test_no_stats_is_unknown(self):
        # 2022 has no skater stats in the fixture.
        g = _game(self.full, 2022, 152)
        self.assertEqual(lineups.resolve_lineup(self.full, 2022, 152, g["away_team_id"]), (None, lineups.UNKNOWN))

    def test_starter_and_lineup_read_the_same_snapshot(self):
        checked = 0
        for st in self.full.execute(
            "SELECT * FROM game_starters WHERE season = 2027 AND snapshot_player_id IS NOT NULL"
        ):
            snapshot = pregame.latest_pregame_snapshot(self.full, _game(self.full, 2027, st["game_id"]), st["team_role"])
            self.assertEqual(
                (snapshot["starter_player_id"], snapshot["captured_at"]),
                (st["snapshot_player_id"], st["snapshot_captured_at"]),
            )
            checked += 1
        self.assertEqual(checked, 254)

    def test_snapshot_captured_after_the_start_is_ignored(self):
        season, game_id, team_id = ABSENCE
        start = datetime.fromisoformat(_game(self.full, season, game_id)["start_utc"].replace("Z", "+00:00"))
        snapshots = self.dir / "avlate.snapshots.db"
        shutil.copy(self.snapshots, snapshots)
        conn = sqlite3.connect(snapshots)
        conn.execute(
            "UPDATE lineup_snapshots SET captured_at = ? WHERE season = ? AND game_id = ?",
            ((start + timedelta(minutes=1)).isoformat(), season, game_id),
        )
        conn.commit()
        conn.close()
        late = self.build_features("avlate", snapshots=snapshots)
        lineup, source = lineups.resolve_lineup(late, season, game_id, team_id)
        self.assertEqual(source, lineups.ACTUAL)
        self.assertEqual(lineup, lineups.actual_lineup(self.full, season, game_id, team_id))
        self.assertEqual(_row(late, season, game_id, team_id)["lineup_source"], lineups.ACTUAL)


class AvailabilityTest(FeatureTest):
    def test_absence_pinned_by_hand(self):
        f = _row(self.full, *ABSENCE)
        self.assertEqual(f["lineup_source"], lineups.SNAPSHOT)
        self.assertEqual((f["n_window_games_5"], f["n_window_games_10"]), (3, 3))
        for n in (5, 10):
            self.assertAlmostEqual(f[f"missing_share_{n}"], 2012 / 53074)
            self.assertAlmostEqual(f[f"missing_share_f_{n}"], 2012 / 32075)
            self.assertEqual(f[f"missing_share_d_{n}"], 0.0)

    def test_windows_match_an_independent_count(self):
        checked = 0
        for f in self.full.execute(
            "SELECT * FROM features_availability WHERE season = 2027 AND lineup_source <> 'unknown'"
        ):
            lineup, _ = lineups.resolve_lineup(self.full, f["season"], f["game_id"], f["team_id"])
            for n in availability.WINDOWS:
                expected = expected_shares(self.full, f["season"], f["game_id"], f["team_id"], lineup, n)
                got = (f[f"n_window_games_{n}"], f[f"missing_share_{n}"],
                       f[f"missing_share_f_{n}"], f[f"missing_share_d_{n}"])
                self.assertEqual(got[0], expected[0])
                for a, b in zip(got[1:], expected[1:]):
                    if b is None:
                        self.assertIsNone(a)
                    else:
                        self.assertAlmostEqual(a, b)
            checked += 1
        self.assertEqual(checked, 322)

    def test_first_game_of_the_season_is_null(self):
        # 2701277 is Lukko's 2027 opener.
        f = _row(self.full, 2027, 2701277, LUKKO)
        self.assertEqual([f[c] for c in VALUE_COLUMNS + ("missing_share_10_fallback",)],
                         [0, None, None, None, 0, None, None, None, None])

    def test_loaned_player_counts_only_his_minutes_for_this_team(self):
        season, game_id = LOAN_GAME
        f = _row(self.full, season, game_id, ASSAT)
        lineup, _ = lineups.resolve_lineup(self.full, season, game_id, ASSAT)
        self.assertNotIn(LOANED, lineup)
        self.assertIn(LOANED, lineups.resolve_lineup(self.full, season, game_id, "292293444:jukurit")[0])
        g = _game(self.full, season, game_id)
        window = availability.team_history(self.full, g, ASSAT)["window"][:10]
        self.assertEqual(sum(game[LOANED][1] for game in window if LOANED in game), 571 + 457)
        missing = sum(t for game in window for pid, (_, t) in game.items() if pid not in lineup)
        total = sum(t for game in window for _, t in game.values())
        self.assertAlmostEqual(f["missing_share_10"], missing / total)
        # His Jukurit minutes (672 + 759 + 792 + 0 + 592) would change it.
        self.assertNotAlmostEqual(f["missing_share_10"], (missing + 2815) / (total + 2815))

    def test_gap_game_is_skipped_as_history_but_featured(self):
        f = _row(self.full, 2025, 51170, PELICANS)
        self.assertEqual(f["lineup_source"], lineups.ACTUAL)
        self.assertEqual(f["n_window_games_10"], 10)
        self.assertIsNotNone(f["missing_share_10"])
        previous = self.full.execute("""
            SELECT game_id FROM game_spine WHERE season = 2025 AND ended = 1 AND start_utc < ?
              AND ? IN (home_team_id, away_team_id) ORDER BY start_utc DESC LIMIT 1
        """, (_game(self.full, 2025, 51170)["start_utc"], PELICANS)).fetchone()[0]
        self.assertEqual(previous, 51169)
        window = window_games(self.full, 2025, 51170, PELICANS, 10)
        self.assertNotIn(51169, window)
        lineup, _ = lineups.resolve_lineup(self.full, 2025, 51170, PELICANS)
        self.assertAlmostEqual(f["missing_share_10"], expected_shares(self.full, 2025, 51170, PELICANS, lineup, 10)[1])
        # The gap game still gives a lineup: the next game's fallback.
        fallback = availability.team_history(self.full, _game(self.full, 2025, 51171), PELICANS)["fallback"]
        self.assertEqual(fallback, lineups.actual_lineup(self.full, 2025, 51170, PELICANS))

    def test_unknown_uses_the_last_known_lineup(self):
        season, game_id = UNPLAYED_NO_SNAPSHOT
        g = _game(self.full, season, game_id)
        for role in ("home", "away"):
            f = _row(self.full, season, game_id, g[f"{role}_team_id"])
            self.assertEqual(f["lineup_source"], lineups.UNKNOWN)
            self.assertIsNotNone(f["missing_share_10"])
            self.assertEqual(f["missing_share_10"], f["missing_share_10_fallback"])
            last = self.full.execute("""
                SELECT game_id FROM game_spine WHERE season = ? AND ended = 1 AND start_date < ?
                  AND ? IN (home_team_id, away_team_id) ORDER BY start_utc DESC LIMIT 1
            """, (season, g["start_date"], f["team_id"])).fetchone()[0]
            self.assertEqual(
                availability.team_history(self.full, g, f["team_id"])["fallback"],
                lineups.actual_lineup(self.full, season, last, f["team_id"]),
            )

    def test_without_any_lineup_everything_is_null(self):
        # 2022: no skater stats in the fixture, so no window and no fallback.
        self.assertEqual(
            self.full.execute(
                "SELECT COUNT(*), COUNT(missing_share_10), COUNT(missing_share_10_fallback), "
                "MAX(n_window_games_10) FROM features_availability WHERE season = 2022"
            ).fetchone()[:],
            (992, 0, 0, 0),
        )


class AvailabilityPointInTimeTest(FeatureTest):
    def truncated(self, season, game_id, name):
        start = _game(self.full, season, game_id)["start_utc"]
        hockey = self.edited_hockey(
            name,
            ("DELETE FROM games WHERE start_utc >= ? AND NOT (season = ? AND game_id = ?)",
             (start, season, game_id)),
            (f"UPDATE games SET {UNPLAYED} WHERE season = ? AND game_id = ?", (season, game_id)),
        )
        return self.build_features(name, hockey)

    def test_truncation(self):
        for season, game_id in TARGETS:
            with self.subTest(game=(season, game_id)):
                truncated = self.truncated(season, game_id, f"avtrunc{season}_{game_id}")
                g = _game(truncated, season, game_id)
                for role in ("home", "away"):
                    team_id = g[f"{role}_team_id"]
                    full, cut = _row(self.full, season, game_id, team_id), _row(truncated, season, game_id, team_id)
                    if full["lineup_source"] == lineups.SNAPSHOT:
                        self.assertEqual(tuple(cut), tuple(full))
                        continue
                    # Unplayed now, so no actual lineup: the fallback.
                    self.assertEqual(cut["lineup_source"], lineups.UNKNOWN)
                    self.assertEqual(cut["missing_share_10"], cut["missing_share_10_fallback"])
                    self.assertEqual([cut[c] for c in WINDOW_COLUMNS], [full[c] for c in WINDOW_COLUMNS])
                    # The full build's lineup over the truncated build's history.
                    actual = lineups.actual_lineup(self.full, season, game_id, team_id)
                    values = availability.team_values(availability.team_history(truncated, g, team_id), actual)
                    self.assertEqual(values, [full[c] for c in VALUE_COLUMNS])

    def test_poison(self):
        for season, game_id in TARGETS:
            with self.subTest(game=(season, game_id)):
                day = _game(self.full, season, game_id)["start_date"]
                hockey = self.edited_hockey(f"avpoison{season}_{game_id}", (TOI_POISON, (day,)))
                poisoned = self.build_features(f"avpoison{season}_{game_id}", hockey)
                where = ("season = ? AND game_id = ?", (season, game_id))
                self.assertEqual(
                    rows(poisoned, "features_availability", *where), rows(self.full, "features_availability", *where)
                )
                later = "season = ? AND game_id IN (SELECT game_id FROM game_spine WHERE season = ? AND start_date > ?)"
                self.assertNotEqual(
                    rows(poisoned, "features_availability", later, (season, season, day)),
                    rows(self.full, "features_availability", later, (season, season, day)),
                )


if __name__ == "__main__":
    unittest.main()
