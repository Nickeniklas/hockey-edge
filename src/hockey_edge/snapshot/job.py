"""Snapshot capture job — Layer 2 per docs/DATA_PIPELINE.md.

Not scheduled yet (placement of an always-on machine is still an open item —
see CLAUDE.md). Run manually for now:

    python -m hockey_edge.snapshot.job

Each run: fetch current Liiga odds via OddsPapi and append-only write any
snapshots to data/snapshots.db. Lineup capture is stubbed (see lineups.py) and
is skipped until the pre-puck-drop game_detail test is done.

Alerting: failures log at CRITICAL, to both console and logs/snapshot_job.log.
There's no email/Slack hookup yet (open item) — until one exists, a CRITICAL
line in the log is the signal to check in manually. A missed capture is gone
forever, so this job must never silently swallow a failure.
"""

import logging
from pathlib import Path

from dotenv import load_dotenv

from hockey_edge.snapshot import storage
from hockey_edge.snapshot.odds.base import OddsProvider
from hockey_edge.snapshot.odds.oddspapi import LIIGA_TOURNAMENT_ID, OddsPapiProvider

LOG_DIR = Path(__file__).resolve().parents[3] / "logs"


def _configure_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("hockey_edge.snapshot")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        file_handler = logging.FileHandler(LOG_DIR / "snapshot_job.log")
        file_handler.setFormatter(fmt)
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
    return logger


def capture_odds(
    provider: OddsProvider, conn, logger: logging.Logger, *, book: str = "pinnacle"
) -> int:
    try:
        snapshots = provider.fetch_odds(tournament_ref=LIIGA_TOURNAMENT_ID, book=book)
    except Exception:
        logger.critical(
            "odds capture FAILED (provider=%s) — this poll's data is unrecoverable",
            provider.name,
            exc_info=True,
        )
        return 0

    for snapshot in snapshots:
        storage.insert_odds_snapshot(conn, snapshot)

    logger.info(
        "odds capture ok (provider=%s): %d snapshot(s) written",
        provider.name,
        len(snapshots),
    )
    return len(snapshots)


def capture_lineups(conn, logger: logging.Logger) -> int:
    logger.info(
        "lineup capture skipped — blocked on pre-game game_detail test, "
        "see hockey_edge.snapshot.lineups module docstring"
    )
    return 0


def main() -> None:
    load_dotenv()
    logger = _configure_logging()
    conn = storage.get_connection()
    try:
        provider = OddsPapiProvider()
        capture_odds(provider, conn, logger)
        capture_lineups(conn, logger)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
