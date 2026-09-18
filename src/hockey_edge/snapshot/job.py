"""Snapshot capture job — Layer 2 per docs/DATA_PIPELINE.md.

Scheduler is dumb, job is smart: an external scheduler fires this every 15
minutes and the job itself decides whether anything is due. Registered as a
Windows scheduled task on the desktop since 2026-09-02, running 11:00–23:00
local (see docs/snapshot_job_task_scheduler.xml for the definition, the
registration command, and why the window starts at 11:00 rather than 14:00).
docs/snapshot_job_launchd.plist and docs/snapshot_job_cron.txt remain
scaffolding for other hosts, not deployed. Run manually or via the scheduler:

    python -m hockey_edge.snapshot.job --once       # normal pass
    python -m hockey_edge.snapshot.job --dry-run     # zero HTTP requests

Each --once pass: discover live fixtures (games_by_date -- see fixtures.py),
mark any windows that genuinely passed uncaptured as 'missed', work out which
(season, game_id, kind, window) capture windows are due right now, and — if
any odds window is due, eligible under ODDS_RETRY_GAP, and the monthly
OddsPapi ceiling allows it — poll the board once per book in ODDS_BOOKS (one
poll covers every posted fixture for one book; see docs/DATA_PIPELINE.md's
OddsPapi section).
Lineup capture (see lineups.py) is wired: one game_detail(+game_preview)
fetch per due (season, game_id), regardless of window -- opening/mid windows
mostly return unassigned squads (line=null everywhere) and that's expected,
captured anyway as evidence of when lineups actually post; the closing
window (T-25min, closest to the T-30 test that unblocked this) is where a
real confirmed lineup shows up. capture_lineups re-queries due windows
itself rather than reusing the `due` list computed for odds above, so it
stays correct regardless of whether odds capture already marked some of
those windows satisfied this pass.

Odds capture runs OddsPapiProvider against ODDS_BOOKS (wired 2026-09-17,
docs/ODDS_PLAN.md — it replaced NullOddsProvider, which is kept in
odds/null_provider.py as the zero-request stand-in). An odds window is
satisfied for a game that any book in ODDS_BOOKS priced and that resolved to
a liiga.fi game, with the books recorded in capture_windows.satisfied_by —
books post fixtures independently, and PRIMARY_ODDS_BOOK is sometimes late
or absent on a game another book already prices (KooKoo–SaiPa, 2026-09-18).
A game on no board stays pending and is retried no sooner than
ODDS_RETRY_GAP, so one unposted fixture can't drain the monthly budget.
Odds and lineup windows are tracked separately (capture_windows.kind): they
succeed independently, and a shared status used to let a successful odds
poll hide windows from lineup capture.

Alerting: failures log at CRITICAL to logs/snapshot_job.log, plus the console
when run from a terminal. The scheduled task runs pythonw.exe, where
sys.stdout/sys.stderr are None and the log file is the only record — main()
therefore logs any otherwise-unhandled exception before letting it exit
non-zero. There's no email/Slack hookup yet (open item) — until one exists, a
CRITICAL line in the log is the signal to check in manually. A missed capture
is gone forever, so this job must never silently swallow a failure, and --once
exits non-zero on any failure so a scheduler can surface it.
"""

import argparse
import logging
import sqlite3
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from hockey_edge.snapshot import fixtures, lineups, storage
from hockey_edge.snapshot.odds.base import OddsProvider, OddsSnapshot
from hockey_edge.snapshot.odds.oddspapi import OddsPapiProvider, load_team_map

LOG_DIR = Path(__file__).resolve().parents[3] / "logs"

# OddsPapi free tier is 250 req/mo; this leaves headroom for a bug not to
# blow the whole tier overnight. Currently moot with NullOddsProvider (zero
# real requests ever recorded) but enforced regardless so it's already
# correct once a real provider is wired.
ODDS_MONTHLY_CEILING = 200

# One poll per book per tick. Pinnacle is primary: the sharpest of the books
# checked, and the benchmark line validation is meant to use. bet365 is the
# second opinion (docs/ODDS_PLAN.md Phase 0 — Betsson failed those checks) and
# the fallback: either book pricing a game satisfies its window, and
# capture_windows.satisfied_by records which did.
ODDS_BOOKS = ("pinnacle", "bet365")
PRIMARY_ODDS_BOOK = "pinnacle"

# A board poll covers every fixture posted, so any successful poll is an
# attempt on every window due at that moment. A window still due afterwards
# is one whose fixture isn't posted yet, and re-polling for it every 15
# minutes would burn ~80 requests on a single unposted game between its
# opening window (T-24h) and puck drop. Retry no sooner than this -- except
# for the closing window, where a late-posted price is the whole point and a
# missed capture is unrecoverable.
ODDS_RETRY_GAP = timedelta(minutes=60)

# Gap between the per-book polls of a single tick. OddsPapi rate-limits bursts
# independently of the monthly quota: on the first live run (2026-09-17 21:30
# local) two calls ~1s apart got the second one HTTP 429'd (pinnacle 200,
# bet365 429), while Phase 0's two calls 1.5s apart both returned 200 -- so the
# threshold sits near a second. A 429 still bills as a request while returning
# nothing, so this is deliberately far clear of that line rather than
# minimal: the job ticks every 15 minutes, so the wait costs nothing real, and
# a retry is not an option (it would be another billed request).
# If a 429 ever recurs anyway, the next steps are: raise this; then stagger the
# books across consecutive ticks (same request count, no burst, at the cost of
# the two books' prices being 15 min apart); then drop to PRIMARY_ODDS_BOOK
# alone (halves the monthly spend, loses the second opinion).
ODDS_BOOK_DELAY_SECONDS = 20.0


def _configure_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("hockey_edge.snapshot")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        # encoding is explicit: the scheduled task runs under pythonw with no
        # console, so the locale default would be cp1252 and a record carrying
        # a name like Kärpät/Jyväskylä would raise inside emit and be dropped
        # with nothing to surface it.
        file_handler = logging.FileHandler(LOG_DIR / "snapshot_job.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
        # Only when there is somewhere to write: under pythonw sys.stderr is
        # None, and a StreamHandler holding a None stream raises (silently,
        # swallowed by Handler.handleError) once per record. Run from a
        # terminal, this still echoes as before.
        if sys.stderr is not None:
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setFormatter(fmt)
            logger.addHandler(console_handler)
    return logger


def _now_iso() -> str:
    # Must match fixtures._iso_utc's format ('Z'-suffixed, no microseconds):
    # this value gets compared via plain SQL string ordering against
    # capture_windows.due_at and discovered_fixtures.start_utc (both in that
    # format) in storage.get_due_windows/mark_missed_windows — a mismatched
    # suffix (e.g. Python's default '+00:00' isoformat()) sorts differently
    # at exact-tie instants and would silently misjudge windows at the
    # boundary.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# A board fixture may resolve to a discovered game whose start differs by at
# most this much -- enough for a reschedule within the day, small enough that
# a back-to-back with home/away swapped (KooKoo–SaiPa 2026-09-18/19) can never
# match the wrong game, since the home/away pair must match too.
RESOLVE_MAX_START_DIFF = timedelta(hours=24)


def windows_eligible_for_poll(
    due: list, *, last_poll_iso: str | None, now_iso: str
) -> list:
    """Of the due odds windows, those worth spending a poll on right now: a
    window whose fixture was already looked for on a successful poll less
    than ODDS_RETRY_GAP ago waits. 'Looked for' means the poll happened at or
    after the window came due -- an earlier poll says nothing about it.
    Closing windows are always eligible (see ODDS_RETRY_GAP)."""
    if last_poll_iso is None:
        return list(due)
    retry_from = _parse_utc(now_iso) - ODDS_RETRY_GAP
    recent = _parse_utc(last_poll_iso) > retry_from
    return [
        row for row in due
        if row["window"] == "closing" or not (recent and last_poll_iso >= row["due_at"])
    ]


def resolve_snapshots(
    conn: sqlite3.Connection,
    snapshots: list[OddsSnapshot],
    team_map: dict[int, str],
    logger: logging.Logger,
) -> list[OddsSnapshot]:
    """Attach (season, game_id) to each snapshot whose fixture maps to exactly
    one discovered liiga.fi game: both participants known in the curated team
    map, same home/away teamIds, start within RESOLVE_MAX_START_DIFF.
    Anything else stays unresolved (NULL) with the reason logged -- never
    guessed. Resolved once per fixture, not per market row."""
    resolved_by_fixture: dict[str, tuple[int, int] | None] = {}
    out = []
    for snapshot in snapshots:
        if snapshot.fixture_ref not in resolved_by_fixture:
            resolved_by_fixture[snapshot.fixture_ref] = _resolve_fixture(conn, snapshot, team_map, logger)
        match = resolved_by_fixture[snapshot.fixture_ref]
        if match is not None:
            snapshot = replace(snapshot, season=match[0], game_id=match[1])
        out.append(snapshot)
    return out


def _resolve_fixture(conn, snapshot: OddsSnapshot, team_map: dict[int, str], logger) -> tuple[int, int] | None:
    home = team_map.get(snapshot.home_participant_id)
    away = team_map.get(snapshot.away_participant_id)
    if home is None or away is None:
        logger.warning(
            "odds resolve: fixture %s has participant(s) not in the curated team map "
            "(home=%s away=%s) — left unresolved; add them to oddspapi_teams.json with evidence",
            snapshot.fixture_ref,
            snapshot.home_participant_id if home is None else "ok",
            snapshot.away_participant_id if away is None else "ok",
        )
        return None
    if snapshot.start_utc is None:
        logger.warning("odds resolve: fixture %s has no startTime — left unresolved", snapshot.fixture_ref)
        return None
    start = _parse_utc(snapshot.start_utc)
    candidates = [
        row for row in storage.find_liiga_games(conn, home_team_id=home, away_team_id=away)
        if abs(_parse_utc(row["start_utc"]) - start) <= RESOLVE_MAX_START_DIFF
    ]
    if len(candidates) == 1:
        return candidates[0]["season"], candidates[0]["game_id"]
    logger.warning(
        "odds resolve: fixture %s (%s v %s, %s) matched %d discovered game(s) — left unresolved",
        snapshot.fixture_ref, home, away, snapshot.start_utc, len(candidates),
    )
    return None


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def capture_odds(
    provider: OddsProvider,
    conn: sqlite3.Connection,
    logger: logging.Logger,
    *,
    book: str = "pinnacle",
) -> list:
    """Call the provider once, record an api_usage row (skipped for a stub
    provider — see OddsProvider.is_stub), resolve and write any snapshots.
    Returns the snapshots so the caller can decide which due windows they
    satisfy."""
    from hockey_edge.snapshot.odds.oddspapi import LIIGA_TOURNAMENT_ID

    requested_at = _now_iso()
    try:
        snapshots = provider.fetch_odds(tournament_ref=LIIGA_TOURNAMENT_ID, book=book)
    except Exception:
        logger.critical(
            "odds capture FAILED (provider=%s) — this poll's data is unrecoverable",
            provider.name,
            exc_info=True,
        )
        if not provider.is_stub:
            storage.insert_api_usage(
                conn, provider=provider.name, endpoint="odds-by-tournaments",
                requested_at=requested_at, http_status=None, outcome="failure",
            )
        return []

    if not provider.is_stub:
        storage.insert_api_usage(
            conn, provider=provider.name, endpoint="odds-by-tournaments",
            requested_at=requested_at, http_status=200,
            outcome="success_content" if snapshots else "success_empty",
        )

    if snapshots:
        snapshots = resolve_snapshots(conn, snapshots, load_team_map(), logger)
    for snapshot in snapshots:
        storage.insert_odds_snapshot(conn, snapshot)

    logger.info(
        "odds capture ok (provider=%s): %d snapshot(s) written",
        provider.name,
        len(snapshots),
    )
    return snapshots


def capture_odds_for_due_windows(
    conn: sqlite3.Connection, logger: logging.Logger, *, due: list, now_iso: str
) -> int:
    """Poll every book once (ODDS_BOOKS) if any due window is eligible and the
    monthly ceiling allows it, then mark satisfied the windows whose game got
    a parsed, resolved row from ANY book, recording which books in
    capture_windows.satisfied_by. A game on no board stays pending — it is not
    a capture. A game only a fallback book priced is captured, but logged at
    WARNING, since it has no benchmark (PRIMARY_ODDS_BOOK) line. Returns the
    number of windows marked satisfied."""
    eligible = windows_eligible_for_poll(
        due,
        last_poll_iso=storage.last_successful_odds_poll(conn, provider="oddspapi"),
        now_iso=now_iso,
    )
    if not eligible:
        logger.info(
            "%d odds window(s) due but all polled within the last %d min and still "
            "unposted — waiting (see ODDS_RETRY_GAP)",
            len(due), int(ODDS_RETRY_GAP.total_seconds() // 60),
        )
        return 0

    used = storage.count_api_usage_this_month(conn, provider="oddspapi", now_iso=now_iso)
    if used + len(ODDS_BOOKS) > ODDS_MONTHLY_CEILING:
        logger.critical(
            "oddspapi monthly ceiling would be exceeded (%d used + %d books > %d) — "
            "skipping odds capture for %d due window(s) this pass",
            used, len(ODDS_BOOKS), ODDS_MONTHLY_CEILING, len(due),
        )
        return 0

    provider = OddsPapiProvider()
    # (season, game_id) -> books that priced it this poll, in ODDS_BOOKS order.
    covered: dict[tuple[int, int], list[str]] = {}
    for index, book in enumerate(ODDS_BOOKS):
        if index:
            time.sleep(ODDS_BOOK_DELAY_SECONDS)
        for s in capture_odds(provider, conn, logger, book=book):
            if s.parsed and s.game_id is not None:
                books = covered.setdefault((s.season, s.game_id), [])
                if book not in books:
                    books.append(book)

    satisfied_by: dict[str, list] = {}
    for row in due:
        books = covered.get((row["season"], row["game_id"]))
        if books:
            satisfied_by.setdefault(",".join(books), []).append(row)
    now = _now_iso()
    for books, rows in satisfied_by.items():
        storage.mark_windows_satisfied(conn, [r["id"] for r in rows], now_iso=now, satisfied_by=books)

    satisfied = [row for rows in satisfied_by.values() for row in rows]
    without_primary = [
        row for books, rows in satisfied_by.items()
        if PRIMARY_ODDS_BOOK not in books.split(",") for row in rows
    ]
    satisfied_ids = {row["id"] for row in satisfied}
    uncovered = [row for row in due if row["id"] not in satisfied_ids]
    logger.info(
        "odds capture: %d/%d due window(s) satisfied (%s); %d left pending%s",
        len(satisfied), len(due),
        ", ".join(f"{books}: {len(rows)}" for books, rows in satisfied_by.items()) or "none",
        len(uncovered),
        "".join(
            f" (season={r['season']} game_id={r['game_id']} window={r['window']})"
            for r in uncovered
        ),
    )
    if without_primary:
        # Not a failure -- the window is captured -- but the benchmark line
        # is missing for these games, which validation will want to know.
        logger.warning(
            "odds capture: %d window(s) satisfied WITHOUT %s (fallback book only):%s",
            len(without_primary), PRIMARY_ODDS_BOOK,
            "".join(
                f" (season={r['season']} game_id={r['game_id']} window={r['window']})"
                for r in without_primary
            ),
        )
    return len(satisfied)


def capture_lineups(conn: sqlite3.Connection, logger: logging.Logger) -> int:
    """One game_detail(+game_preview) fetch per distinct due (season,
    game_id), writing a LineupSnapshot per team-side and marking that game's
    due windows satisfied. A single game's fetch failure is logged at
    CRITICAL (missed capture is unrecoverable, same as odds) and does not
    stop the other due games this pass. Returns the number of windows
    marked satisfied."""
    now_iso = _now_iso()
    due = storage.get_due_windows(conn, kind="lineups", now_iso=now_iso)
    if not due:
        logger.info("lineup capture: no windows currently due")
        return 0

    games: dict[tuple[int, int], list] = {}
    for row in due:
        games.setdefault((row["season"], row["game_id"]), []).append(row)

    satisfied = 0
    for (season, game_id), rows in games.items():
        window_label = ",".join(sorted({row["window"] for row in rows}))
        try:
            snapshots = lineups.fetch_lineups(season, game_id, window=window_label)
        except Exception:
            logger.critical(
                "lineup capture FAILED for season=%s game_id=%s (window=%s) — "
                "this poll's lineup data is unrecoverable",
                season, game_id, window_label, exc_info=True,
            )
            continue

        for snapshot in snapshots:
            storage.insert_lineup_snapshot(conn, snapshot)

        window_ids = [row["id"] for row in rows]
        storage.mark_windows_satisfied(conn, window_ids, now_iso=_now_iso())
        satisfied += len(window_ids)
        logger.info(
            "lineup capture ok: season=%s game_id=%s (window=%s) — %d snapshot(s) "
            "written, %d window(s) marked satisfied",
            season, game_id, window_label, len(snapshots), len(window_ids),
        )

    return satisfied


def run_dry_run(conn: sqlite3.Connection, logger: logging.Logger) -> None:
    """Zero HTTP requests, zero DB writes: report which windows are due (and
    which would be recorded missed) against whatever's already in
    snapshots.db from a prior --once pass. This is how the schedule gets
    verified without spending any budget."""
    now_iso = _now_iso()
    logger.info("dry-run: evaluating capture schedule as of %s (no HTTP, no writes)", now_iso)

    for kind in storage.CAPTURE_KINDS:
        due = storage.get_due_windows(conn, kind=kind, now_iso=now_iso)
        if due:
            logger.info("dry-run: %d %s window(s) currently due:", len(due), kind)
            for row in due:
                logger.info(
                    "  would attempt: kind=%s season=%s game_id=%s window=%s due_at=%s game_start=%s",
                    kind, row["season"], row["game_id"], row["window"], row["due_at"],
                    row["game_start_utc"],
                )
        else:
            logger.info("dry-run: no %s windows currently due", kind)

    conn.row_factory = sqlite3.Row
    would_miss = conn.execute(
        """
        SELECT cw.*, latest.start_utc AS game_start_utc
        FROM capture_windows cw
        JOIN (
            SELECT season, game_id, MAX(start_utc) AS start_utc
            FROM discovered_fixtures GROUP BY season, game_id
        ) latest ON latest.season = cw.season AND latest.game_id = cw.game_id
        WHERE cw.status = 'pending' AND cw.due_at <= ? AND latest.start_utc <= ?
        """,
        (now_iso, now_iso),
    ).fetchall()
    if would_miss:
        logger.info("dry-run: %d window(s) would be recorded MISSED on the next --once pass:", len(would_miss))
        for row in would_miss:
            logger.info(
                "  would miss: kind=%s season=%s game_id=%s window=%s due_at=%s game_start=%s",
                row["kind"], row["season"], row["game_id"], row["window"], row["due_at"],
                row["game_start_utc"],
            )

    due_odds = storage.get_due_windows(conn, kind="odds", now_iso=now_iso)
    eligible = windows_eligible_for_poll(
        due_odds,
        last_poll_iso=storage.last_successful_odds_poll(conn, provider="oddspapi"),
        now_iso=now_iso,
    )
    used = storage.count_api_usage_this_month(conn, provider="oddspapi", now_iso=now_iso)
    projected = len(ODDS_BOOKS) if eligible else 0
    logger.info(
        "dry-run: %d odds window(s) due, %d eligible to poll now — a --once pass would "
        "make %d request(s) (%s)",
        len(due_odds), len(eligible), projected, ", ".join(ODDS_BOOKS) if projected else "none",
    )
    logger.info("dry-run: oddspapi usage this month: %d / %d ceiling", used, ODDS_MONTHLY_CEILING)


def run_once(conn: sqlite3.Connection, logger: logging.Logger) -> bool:
    """Normal pass. Returns True on success (no unhandled failure)."""
    now_iso = _now_iso()
    ok = True

    try:
        fixtures.discover_fixtures(conn)
    except Exception:
        logger.critical("fixture discovery FAILED", exc_info=True)
        ok = False

    now_iso = _now_iso()
    missed = storage.mark_missed_windows(conn, now_iso=now_iso)
    for row in missed:
        logger.warning(
            "capture window MISSED (genuinely passed uncaptured): "
            "kind=%s season=%s game_id=%s window=%s due_at=%s game_start=%s",
            row["kind"], row["season"], row["game_id"], row["window"], row["due_at"],
            row["game_start_utc"],
        )

    due = storage.get_due_windows(conn, kind="odds", now_iso=now_iso)
    if due:
        try:
            capture_odds_for_due_windows(conn, logger, due=due, now_iso=now_iso)
        except Exception:
            logger.critical("odds capture FAILED", exc_info=True)
            ok = False
    else:
        logger.info("no odds capture windows due this pass")

    try:
        capture_lineups(conn, logger)
    except Exception:
        logger.critical("lineup capture FAILED", exc_info=True)
        ok = False

    return ok


def main() -> None:
    # Logging is configured before anything that can fail, so that the window
    # in which a failure cannot be recorded stays as small as it can be. That
    # window is not empty: an error raised while importing this module, or
    # inside _configure_logging itself (an unwritable logs/ directory, say),
    # happens with no logger and — under pythonw — no stderr either, leaving
    # only Task Scheduler's non-zero Last Run Result.
    logger = _configure_logging()
    try:
        parser = argparse.ArgumentParser()
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--once", action="store_true", help="Run a normal single pass.")
        mode.add_argument(
            "--dry-run", action="store_true",
            help="Zero HTTP requests, zero writes: report which windows are due.",
        )
        args = parser.parse_args()

        load_dotenv()
        conn = storage.get_connection()
        try:
            if args.dry_run:
                run_dry_run(conn, logger)
            else:
                ok = run_once(conn, logger)
                if not ok:
                    sys.exit(1)
        finally:
            conn.close()
    except SystemExit:
        # --once's deliberate exit(1), and argparse's exit(2) on a bad
        # invocation: both already said what they needed to.
        raise
    except BaseException:
        logger.critical("snapshot job aborted with an unhandled exception", exc_info=True)
        raise


if __name__ == "__main__":
    main()
