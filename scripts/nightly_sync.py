"""Nightly live-season sync for hockey.db — one command, two passes.

Keeps `data/hockey.db` current for the season in progress, which nothing else
does: `hockey_edge.snapshot.job` writes only to `data/snapshots.db` (it reads
liiga.fi live and deliberately never touches hockey.db), and `backfill.py` was
built as a one-time-per-historical-season pass. Without this wrapper running,
completed games of the live season never land in hockey.db at all — which
also means `scripts/verify_starters.py` has nothing to score and the feature
store has no current-season rows.

Two passes, in this order:

1. **backfill, season-level forced + `--only-ended`** — refreshes the season's
   schedule (games_by_season across all tournament phases + standings), then
   fetches per-game endpoints for games that have `ended=1` and aren't already
   cached.

   The force is scoped to `{games_by_season, standings}` and is **load-bearing,
   not an optimization**: sync_state marks those `success` on first fetch and
   then skips the network forever, so without forcing them this pass would
   never learn that last night's games now have `ended=1` — the `games` table
   would stay frozen at whatever the schedule looked like the first time it
   ran, and the per-game step below would keep re-examining the same finished
   set for the rest of the season. Per-game endpoints are deliberately NOT
   forced: a blind bulk `game_detail` refetch is unsafe (it can lose event
   data — see docs/RESYNC.md), and pass 2 is the guarded path for that.

   `--only-ended` matters on a live season for the mirror-image reason: a
   not-yet-played game returns a pre-game shell, raw_cache records it
   `success`, and sync_state then skips that game forever — so its real
   post-game data would never arrive. See backfill.py's `--only-ended` help.
2. **resync --days N** — refetches recently-completed games through the
   no-shrink guard, because liiga.fi keeps changing a completed game's data
   after first fetch (both directions — see docs/RESYNC.md). This is what
   picks up retroactive enrichment such as the thin `game_puck_control` /
   `shot_events` rows a game often has on the night it's played.

Pass 1 gets a game in; pass 2 keeps correcting it for `--days` afterwards.
The two are complementary — running only pass 1 leaves every game frozen at
whatever liiga.fi happened to be serving minutes after the final whistle.

Usage (from repo root, venv active):

    python scripts/nightly_sync.py
    python scripts/nightly_sync.py --season 2027 --days 7
    python scripts/nightly_sync.py --dry-run

Logs to `logs/nightly_sync.log`, plus stdout when run from a terminal. The
scheduled task runs pythonw.exe, where sys.stdout/sys.stderr are None and the
log file is the only record — main() therefore logs any otherwise-unhandled
exception before letting it exit non-zero. Exits non-zero if either pass
fails, so a scheduler can surface it. Registered with schtasks rather than by
this repo — see docs/RESYNC.md's nightly-sync section.
"""

import argparse
import logging
import sys
from pathlib import Path

from hockey_edge.ingest import db
from hockey_edge.ingest.liiga import backfill, resync

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = REPO_ROOT / "logs"

# The live season. Liiga's season numbering is ending-year (season=2027 is
# 2026-27), confirmed via scripts/season_probe.py -- see CLAUDE.md.
DEFAULT_SEASON = 2027

# Comfortably longer than any plausible gap between scheduled runs, so a few
# missed nights (machine asleep, per the Task Scheduler setup) still get
# caught up rather than silently leaving a game frozen at its first fetch.
DEFAULT_DAYS = 7

# Refetched every run regardless of sync_state -- see pass 1 in the module
# docstring for why this is required rather than merely nice to have.
# Deliberately excludes game_detail/game_stats/shotmap: forcing those in bulk
# is the unsafe operation docs/RESYNC.md warns about.
SEASON_LEVEL_ENDPOINTS = {"games_by_season", "standings"}

logger = logging.getLogger("hockey_edge.nightly_sync")


def _configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Configure the root logger so the ingest modules' own loggers
    # ("hockey_edge.ingest", "hockey_edge.ingest.resync") propagate into this
    # file too -- their _configure_logging() is never called here, since this
    # calls their functions directly rather than their main().
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        file_handler = logging.FileHandler(LOG_DIR / "nightly_sync.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
        # Only when there is somewhere to write: the scheduled task runs under
        # pythonw, where sys.stdout is None and a StreamHandler holding it
        # raises (silently, swallowed by Handler.handleError) once per record.
        # Run from a terminal, this still echoes as before.
        if sys.stdout is not None:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(fmt)
            root.addHandler(console_handler)


def main() -> None:
    # Logging is configured before anything that can fail, so that the window
    # in which a failure cannot be recorded stays as small as it can be. That
    # window is not empty: an error raised while importing this module, or
    # inside _configure_logging itself (an unwritable logs/ directory, say),
    # happens with no logger and -- under pythonw -- no stderr either, leaving
    # only Task Scheduler's non-zero Last Run Result.
    _configure_logging()
    try:
        _main()
    except SystemExit:
        # _main's deliberate exit(1), and argparse's exit(2) on a bad
        # invocation: both already said what they needed to.
        raise
    except BaseException:
        logger.critical("nightly sync aborted with an unhandled exception", exc_info=True)
        raise


def _main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON,
                        help=f"live season to sync (default {DEFAULT_SEASON})")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"resync window in days (default {DEFAULT_DAYS})")
    parser.add_argument(
        "--min-hours-since-fetch", type=float, default=resync.DEFAULT_MIN_HOURS_SINCE_FETCH,
        help="skip a (game, endpoint) refetched more recently than this "
        f"(default {resync.DEFAULT_MIN_HOURS_SINCE_FETCH})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report what the resync pass would check, zero HTTP requests. The backfill pass is "
        "SKIPPED entirely under --dry-run (backfill.py has no dry-run mode of its own).",
    )
    args = parser.parse_args()

    logger.info(
        "nightly sync starting: season=%s days=%s dry_run=%s", args.season, args.days, args.dry_run
    )

    ok = True
    conn = db.get_connection()
    try:
        if args.dry_run:
            logger.info("dry-run: skipping backfill pass (no dry-run mode available for it)")
        else:
            try:
                summary = backfill.backfill_season(
                    conn, args.season,
                    force=True, force_endpoints=SEASON_LEVEL_ENDPOINTS,
                    only_ended=True,
                )
                logger.info("pass 1 (backfill) complete: %s", summary)
            except Exception:
                logger.critical("pass 1 (backfill) FAILED for season=%s", args.season, exc_info=True)
                ok = False

        try:
            summary = resync.run_resync(
                conn,
                days=args.days,
                endpoints=list(resync.RESYNC_ENDPOINTS),
                min_hours_since_fetch=args.min_hours_since_fetch,
                dry_run=args.dry_run,
            )
            logger.info("pass 2 (resync) complete: %s", summary)
        except Exception:
            logger.critical("pass 2 (resync) FAILED", exc_info=True)
            ok = False
    finally:
        conn.close()

    if not ok:
        logger.critical("nightly sync finished WITH FAILURES — see above")
        sys.exit(1)
    logger.info("nightly sync finished ok")


if __name__ == "__main__":
    main()
