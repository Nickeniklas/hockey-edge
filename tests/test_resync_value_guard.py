"""resync.py's value guard for game_stats: refuse a reparse whose totals
collapse or whose goals move away from the final score, even when every
row count holds.

Run: python -m unittest discover -s tests

Fixtures are real game_stats responses:
- season 2024 game 2: the 2026-07 original and the 2026-09-25 refetch with
  stripped values (same row counts; power-play and shorthanded instances
  3 -> 0, team-period goals 5 -> 2).
- season 2027 game 2701301: the first post-game fetch (2026-09-16) and the
  live correction liiga.fi served a day later (player time on ice -5.3%,
  corsi -8%), which must still be accepted.

The per-team tests strip one team of the 2027 correction the way liiga.fi
strips history (values zeroed, rows kept): see strip_team.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hockey_edge.ingest import db, raw_cache
from hockey_edge.ingest.liiga import parsers, resync

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "liiga" / "game_stats"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


ORIGINAL_2024 = load("2024_game2_original.json")
STRIPPED_2024 = load("2024_game2_refetch_stripped.json")
FIRST_2027 = load("2027_game2701301_first.json")
CORRECTED_2027 = load("2027_game2701301_corrected.json")


def strip_team(data: dict, side: str, player_fields=(), goalie_fields=()) -> dict:
    """A copy of a real game_stats response with the named period-stat
    fields of one side ('homeTeam' / 'awayTeam') set to 0, rows kept."""
    data = json.loads(json.dumps(data))
    for team_period in data[side]:
        for key, fields in (("periodPlayerStats", player_fields), ("goaliePeriodStats", goalie_fields)):
            for row in team_period.get(key) or []:
                for field in fields:
                    row["period"][field] = 0
    return data


JUKURIT = "292293444:jukurit"  # away in 2027:2701301
SKATER_FIELDS = ("timeofice", "corsiFor", "faceoffsTotal")

# Real games rows, copied from data/hockey.db.
GAMES = {
    (2, 2024): "(2, 2024, 'RUNKOSARJA', '2023-09-13T15:30:00Z', '292293444:jukurit', 'Jukurit', "
    "'461765763:kookoo', 'KooKoo', 1, 4, 1, 1)",
    (2701301, 2027): "(2701301, 2027, 'RUNKOSARJA', '2026-09-15T15:30:00Z', '651304385:tps', 'TPS', "
    "'292293444:jukurit', 'Jukurit', 1, 4, 1, 1)",
}
TABLES = resync.GUARDED_TABLES["game_stats"]


class ValueGuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = db.get_connection(Path(self._tmp.name) / "hockey.db")
        for values in GAMES.values():
            self.conn.execute(
                "INSERT INTO games (game_id, season, phase, start_utc, home_team_id, home_team_name, away_team_id, "
                f"away_team_name, home_goals, away_goals, started, ended) VALUES {values}"
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def _store(self, data: dict, game_id: int, season: int) -> None:
        parsers.upsert_game_stats(self.conn, game_id, season, parsers.parse_game_stats(data, game_id, season))
        self.conn.commit()

    def _reparse(self, data: dict, game_id: int, season: int) -> str:
        parsed = parsers.parse_game_stats(data, game_id, season)
        return resync.reparse_with_shrink_guard(
            self.conn, game_id=game_id, season=season, endpoint_name="game_stats",
            do_upsert=lambda: parsers.upsert_game_stats(self.conn, game_id, season, parsed),
        )

    def _rows(self, game_id: int, season: int) -> dict[str, list[tuple]]:
        return {
            t: self.conn.execute(f"SELECT * FROM {t} WHERE game_id = ? AND season = ? ORDER BY id", (game_id, season)).fetchall()
            for t in TABLES
        }

    def test_stripped_refetch_is_refused_and_rows_restored_exactly(self):
        self._store(ORIGINAL_2024, 2, 2024)
        before = self._rows(2, 2024)
        with self.assertLogs("hockey_edge.ingest.resync", "WARNING") as logs:
            outcome = self._reparse(STRIPPED_2024, 2, 2024)
        self.assertEqual(outcome, "value_guarded")
        self.assertEqual(self._rows(2, 2024), before)  # row-for-row, ids included
        for name in ("team_powerplay_instances", "team_shorthanded_instances", "team_goals"):
            self.assertIn(name, logs.output[0])

    def test_stripped_refetch_shrinks_no_table(self):
        # The case the row-count guard alone missed: no table loses a row
        # (puck control even gains two), so only the value guard can refuse it.
        self._store(ORIGINAL_2024, 2, 2024)
        before = {t: len(r) for t, r in self._rows(2, 2024).items()}
        self._store(STRIPPED_2024, 2, 2024)
        after = {t: len(r) for t, r in self._rows(2, 2024).items()}
        self.assertEqual([t for t in TABLES if after[t] < before[t]], [])

    def test_same_response_again_is_applied(self):
        self._store(ORIGINAL_2024, 2, 2024)
        self.assertEqual(self._reparse(ORIGINAL_2024, 2, 2024), "reparsed")

    def test_real_live_correction_with_drops_is_applied(self):
        self._store(FIRST_2027, 2701301, 2027)
        before = resync.game_stats_totals(self.conn, 2701301, 2027)
        self.assertEqual(self._reparse(CORRECTED_2027, 2701301, 2027), "reparsed")
        after = resync.game_stats_totals(self.conn, 2701301, 2027)
        # The drops this fixture exists for, so the test can't pass vacuously.
        self.assertEqual((before["player_toi"], after["player_toi"]), (37478, 35479))
        self.assertEqual((before["player_corsi_for"], after["player_corsi_for"]), (307, 282))

    def test_resync_game_endpoint_reports_value_guarded(self):
        self._store(ORIGINAL_2024, 2, 2024)
        fetched = raw_cache.FetchResult(data=STRIPPED_2024, status="success", from_cache=False)
        with mock.patch.object(resync.raw_cache, "fetch", return_value=fetched), self.assertLogs("hockey_edge.ingest.resync", "WARNING"):
            outcome = resync.resync_game_endpoint(self.conn, 2024, 2, "game_stats", min_hours_since_fetch=12)
        self.assertEqual(outcome, "value_guarded")

    def test_one_team_stripped_is_refused_per_team_only(self):
        # Jukurit's skater time on ice, corsi and faceoffs zeroed: the game
        # totals keep 50.1%, 77% and 50%, so the per-game rule alone passes.
        self._store(CORRECTED_2027, 2701301, 2027)
        before = self._rows(2701301, 2027)
        totals_before = resync.game_stats_totals(self.conn, 2701301, 2027)
        stripped = strip_team(CORRECTED_2027, "awayTeam", player_fields=SKATER_FIELDS)

        self._store(stripped, 2701301, 2027)
        totals_after = resync.game_stats_totals(self.conn, 2701301, 2027)
        self.assertEqual(totals_after["player_toi"], 17776)  # of 35479
        self.assertEqual(resync.value_guard_violations(totals_before, totals_after, 5), {})
        self._store(CORRECTED_2027, 2701301, 2027)

        with self.assertLogs("hockey_edge.ingest.resync", "WARNING") as logs:
            outcome = self._reparse(stripped, 2701301, 2027)
        self.assertEqual(outcome, "value_guarded")
        self.assertEqual(self._rows(2701301, 2027), before)  # row-for-row, ids included
        for name in ("player_toi", "player_corsi_for", "player_faceoffs"):
            self.assertIn(f"{name}[{JUKURIT}]", logs.output[0])
        self.assertNotIn("651304385:tps", logs.output[0])

    def test_real_live_correction_passes_per_team(self):
        # TPS's skater time on ice fell 10% (19768 -> 17776) and its corsi
        # 10% in the real correction; both stay above the per-team floor.
        self._store(FIRST_2027, 2701301, 2027)
        before = resync.game_stats_team_totals(self.conn, 2701301, 2027)
        self.assertEqual(self._reparse(CORRECTED_2027, 2701301, 2027), "reparsed")
        after = resync.game_stats_team_totals(self.conn, 2701301, 2027)
        self.assertEqual(resync.team_value_guard_violations(before, after), {})
        self.assertEqual((before["player_toi"]["651304385:tps"], after["player_toi"]["651304385:tps"]), (19768, 17776))
        self.assertEqual((before["player_corsi_for"]["651304385:tps"], after["player_corsi_for"]["651304385:tps"]), (243, 218))

    def test_team_growing_from_zero_is_applied(self):
        # Stored with Jukurit's goalie time on ice at 0 (as all of 2026 is
        # served), then the full values arrive: grow-from-zero must pass.
        self._store(strip_team(CORRECTED_2027, "awayTeam", goalie_fields=("timeofice",)), 2701301, 2027)
        before = resync.game_stats_team_totals(self.conn, 2701301, 2027)
        self.assertEqual(before["goalie_toi"][JUKURIT], 0)
        self.assertEqual(self._reparse(CORRECTED_2027, 2701301, 2027), "reparsed")
        self.assertEqual(resync.game_stats_team_totals(self.conn, 2701301, 2027)["goalie_toi"][JUKURIT], 3600)


class TeamViolationsTest(unittest.TestCase):
    def _totals(self, **overrides) -> dict:
        totals = {name: {"a": 100, "b": 100} for name in resync.VALUE_TOTALS}
        totals.update(overrides)
        return totals

    def test_floor_is_half_per_team(self):
        self.assertEqual(resync.team_value_guard_violations(self._totals(), self._totals(player_toi={"a": 50, "b": 100})), {})
        self.assertEqual(
            resync.team_value_guard_violations(self._totals(), self._totals(player_toi={"a": 49, "b": 100})),
            {"player_toi[a]": (100, 49)},
        )

    def test_team_missing_after_counts_as_zero(self):
        self.assertEqual(
            resync.team_value_guard_violations(self._totals(), self._totals(goalie_toi={"b": 100})),
            {"goalie_toi[a]": (100, 0)},
        )

    def test_zero_before_is_not_checked(self):
        before = self._totals(goalie_toi={"a": 0, "b": 0})
        self.assertEqual(resync.team_value_guard_violations(before, self._totals(goalie_toi={})), {})

    def test_new_team_is_not_checked(self):
        self.assertEqual(resync.team_value_guard_violations(self._totals(), self._totals(player_toi={"a": 100, "b": 100, "c": 0})), {})


class ViolationsTest(unittest.TestCase):
    def _totals(self, **overrides) -> dict:
        totals = {name: 100 for name in resync.VALUE_TOTALS}
        totals.update(team_goals=5, player_goals=5)
        totals.update(overrides)
        return totals

    def test_floor_is_half(self):
        self.assertEqual(resync.value_guard_violations(self._totals(), self._totals(player_toi=50), 5), {})
        self.assertEqual(
            resync.value_guard_violations(self._totals(), self._totals(player_toi=49), 5), {"player_toi": (100, 49)}
        )

    def test_growth_is_allowed(self):
        grown = self._totals(**{name: 400 for name in resync.VALUE_TOTALS})
        self.assertEqual(resync.value_guard_violations(self._totals(), grown, 5), {})

    def test_zero_before_cannot_be_refused(self):
        self.assertEqual(resync.value_guard_violations(self._totals(goalie_toi=0), self._totals(goalie_toi=0), 5), {})

    def test_goal_correction_toward_final_is_allowed(self):
        # Seen live on 2027:2701279: player goals 7 -> 6 with a 4-2 final.
        before, after = self._totals(team_goals=6, player_goals=7), self._totals(team_goals=6, player_goals=6)
        self.assertEqual(resync.value_guard_violations(before, after, 6), {})
        self.assertEqual(resync.value_guard_violations(after, before, 6), {"player_goals": (6, 7, 6)})

    def test_unknown_final_skips_goals(self):
        self.assertEqual(resync.value_guard_violations(self._totals(), self._totals(team_goals=0), None), {})


if __name__ == "__main__":
    unittest.main()
