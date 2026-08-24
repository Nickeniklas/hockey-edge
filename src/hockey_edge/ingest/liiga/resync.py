"""Time-windowed re-sync for recently-completed Liiga games — Phase 4c.

Separate from `backfill.py` (a season-scoped, one-time-per-season pass) and
separate from `hockey_edge.snapshot.job` (the pre-puck-drop capture path
against snapshots.db) — different database, different failure modes,
different leakage rules. This runs against `data/hockey.db` only, on games
that have already ended.

Why this exists: `sync_state` skips the network entirely once a row is
`status='success'`, so staleness was structurally undetectable — and
liiga.fi does not treat a completed game's data as final. Confirmed
2026-08-24 (docs/RESYNC.md has the full writeup): refetching season=2024
game_id=1 across all three per-game endpoints showed **all three** return
different content on refetch, not just game_stats as originally assumed —
and not always in the direction of more data. `game_detail`'s
goalkeeper-event arrays lost real rows on refetch for that game (4->1 and
3->0 entries). The no-shrink guard below exists specifically because of that
finding: reparsing a refetched response into curated tables never leaves
those tables with fewer rows than they already had, for any endpoint.

Usage (from repo root, venv active):

    python -m hockey_edge.ingest.liiga.resync --days 7
    python -m hockey_edge.ingest.liiga.resync --days 7 --dry-run
    python -m hockey_edge.ingest.liiga.resync --days 7 --endpoints game_stats

Staleness is derived from `sync_state.fetched_at` vs. `games.start_utc` —
two columns that already exist — rather than a new `last_verified_at`
column, so this needed no DDL change.
"""

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hockey_edge.ingest import db, raw_cache
from hockey_edge.ingest.liiga import parsers
from hockey_edge.ingest.liiga.endpoints import ENDPOINTS

RESYNC_ENDPOINTS = ("game_detail", "game_stats", "shotmap")

DEFAULT_DAYS = 7
DEFAULT_MIN_HOURS_SINCE_FETCH = 12.0

# (game_id, season)-scoped tables each endpoint's upsert rewrites via
# delete-and-reinsert -- what the no-shrink guard protects. `players` (also
# written by game_detail's upsert) is deliberately excluded: it's a global
# upsert-by-player_id table, not a per-game delete+reinsert, so it can't
# "shrink" in the row-count sense this guard checks, and letting a player's
# bio fields (handedness, weight, ...) update from a corrected fetch is
# desirable regardless of what happens with this game's event tables.
GUARDED_TABLES = {
    "game_detail": ["game_rosters", "game_penalty_events", "game_goalkeeper_events"],
    "game_stats": [
        "game_team_period_stats", "game_player_period_stats",
        "game_goalie_period_stats", "game_puck_control",
    ],
    "shotmap": ["shot_events"],
}

logger = logging.getLogger("hockey_edge.ingest.resync")


def _configure_logging() -> None:
    # Separate log file from backfill.py and from the snapshot job -- three
    # different passes, three different failure modes, kept visibly apart.
    log_dir = Path(__file__).resolve().parents[4] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "liiga_resync.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(dt: datetime) -> str:
    # Must match how games.start_utc is stored (liiga.fi's own 'Z'-suffixed,
    # no-microseconds format) since find_resync_candidates compares this via
    # plain SQL string ordering -- a mismatched suffix sorts differently at
    # exact-tie instants (same lesson as hockey_edge.snapshot.fixtures).
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_resync_candidates(conn: sqlite3.Connection, *, days: int) -> list[tuple[int, int]]:
    """Games with ended=1 and start_utc within the last `days` days. Returns
    (game_id, season) pairs, most recent first."""
    cutoff = _iso_utc(_now() - timedelta(days=days))
    rows = conn.execute(
        "SELECT game_id, season FROM games WHERE ended = 1 AND start_utc >= ? ORDER BY start_utc DESC",
        (cutoff,),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _get_sync_state(conn: sqlite3.Connection, endpoint_name: str, season: int, game_id: int):
    entity_id = f"{season}:{game_id}"
    return conn.execute(
        "SELECT fetched_at, content_hash FROM sync_state WHERE league = 'liiga' AND endpoint = ? AND entity_id = ?",
        (endpoint_name, entity_id),
    ).fetchone()


def _is_due(conn: sqlite3.Connection, endpoint_name: str, season: int, game_id: int, *, min_hours_since_fetch: float) -> bool:
    """Never fetched -> due. Otherwise due once its last fetch is older than
    min_hours_since_fetch. This is what keeps a 15-minute-cadence scheduler
    from refetching the same completed game dozens of times across an N-day
    window -- the outer schedule can be dumb (fire every 15 min) because
    this check, not the schedule, decides what's actually stale."""
    row = _get_sync_state(conn, endpoint_name, season, game_id)
    if row is None:
        return True
    fetched_at = datetime.fromisoformat(row[0])
    age_hours = (_now() - fetched_at).total_seconds() / 3600
    return age_hours >= min_hours_since_fetch


def _count(conn: sqlite3.Connection, table: str, game_id: int, season: int) -> int:
    return conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE game_id = ? AND season = ?", (game_id, season)
    ).fetchone()[0]


def _snapshot_table(conn: sqlite3.Connection, table: str, game_id: int, season: int) -> list[sqlite3.Row]:
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    return cur.execute(
        f"SELECT * FROM {table} WHERE game_id = ? AND season = ?", (game_id, season)
    ).fetchall()


def _restore_table(conn: sqlite3.Connection, table: str, game_id: int, season: int, rows: list[sqlite3.Row]) -> None:
    conn.execute(f"DELETE FROM {table} WHERE game_id = ? AND season = ?", (game_id, season))
    if rows:
        columns = rows[0].keys()
        conn.executemany(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
            [tuple(r) for r in rows],
        )
    conn.commit()


def reparse_with_shrink_guard(conn: sqlite3.Connection, *, game_id: int, season: int, endpoint_name: str, do_upsert) -> bool:
    """Runs do_upsert() (a zero-arg callable performing the real parse+upsert,
    which commits internally same as backfill.py's flow), then compares
    per-table row counts before vs. after for every table this endpoint
    guards. If any table would end up with fewer rows, restores every
    guarded table for this game to its pre-upsert state (exact row-for-row,
    including original ids) and logs a WARNING with before/after counts per
    table. Compares per-table totals, not per-team split -- an event moving
    between the home/away arrays (seen in the 2026-08-24 finding) is a
    legitimate correction and must not trip this guard. Returns True if the
    reparse was applied, False if it was reverted."""
    tables = GUARDED_TABLES[endpoint_name]
    before = {t: _count(conn, t, game_id, season) for t in tables}
    backups = {t: _snapshot_table(conn, t, game_id, season) for t in tables}

    do_upsert()

    after = {t: _count(conn, t, game_id, season) for t in tables}
    shrinking = {t: (before[t], after[t]) for t in tables if after[t] < before[t]}

    if shrinking:
        for t in tables:
            _restore_table(conn, t, game_id, season, backups[t])
        logger.warning(
            "season=%s game_id=%s endpoint=%s: reparse SKIPPED, would shrink curated table(s) "
            "(table: before->after): %s -- raw response saved to disk regardless; existing "
            "curated rows kept untouched",
            season, game_id, endpoint_name, shrinking,
        )
        return False

    logger.info(
        "season=%s game_id=%s endpoint=%s: reparse applied, row counts: %s",
        season, game_id, endpoint_name, after,
    )
    return True


def resync_game_endpoint(
    conn: sqlite3.Connection, season: int, game_id: int, endpoint_name: str, *, min_hours_since_fetch: float
) -> str:
    """Returns one of: 'skipped_not_due', 'fetch_failed', 'unchanged',
    'reparsed', 'shrink_guarded'."""
    if not _is_due(conn, endpoint_name, season, game_id, min_hours_since_fetch=min_hours_since_fetch):
        return "skipped_not_due"

    endpoint = ENDPOINTS[endpoint_name]
    entity_id = f"{season}:{game_id}"
    url = endpoint.url_template.format(season=season, game_id=game_id)

    prior = _get_sync_state(conn, endpoint_name, season, game_id)
    prior_hash = prior[1] if prior else None

    # force=True: bypass sync_state's normal "already success, skip network"
    # shortcut -- that shortcut is exactly the bug this module exists to
    # work around. raw_cache.fetch still writes the raw file + sync_state +
    # raw_responses unconditionally on a real fetch, satisfying "still write
    # the new raw response to disk" regardless of what happens next.
    result = raw_cache.fetch(conn, endpoint, entity_id, url, season=season, force=True)
    if result.status != "success":
        return "fetch_failed"

    new_row = _get_sync_state(conn, endpoint_name, season, game_id)
    new_hash = new_row[1] if new_row else None

    if prior_hash is not None and prior_hash == new_hash:
        return "unchanged"  # content hash decides whether to reparse: no curated write at all

    if endpoint_name == "game_detail":
        parsed = parsers.parse_game_detail(result.data, game_id, season)
        applied = reparse_with_shrink_guard(
            conn, game_id=game_id, season=season, endpoint_name="game_detail",
            do_upsert=lambda: parsers.upsert_game_detail(conn, game_id, season, parsed),
        )
    elif endpoint_name == "game_stats":
        parsed = parsers.parse_game_stats(result.data, game_id, season)
        applied = reparse_with_shrink_guard(
            conn, game_id=game_id, season=season, endpoint_name="game_stats",
            do_upsert=lambda: parsers.upsert_game_stats(conn, game_id, season, parsed),
        )
    elif endpoint_name == "shotmap":
        rows = parsers.parse_shotmap(result.data, game_id, season)
        applied = reparse_with_shrink_guard(
            conn, game_id=game_id, season=season, endpoint_name="shotmap",
            do_upsert=lambda: parsers.upsert_shot_events(conn, game_id, season, rows),
        )
    else:
        raise ValueError(f"resync not implemented for endpoint {endpoint_name!r}")

    return "reparsed" if applied else "shrink_guarded"


def run_resync(
    conn: sqlite3.Connection, *, days: int, endpoints: list[str],
    min_hours_since_fetch: float, dry_run: bool,
) -> dict:
    candidates = find_resync_candidates(conn, days=days)
    logger.info(
        "resync: %d candidate game(s) with ended=1 in the last %d day(s), endpoints=%s, dry_run=%s",
        len(candidates), days, endpoints, dry_run,
    )

    outcome_counts: dict[str, dict[str, int]] = {}
    for game_id, season in candidates:
        for endpoint_name in endpoints:
            if dry_run:
                due = _is_due(conn, endpoint_name, season, game_id, min_hours_since_fetch=min_hours_since_fetch)
                outcome = "would_check" if due else "skipped_not_due"
            else:
                outcome = resync_game_endpoint(
                    conn, season, game_id, endpoint_name, min_hours_since_fetch=min_hours_since_fetch,
                )
            bucket = outcome_counts.setdefault(endpoint_name, {})
            bucket[outcome] = bucket.get(outcome, 0) + 1

    return {"days": days, "candidates": len(candidates), "dry_run": dry_run, "outcomes": outcome_counts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"re-sync games with ended=1 and start_utc within the last N days (default {DEFAULT_DAYS})")
    parser.add_argument(
        "--endpoints", type=str, default=",".join(RESYNC_ENDPOINTS),
        help="comma-separated subset of " + ",".join(RESYNC_ENDPOINTS),
    )
    parser.add_argument(
        "--min-hours-since-fetch", type=float, default=DEFAULT_MIN_HOURS_SINCE_FETCH,
        help=f"skip a (game, endpoint) whose last fetch is more recent than this many hours "
        f"(default {DEFAULT_MIN_HOURS_SINCE_FETCH}) -- throttles a 15-minute scheduler down to a "
        "sane refetch rate per game across the whole --days window",
    )
    parser.add_argument("--dry-run", action="store_true", help="report what would be checked, zero HTTP requests")
    args = parser.parse_args()

    endpoints = [e.strip() for e in args.endpoints.split(",") if e.strip()]
    unknown = set(endpoints) - set(RESYNC_ENDPOINTS)
    if unknown:
        parser.error(f"unknown endpoint(s): {sorted(unknown)}, must be from {RESYNC_ENDPOINTS}")

    _configure_logging()
    conn = db.get_connection()
    try:
        summary = run_resync(
            conn, days=args.days, endpoints=endpoints,
            min_hours_since_fetch=args.min_hours_since_fetch, dry_run=args.dry_run,
        )
        logger.info("resync complete: %s", summary)
        print(summary)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
