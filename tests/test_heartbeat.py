"""hockey_edge.ops.heartbeat and the snapshot job's missed-windows report.

Run: python -m unittest discover -s tests

Zero real HTTP: urllib.request.urlopen is mocked in every ping test, and the
job tests replace heartbeat.ping with a recorder. The ping URLs here are
made up and only ever live in a patched os.environ.
"""

import io
import logging
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from hockey_edge.ops import heartbeat
from hockey_edge.snapshot import job, storage

URL = "https://hc-ping.example/0b1d0c4e-secret-uuid"
ENV = {"HC_MISSED_WINDOWS": URL, "HC_SNAPSHOT_JOB": URL + "-job"}
SECRET_ENV = {var: "" for var in heartbeat.SECRET_ENV_VARS}


class FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PingTestCase(unittest.TestCase):
    def setUp(self):
        # Start from no secrets at all, then add the fake URLs.
        env = mock.patch.dict("os.environ", {**SECRET_ENV, **ENV})
        env.start()
        self.addCleanup(env.stop)
        self.log = io.StringIO()
        handler = logging.StreamHandler(self.log)
        handler.setLevel(logging.DEBUG)
        heartbeat.logger.addHandler(handler)
        heartbeat.logger.setLevel(logging.DEBUG)
        self.addCleanup(heartbeat.logger.removeHandler, handler)

    def ping_with(self, urlopen_effect, check="missed-windows", status="fail", body="x"):
        with mock.patch.object(heartbeat.urllib.request, "urlopen") as urlopen:
            if isinstance(urlopen_effect, int):
                urlopen.return_value = FakeResponse(urlopen_effect)
            else:
                urlopen.side_effect = urlopen_effect
            result = heartbeat.ping(check, status, body)
        return result, urlopen


class PingTest(PingTestCase):
    def test_success_fail_and_start_urls(self):
        for status, url in (("success", URL), ("fail", URL + "/fail"), ("start", URL + "/start")):
            ok, urlopen = self.ping_with(200, status=status, body="hello")
            self.assertTrue(ok)
            request = urlopen.call_args.args[0]
            self.assertEqual(request.full_url, url)
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.data, b"hello")
            self.assertEqual(urlopen.call_args.kwargs["timeout"], heartbeat.TIMEOUT_SECONDS)

    def test_failures_never_raise(self):
        effects = [
            urllib.error.URLError("getaddrinfo failed"),
            socket.timeout("timed out"),
            TimeoutError("timed out"),
            urllib.error.HTTPError(URL + "/fail", 500, "Server Error", {}, None),
            ConnectionResetError(),
            ValueError(f"unknown url type: {URL}"),
            500,
        ]
        for effect in effects:
            with self.subTest(effect=effect):
                ok, urlopen = self.ping_with(effect)
                self.assertFalse(ok)
                self.assertEqual(urlopen.call_count, 1)  # no retry

    def test_missing_variable_is_a_no_op(self):
        with mock.patch.dict("os.environ", {"HC_BACKUP": ""}):
            ok, urlopen = self.ping_with(200, check="backup")
        self.assertFalse(ok)
        urlopen.assert_not_called()
        self.assertEqual(self.log.getvalue().count("\n"), 1)
        self.assertIn("HC_BACKUP not set", self.log.getvalue())

    def test_url_never_logged(self):
        for effect in (200, 500, urllib.error.HTTPError(URL + "/fail", 503, URL, {}, None),
                       urllib.error.URLError(URL), ValueError(f"unknown url type: {URL}"),
                       RuntimeError(URL)):
            self.ping_with(effect)
        logged = self.log.getvalue()
        self.assertTrue(logged)
        self.assertNotIn("hc-ping.example", logged)
        self.assertNotIn("secret-uuid", logged)

    def test_secrets_are_redacted_from_the_body(self):
        with mock.patch.dict("os.environ", {"ODDSPAPI_KEY": "k3y-value"}):
            _ok, urlopen = self.ping_with(
                200, body=f"HTTPError for https://api.oddspapi.io/v4/x?apiKey=k3y-value via {URL}"
            )
        sent = urlopen.call_args.args[0].data.decode()
        self.assertNotIn("k3y-value", sent)
        self.assertNotIn("secret-uuid", sent)
        self.assertIn("[ODDSPAPI_KEY]", sent)

    def test_unknown_check_is_not_sent(self):
        ok, urlopen = self.ping_with(200, check="nope")
        self.assertFalse(ok)
        urlopen.assert_not_called()

    def test_last_error_handler_keeps_the_last_error_line(self):
        log = logging.getLogger("test.heartbeat.last_error")
        handler = heartbeat.LastErrorHandler()
        log.addHandler(handler)
        self.addCleanup(log.removeHandler, handler)
        log.warning("not this")
        log.error("first %s", "error")
        log.critical("pass 2 (resync) FAILED")
        log.info("nor this")
        self.assertEqual(handler.last, "CRITICAL test.heartbeat.last_error: pass 2 (resync) FAILED")


class MissedWindowReportTest(unittest.TestCase):
    """run_once's missed-windows ping, on a temp snapshots.db."""

    START = "2026-09-18T15:30:00Z"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = storage.get_connection(Path(self._tmp.name) / "snapshots.db")
        storage.insert_discovered_fixture(
            self.conn, league="liiga", season=2027, game_id=2701309, serie="RUNKOSARJA",
            start_utc=self.START, home_team="Jukurit", away_team="Pelicans",
            started=False, ended=False, captured_at="2026-09-17T09:00:00Z", source="test", raw_payload="{}",
        )
        for kind, window, due_at in (("odds", "opening", "2026-09-17T15:30:00Z"),
                                     ("lineups", "closing", "2026-09-18T15:05:00Z")):
            storage.upsert_capture_window(self.conn, league="liiga", season=2027, game_id=2701309,
                                          kind=kind, window=window, due_at=due_at)
        self.pings = []

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def run_at(self, now_iso):
        def record(check, status="success", body=""):
            self.pings.append((check, status, body))
            return True

        with mock.patch.object(job, "_now_iso", return_value=now_iso), \
                mock.patch.object(job.fixtures, "discover_fixtures"), \
                mock.patch.object(job, "capture_odds_for_due_windows") as capture_odds, \
                mock.patch.object(job, "capture_lineups", return_value=0), \
                mock.patch.object(job.heartbeat, "ping", side_effect=record):
            ok = job.run_once(self.conn, logging.getLogger("test.job"))
        capture_odds.assert_not_called()  # every window here is past or not yet due
        self.assertTrue(ok)
        return self.pings[-1]

    def test_newly_missed_windows_send_one_fail_ping_listing_them(self):
        check, status, body = self.run_at("2026-09-18T16:00:00Z")
        self.assertEqual((check, status), ("missed-windows", "fail"))
        self.assertEqual(len(self.pings), 1)
        self.assertIn("2 capture window(s) newly missed", body)
        self.assertIn("kind=odds season=2027 game_id=2701309 window=opening", body)
        self.assertIn("kind=lineups season=2027 game_id=2701309 window=closing", body)

    def test_same_windows_are_not_reported_twice_and_every_clean_run_sends_success(self):
        self.run_at("2026-09-18T16:00:00Z")
        for now in ("2026-09-18T16:15:00Z", "2026-09-18T16:30:00Z", "2026-09-18T16:45:00Z"):
            self.assertEqual(self.run_at(now), ("missed-windows", "success", ""))
        self.assertEqual([p[1] for p in self.pings], ["fail", "success", "success", "success"])

    def test_run_with_nothing_missed_sends_success(self):
        # Before the opening window is due: nothing to miss.
        self.assertEqual(self.run_at("2026-09-17T09:00:00Z"), ("missed-windows", "success", ""))


class SnapshotJobPingTest(unittest.TestCase):
    """main()'s snapshot-job ping: success, or fail with the last error line."""

    def run_main(self, run_once_result=True, run_once_effect=None):
        pings = []

        def record(check, status="success", body=""):
            pings.append((check, status, body))
            return True

        def fake_run_once(conn, logger):
            if run_once_effect:
                raise run_once_effect
            if not run_once_result:
                logger.critical("odds capture FAILED")
            return run_once_result

        with mock.patch("sys.argv", ["job", "--once"]), \
                mock.patch.object(job, "load_dotenv"), \
                mock.patch.object(job.storage, "get_connection"), \
                mock.patch.object(job, "run_once", side_effect=fake_run_once), \
                mock.patch.object(job, "_configure_logging", return_value=logging.getLogger("test.job.main")), \
                mock.patch.object(job.heartbeat, "ping", side_effect=record):
            try:
                job.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
            except RuntimeError:
                code = "raised"
        return code, [p for p in pings if p[0] == "snapshot-job"]

    def test_clean_pass_sends_success(self):
        self.assertEqual(self.run_main(), (0, [("snapshot-job", "success", "")]))

    def test_failed_pass_sends_fail_with_last_error_line(self):
        code, pings = self.run_main(run_once_result=False)
        self.assertEqual(code, 1)
        self.assertEqual(pings, [("snapshot-job", "fail", "CRITICAL test.job.main: odds capture FAILED")])

    def test_aborted_pass_sends_fail(self):
        code, pings = self.run_main(run_once_effect=RuntimeError("boom"))
        self.assertEqual(code, "raised")
        self.assertEqual(pings, [("snapshot-job", "fail", "aborted: RuntimeError: boom")])


class NightlySyncPingTest(unittest.TestCase):
    """scripts/nightly_sync.py main(): start, then success or fail."""

    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import nightly_sync
        cls.sync = nightly_sync

    def run_main(self, argv, main_effect=None):
        pings = []

        def record(check, status="success", body=""):
            pings.append((check, status, body))
            return True

        def fake_main(args):
            if main_effect is SystemExit:
                self.sync.logger.critical("pass 2 (resync) FAILED")
                raise SystemExit(1)
            if main_effect:
                raise main_effect

        with mock.patch("sys.argv", ["nightly_sync.py", *argv]), \
                mock.patch.object(self.sync, "_configure_logging"), \
                mock.patch.object(self.sync, "load_dotenv"), \
                mock.patch.object(self.sync, "_main", side_effect=fake_main), \
                mock.patch.object(self.sync.heartbeat, "ping", side_effect=record):
            try:
                self.sync.main()
            except (SystemExit, RuntimeError):
                pass
        return [p[1:] for p in pings if p[0] == "nightly-sync"]

    def test_clean_run_sends_start_then_success(self):
        self.assertEqual(self.run_main([]), [("start", ""), ("success", "")])

    def test_failed_run_sends_fail_with_exit_status_and_last_error(self):
        self.assertEqual(self.run_main([], SystemExit),
                         [("start", ""), ("fail", "exit status 1: CRITICAL hockey_edge.nightly_sync: pass 2 (resync) FAILED")])

    def test_aborted_run_sends_fail(self):
        self.assertEqual(self.run_main([], RuntimeError("boom")),
                         [("start", ""), ("fail", "aborted: RuntimeError: boom")])

    def test_dry_run_sends_nothing(self):
        self.assertEqual(self.run_main(["--dry-run"]), [])


if __name__ == "__main__":
    unittest.main()
