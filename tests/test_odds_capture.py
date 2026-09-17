"""Phase 3 wiring: retry pacing, per-game satisfaction, budget ceiling.

Run: python -m unittest discover -s tests
"""

import json
import logging
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from hockey_edge.snapshot import job, storage
from hockey_edge.snapshot.odds.base import OddsProvider, OddsSnapshot
from hockey_edge.snapshot.odds.oddspapi import load_team_map, parse_fixture

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "oddspapi"
LOGGER = logging.getLogger("test")
NOW = "2026-09-17T18:00:00Z"


def window(window_name, due_at, *, season=2027, game_id=1, row_id=1):
    return {"id": row_id, "season": season, "game_id": game_id,
            "window": window_name, "due_at": due_at}


class EligibilityTest(unittest.TestCase):
    def test_no_poll_yet_everything_eligible(self):
        due = [window("opening", "2026-09-17T12:00:00Z")]
        self.assertEqual(job.windows_eligible_for_poll(due, last_poll_iso=None, now_iso=NOW), due)

    def test_recent_poll_after_due_holds_off(self):
        due = [window("opening", "2026-09-17T12:00:00Z")]
        self.assertEqual(
            job.windows_eligible_for_poll(due, last_poll_iso="2026-09-17T17:45:00Z", now_iso=NOW), []
        )

    def test_retry_allowed_once_the_gap_has_passed(self):
        due = [window("opening", "2026-09-17T12:00:00Z")]
        self.assertEqual(
            len(job.windows_eligible_for_poll(due, last_poll_iso="2026-09-17T16:30:00Z", now_iso=NOW)), 1
        )

    def test_poll_before_the_window_came_due_does_not_count(self):
        due = [window("mid", "2026-09-17T17:50:00Z")]
        self.assertEqual(
            len(job.windows_eligible_for_poll(due, last_poll_iso="2026-09-17T17:45:00Z", now_iso=NOW)), 1
        )

    def test_closing_window_always_eligible(self):
        due = [window("closing", "2026-09-17T17:40:00Z"), window("mid", "2026-09-17T17:40:00Z", row_id=2)]
        eligible = job.windows_eligible_for_poll(due, last_poll_iso="2026-09-17T17:55:00Z", now_iso=NOW)
        self.assertEqual([r["window"] for r in eligible], ["closing"])


class FakeProvider(OddsProvider):
    """Serves each book its own saved board from 2026-09-17, without any HTTP."""

    name = "oddspapi"

    def __init__(self, *args, **kwargs):
        self.calls = []

    def fetch_odds(self, *, tournament_ref, book):
        self.calls.append(book)
        board = json.loads((FIXTURES / f"odds-by-tournaments_134_{book}_2026-09-17.json").read_text(encoding="utf-8"))
        captured_at = datetime(2026, 9, 17, 18, tzinfo=timezone.utc)
        return [s for f in board for s in parse_fixture(f, book=book, captured_at=captured_at)]


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = storage.get_connection(Path(self._tmp.name) / "snapshots.db")
        team_map = load_team_map()
        # Only Jukurit–Pelicans (a game the board prices) is discovered; game 999 is not.
        payload = json.dumps({"homeTeam": {"teamId": team_map[3824]}, "awayTeam": {"teamId": team_map[3844]}})
        storage.insert_discovered_fixture(
            self.conn, league="liiga", season=2027, game_id=2701309, serie="RUNKOSARJA",
            start_utc="2026-09-18T15:30:00Z", home_team="Jukurit", away_team="Pelicans",
            started=False, ended=False, captured_at=NOW, source="test", raw_payload=payload,
        )
        self.due = [
            window("opening", "2026-09-17T15:30:00Z", game_id=2701309, row_id=1),
            window("opening", "2026-09-17T15:30:00Z", game_id=999, row_id=2),
        ]
        for row in self.due:
            storage.upsert_capture_window(
                self.conn, league="liiga", season=2027, game_id=row["game_id"],
                kind="odds", window="opening", due_at=row["due_at"],
            )
        self.due = [dict(r, id=i + 1) for i, r in enumerate(self.due)]

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def _run(self):
        with mock.patch.object(job, "OddsPapiProvider", FakeProvider), \
                mock.patch.object(job.time, "sleep") as sleep:
            satisfied = job.capture_odds_for_due_windows(self.conn, LOGGER, due=self.due, now_iso=NOW)
        self.sleeps = [call.args[0] for call in sleep.call_args_list]
        return satisfied

    def test_books_are_spaced_apart(self):
        """Back-to-back calls got the second book 429'd on the first live run."""
        self._run()
        self.assertEqual(self.sleeps, [job.ODDS_BOOK_DELAY_SECONDS] * (len(job.ODDS_BOOKS) - 1))

    def test_no_sleep_when_the_poll_is_skipped(self):
        storage.insert_api_usage(self.conn, provider="oddspapi", endpoint="odds-by-tournaments",
                                 requested_at="2026-09-17T17:50:00Z", http_status=200, outcome="success_content")
        self._run()
        self.assertEqual(self.sleeps, [])

    def test_only_priced_game_is_satisfied(self):
        self.assertEqual(self._run(), 1)
        statuses = dict(self.conn.execute("SELECT game_id, status FROM capture_windows"))
        self.assertEqual(statuses, {2701309: "satisfied", 999: "pending"})

    def test_one_request_per_book_and_rows_written(self):
        self._run()
        usage = self.conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT requested_at) FROM api_usage WHERE provider = 'oddspapi'"
        ).fetchone()
        self.assertEqual(usage[0], len(job.ODDS_BOOKS))
        books = [r[0] for r in self.conn.execute("SELECT DISTINCT book FROM odds_snapshots ORDER BY book")]
        self.assertEqual(books, sorted(job.ODDS_BOOKS))
        resolved = self.conn.execute(
            "SELECT COUNT(*) FROM odds_snapshots WHERE game_id = 2701309 AND parsed = 1"
        ).fetchone()[0]
        self.assertEqual(resolved, 2 * len(job.ODDS_BOOKS))  # 1x2 + moneyline, per book
        # Unresolved fixtures are still stored, raw kept.
        self.assertGreater(
            self.conn.execute("SELECT COUNT(*) FROM odds_snapshots WHERE game_id IS NULL").fetchone()[0], 0
        )

    def test_ceiling_blocks_the_poll(self):
        for _ in range(job.ODDS_MONTHLY_CEILING - 1):
            storage.insert_api_usage(self.conn, provider="oddspapi", endpoint="odds-by-tournaments",
                                     requested_at=NOW, http_status=200, outcome="success_content")
        self.assertEqual(self._run(), 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM api_usage").fetchone()[0], job.ODDS_MONTHLY_CEILING - 1
        )

    def test_recent_poll_skips_without_requesting(self):
        storage.insert_api_usage(self.conn, provider="oddspapi", endpoint="odds-by-tournaments",
                                 requested_at="2026-09-17T17:50:00Z", http_status=200, outcome="success_content")
        self.assertEqual(self._run(), 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0], 0)

    def test_failed_poll_does_not_block_retry(self):
        storage.insert_api_usage(self.conn, provider="oddspapi", endpoint="odds-by-tournaments",
                                 requested_at="2026-09-17T17:50:00Z", http_status=None, outcome="failure")
        self.assertEqual(self._run(), 1)


if __name__ == "__main__":
    unittest.main()
