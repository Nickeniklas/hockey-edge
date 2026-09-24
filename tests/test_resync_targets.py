"""resync --targets, and the recovery target/report scripts.

Run: python -m unittest discover -s tests

The games rows below are copied from data/hockey.db as they stand (real
ids, teams, start times, ended flags). Per-game event counts match the real
DB for game_penalty_events and game_puck_control; the event rows themselves
are minimal stand-ins, since only their counts matter here.
"""

import csv
import io
import json
import logging
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from hockey_edge.ingest import db
from hockey_edge.ingest.liiga import resync

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import recovery_report  # noqa: E402
import recovery_targets  # noqa: E402

# (game_id, season, phase, start_utc, home_id, home, away_id, away, hg, ag, started, ended)
REAL_GAMES = [
    (1, 2023, "RUNKOSARJA", "2022-09-13T15:30:00Z", "362185137:tappara", "Tappara", "651304385:tps", "TPS", 3, 1, 1, 1),
    (2, 2023, "RUNKOSARJA", "2022-09-14T15:30:00Z", "168761288:hifk", "HIFK", "55786244:hpk", "HPK", 4, 1, 1, 1),
    (1, 2024, "RUNKOSARJA", "2023-09-12T15:30:00Z", "624554857:lukko", "Lukko", "55786244:hpk", "HPK", 1, 2, 1, 1),
    # Never-ended friendly: why the live target count is 1,135, not the backlog's 1,137.
    (6763, 2017, "PRACTICE", "2016-09-06T15:30:00Z", "461765763:kookoo", "KooKoo", "1368623516:espoo united", "Espoo United", 0, 0, 0, 0),
    (1, 2025, "RUNKOSARJA", "2024-09-10T15:30:00Z", "168761288:hifk", "HIFK", "1368624751:k-espoo", "K-Espoo", 5, 2, 1, 1),
]
# (season, game_id) -> (penalty events, puck-control periods), as in the real DB.
REAL_COUNTS = {(2023, 1): (0, 1), (2023, 2): (0, 1), (2024, 1): (9, 1), (2017, 6763): (0, 0), (2025, 1): (7, 3)}


def build_db(path: Path) -> sqlite3.Connection:
    conn = db.get_connection(path)
    conn.executemany(
        "INSERT INTO games (game_id, season, phase, start_utc, home_team_id, home_team_name, away_team_id, "
        "away_team_name, home_goals, away_goals, started, ended) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        REAL_GAMES,
    )
    for (season, game_id), (n_pen, n_puck) in REAL_COUNTS.items():
        conn.executemany(
            "INSERT INTO game_penalty_events (game_id, season, team_id, event_id) VALUES (?,?,?,?)",
            [(game_id, season, "x", i) for i in range(n_pen)],
        )
        conn.executemany(
            "INSERT INTO game_puck_control (game_id, season, period) VALUES (?,?,?)",
            [(game_id, season, p) for p in range(1, n_puck + 1)],
        )
    conn.commit()
    return conn


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path


class LoadTargetsTest(TempDirTest):
    def test_parses_rows_in_order_and_drops_duplicates(self):
        path = self.write("t.csv", "season,game_id\n2023,2\n2023,1\n2023,2\n\n")
        self.assertEqual(resync.load_targets(path), [(2023, 2), (2023, 1)])

    def test_missing_header_is_fatal(self):
        path = self.write("t.csv", "2023,1\n")
        with self.assertRaises(ValueError):
            resync.load_targets(path)

    def test_bad_rows_are_skipped_with_warning(self):
        path = self.write("t.csv", "season,game_id\n2023,1\nabc,2\n2024\n2024,1\n")
        logging.disable(logging.NOTSET)
        with self.assertLogs("hockey_edge.ingest.resync", level="WARNING") as logs:
            targets = resync.load_targets(path)
        self.assertEqual(targets, [(2023, 1), (2024, 1)])
        self.assertEqual(len(logs.records), 2)


class TargetCandidatesTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.conn = build_db(self.tmp / "hockey.db")

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def test_keeps_ended_known_games_only(self):
        targets = [(2023, 1), (2017, 6763), (2023, 999), (2024, 1)]
        logging.disable(logging.NOTSET)
        with self.assertLogs("hockey_edge.ingest.resync", level="WARNING") as logs:
            candidates = resync.find_target_candidates(self.conn, targets)
        self.assertEqual(candidates, [(1, 2023), (1, 2024)])  # (game_id, season)
        self.assertEqual(len(logs.records), 2)  # not ended + unknown

    def test_dry_run_makes_no_fetch(self):
        with mock.patch.object(resync.raw_cache, "fetch", side_effect=AssertionError("HTTP in dry run")):
            summary = resync.run_resync(
                self.conn, targets=[(2023, 1), (2023, 2), (2024, 1)], endpoints=["game_detail", "game_stats"],
                min_hours_since_fetch=168, dry_run=True, outcomes_path=self.tmp / "out.csv",
            )
        self.assertEqual(summary["candidates"], 3)
        self.assertEqual(summary["by_season"][2023]["game_detail"], {"would_check": 2})
        self.assertEqual(summary["outcomes"]["game_stats"], {"would_check": 3})
        self.assertFalse((self.tmp / "out.csv").exists())

    def test_exactly_one_selector(self):
        kwargs = dict(endpoints=["game_detail"], min_hours_since_fetch=1, dry_run=True)
        with self.assertRaises(ValueError):
            resync.run_resync(self.conn, **kwargs)
        with self.assertRaises(ValueError):
            resync.run_resync(self.conn, days=7, targets=[(2023, 1)], **kwargs)

    def test_outcomes_file_appends_across_runs(self):
        out = self.tmp / "outcomes.csv"
        with mock.patch.object(resync, "resync_game_endpoint", return_value="unchanged"):
            for _ in range(2):
                resync.run_resync(
                    self.conn, targets=[(2023, 1)], endpoints=["game_detail"],
                    min_hours_since_fetch=168, dry_run=False, outcomes_path=out,
                )
        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0][:4], ["season", "game_id", "endpoint", "outcome"])
        self.assertEqual([r[:4] for r in rows[1:]], [["2023", "1", "game_detail", "unchanged"]] * 2)


class CliTest(unittest.TestCase):
    def test_days_and_targets_are_mutually_exclusive(self):
        argv = ["resync", "--days", "3", "--targets", "t.csv"]
        with mock.patch.object(sys, "argv", argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            resync.main()
        self.assertEqual(cm.exception.code, 2)


class RecoveryTargetsTest(TempDirTest):
    def test_selection(self):
        conn = build_db(self.tmp / "hockey.db")
        try:
            sel = recovery_targets.select_targets(conn)
        finally:
            conn.close()
        # Zero penalties, ended, 2015-2024: not the unended friendly, not 2025.
        self.assertEqual(sel["game_detail_targets.csv"], [(2023, 1), (2023, 2)])
        # Fewer than 3 puck-control rows: 2024:1 has penalties but only 1 period.
        self.assertEqual(sel["game_stats_targets.csv"], [(2023, 1), (2023, 2), (2024, 1)])

    def test_written_file_round_trips_through_load_targets(self):
        path = self.tmp / "out" / "t.csv"
        recovery_targets.write_targets(path, [(2023, 1), (2023, 2)])
        self.assertEqual(resync.load_targets(path), [(2023, 1), (2023, 2)])


class RecoveryReportTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.conn = build_db(self.tmp / "hockey.db")
        self.targets = [(2023, 1), (2023, 2)]

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def test_report_counts(self):
        report = recovery_report.build_report(self.conn, self.targets)
        g2023 = report["by_season_phase"]["2023"]["RUNKOSARJA"]
        self.assertEqual(g2023["games"], 2)
        self.assertEqual(g2023["zero_penalty_games"], 2)
        self.assertNotIn("2017", report["by_season_phase"])  # ended=1 only
        self.assertEqual(report["by_season_phase"]["2024"]["RUNKOSARJA"]["rows"]["game_penalty_events"], 9)
        self.assertEqual(report["targets"]["2023:1"]["game_penalty_events"], 0)
        json.dumps(report)  # serialisable

    def test_diff_classifies_and_flags_shrink(self):
        before = recovery_report.build_report(self.conn, self.targets)
        # 2023:1 recovers 5 penalties; 2023:2 is refused; 2024:1 loses a puck-control row.
        self.conn.executemany(
            "INSERT INTO game_penalty_events (game_id, season, team_id, event_id) VALUES (1, 2023, 'x', ?)",
            [(i,) for i in range(5)],
        )
        self.conn.execute("DELETE FROM game_puck_control WHERE game_id = 1 AND season = 2024")
        self.conn.commit()
        after = recovery_report.build_report(self.conn, self.targets)

        outcomes = self.write(
            "o.csv",
            "season,game_id,endpoint,outcome,checked_at\n"
            "2023,1,game_detail,reparsed,t\n2023,2,game_detail,shrink_guarded,t\n"
            "2023,2,game_detail,skipped_not_due,t\n",
        )
        classes = recovery_report.classify_targets(before, after, recovery_report.load_outcomes(outcomes))
        self.assertEqual(classes["game_detail"]["2023"], {"recovered": 1, "refused": 1})

        with redirect_stdout(io.StringIO()) as out:
            ok = recovery_report.print_diff(before, after, recovery_report.load_outcomes(outcomes))
        self.assertFalse(ok)
        self.assertIn("SHRANK 2024 game_puck_control: 1 -> 0", out.getvalue())


if __name__ == "__main__":
    unittest.main()
