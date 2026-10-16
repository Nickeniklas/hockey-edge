"""Heartbeat pings to healthchecks.io, a dead-man's switch for the scheduled jobs.

Each job pings its check when it runs. When a ping does not arrive on
schedule, or a "fail" ping does, healthchecks.io emails an alert. The alert
comes from outside the PC, so it also covers a PC that is off.
Plan: docs/PIPELINE_SAFETY_PLAN.md, design answers 3-5.

    check            env var             pinged by
    snapshot-job     HC_SNAPSHOT_JOB     every snapshot --once tick
    missed-windows   HC_MISSED_WINDOWS   every snapshot --once tick (fail when
                                         windows were newly missed, else success)
    nightly-sync     HC_NIGHTLY_SYNC     scripts/nightly_sync.py
    backup           HC_BACKUP           scripts/backup_data.py

Rules:

- ping() never raises. Timeout 10 s, no retry. A failed ping logs one
  warning and the caller carries on: capture matters more than the alert.
- A missing env variable is a no-op with one log line, so tests, the Mac and
  dry runs work without it.
- The ping URL works like a password and is never logged, not even inside an
  exception message (some urllib errors quote the URL), so failures are
  logged by exception type and HTTP status only. A ping body has the values
  of ODDSPAPI_KEY and the HC_* variables replaced before it is sent.
- This module never reads .env itself. The entry points (job.py main,
  nightly_sync.py, backup_data.py) load it, so importing this in a test can
  never pick up the real URLs.

Manual check that alerts arrive (sends one fail ping, then one success):

    python -m hockey_edge.ops.heartbeat --test missed-windows
"""

import argparse
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENV_VARS = {
    "snapshot-job": "HC_SNAPSHOT_JOB",
    "missed-windows": "HC_MISSED_WINDOWS",
    "nightly-sync": "HC_NIGHTLY_SYNC",
    "backup": "HC_BACKUP",
}
STATUSES = ("success", "fail", "start")
TIMEOUT_SECONDS = 10
# healthchecks.io stores up to 100 kB of a ping body; stay far below.
MAX_BODY_BYTES = 10_000
USER_AGENT = "hockey-edge-heartbeat/1"

REPO_ROOT = Path(__file__).resolve().parents[3]

logger = logging.getLogger("hockey_edge.ops.heartbeat")


# Values never sent in a ping body. A fail body can carry exception text, and
# request errors have quoted the OddsPapi key before (raise_for_status()).
SECRET_ENV_VARS = ("ODDSPAPI_KEY", *ENV_VARS.values())


def redact(text: str) -> str:
    for var in SECRET_ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            text = text.replace(value, f"[{var}]")
    return text


def _ping_url(base: str, status: str) -> str:
    base = base.rstrip("/")
    return base if status == "success" else f"{base}/{status}"


def ping(check: str, status: str = "success", body: str = "") -> bool:
    """Send one ping. Returns True if healthchecks.io answered 2xx. Never raises."""
    try:
        if check not in ENV_VARS or status not in STATUSES:
            logger.warning("heartbeat: unknown check %r or status %r, not sent", check, status)
            return False
        var = ENV_VARS[check]
        base = os.environ.get(var, "").strip()
        if not base:
            logger.info("heartbeat %s/%s skipped: %s not set", check, status, var)
            return False
        data = redact(body).encode("utf-8")[:MAX_BODY_BYTES]
        request = urllib.request.Request(
            _ping_url(base, status), data=data, method="POST",
            headers={"User-Agent": USER_AGENT, "Content-Type": "text/plain; charset=utf-8"},
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            code = response.status
        if 200 <= code < 300:
            logger.info("heartbeat %s/%s sent", check, status)
            return True
        logger.warning("heartbeat %s/%s not accepted: HTTP %s", check, status, code)
        return False
    except urllib.error.HTTPError as exc:
        logger.warning("heartbeat %s/%s not accepted: HTTP %s", check, status, exc.code)
        return False
    except Exception as exc:  # noqa: BLE001 -- a ping must never break the job
        # Type only: the message of some errors (ValueError on a malformed
        # URL, for one) contains the URL itself.
        logger.warning("heartbeat %s/%s failed: %s", check, status, type(exc).__name__)
        return False


class LastErrorHandler(logging.Handler):
    """Remembers the last ERROR-or-worse log message, for a fail ping's body."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.last: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.last = f"{record.levelname} {record.name}: {record.getMessage()}"
        except Exception:  # noqa: BLE001
            pass


def _main() -> None:
    parser = argparse.ArgumentParser(description="Send test pings to a healthchecks.io check.")
    parser.add_argument("--test", required=True, choices=sorted(ENV_VARS),
                        help="send one fail ping, then one success ping")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)
    failed = ping(args.test, "fail", f"test fail ping from heartbeat --test {args.test}")
    succeeded = ping(args.test, "success", f"test success ping from heartbeat --test {args.test}")
    print(f"{args.test}: fail ping {'sent' if failed else 'NOT sent'}, "
          f"success ping {'sent' if succeeded else 'NOT sent'}")
    if not (failed and succeeded):
        sys.exit(1)


if __name__ == "__main__":
    _main()
