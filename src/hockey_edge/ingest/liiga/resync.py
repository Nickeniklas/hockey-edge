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
    python -m hockey_edge.ingest.liiga.resync --targets data/recovery/game_detail_targets.csv \
        --endpoints game_detail --min-hours-since-fetch 168 --outcomes data/recovery/game_detail_outcomes.csv

`--targets` (mutually exclusive with `--days`) takes a CSV with a required
`season,game_id` header and re-syncs exactly those games -- the historical
recovery path (docs/RECOVERY_BACKLOG.md). Listed games that are unknown or
not ended are logged at WARNING and skipped, never fatal. `--outcomes`
appends one `season,game_id,endpoint,outcome` row per checked pair, so a
resumed run adds to the record rather than overwriting it.

`--salvage-from-raw` (with `--targets`, `--endpoints game_detail`) makes zero
HTTP requests: it reparses each game's saved raw response and keeps a
guarded table's new rows only where that table had none before -- the
approved exception for games the guard refused (salvage_grow_from_zero).

Staleness is derived from `sync_state.fetched_at` vs. `games.start_utc` —
two columns that already exist — rather than a new `last_verified_at`
column, so this needed no DDL change.
"""

import argparse
import csv
import json
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

# The value guard (game_stats only, added 2026-09-25). The row-count guard
# above let through 3,953 reparses whose period stats liiga.fi had stripped
# (time on ice, corsi, faceoffs zeroed or null, power-play lists empty, some
# goals missing) with identical row counts. These are per-game totals
# compared before vs. after a reparse. A total that was above zero must not
# fall below VALUE_FLOOR of its old value: stripping takes a total to zero,
# while the largest real drop seen across 44 live 2027 refetches was corsi
# -12%. Backtest: refuses 3,767 of the 3,953 stripped reparses (the other
# 186 had no stored values to lose) and none of the 44 live refetches.
VALUE_FLOOR = 0.5
VALUE_TOTALS = {
    "player_toi": ("game_player_period_stats", "time_on_ice_seconds"),
    "player_corsi_for": ("game_player_period_stats", "corsi_for"),
    "player_faceoffs": ("game_player_period_stats", "faceoffs_total"),
    "goalie_toi": ("game_goalie_period_stats", "time_on_ice_seconds"),
    "team_face_off_wins": ("game_team_period_stats", "face_off_wins"),
    "team_powerplay_instances": ("game_team_period_stats", "powerplay_instances"),
    "team_shorthanded_instances": ("game_team_period_stats", "shorthanded_instances"),
}
# Goal totals must not move further from the final score. A plain drop rule
# would refuse real corrections (player goals went 7->6 and 8->7 live, both
# toward the final score); the team-period sum equals the final score in
# every 2022-2024 game.
GOAL_TOTALS = {
    "team_goals": ("game_team_period_stats", "goals"),
    "player_goals": ("game_player_period_stats", "goals"),
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


def load_targets(path: Path) -> list[tuple[int, int]]:
    """Reads a `season,game_id` CSV (header required). Returns (season,
    game_id) pairs in file order, duplicates dropped. A malformed row is
    logged at WARNING and skipped; a missing/wrong header raises ValueError,
    since then no row can be trusted."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = [h.strip() for h in next(reader, [])]
        if header[:2] != ["season", "game_id"]:
            raise ValueError(f"{path}: expected header 'season,game_id', got {header!r}")
        targets: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for line_no, row in enumerate(reader, start=2):
            if not any(cell.strip() for cell in row):
                continue
            try:
                pair = (int(row[0]), int(row[1]))
            except (IndexError, ValueError):
                logger.warning("%s line %d: bad target row %r, skipped", path, line_no, row)
                continue
            if pair not in seen:
                seen.add(pair)
                targets.append(pair)
    return targets


def find_target_candidates(conn: sqlite3.Connection, targets: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Listed (season, game_id) pairs that exist in `games` with ended=1, as
    (game_id, season) pairs in target-file order. Anything else is logged at
    WARNING and skipped."""
    candidates = []
    for season, game_id in targets:
        row = conn.execute(
            "SELECT ended FROM games WHERE game_id = ? AND season = ?", (game_id, season)
        ).fetchone()
        if row is None:
            logger.warning("target season=%s game_id=%s: not in games, skipped", season, game_id)
        elif row[0] != 1:
            logger.warning("target season=%s game_id=%s: not ended, skipped", season, game_id)
        else:
            candidates.append((game_id, season))
    return candidates


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


def game_stats_totals(conn: sqlite3.Connection, game_id: int, season: int) -> dict[str, float]:
    """Per-game totals for VALUE_TOTALS and GOAL_TOTALS. A NULL total (no
    rows, or every value null, as with stripped faceoffs) counts as 0."""
    return {
        name: conn.execute(
            f"SELECT COALESCE(SUM({column}), 0) FROM {table} WHERE game_id = ? AND season = ?", (game_id, season)
        ).fetchone()[0]
        for name, (table, column) in {**VALUE_TOTALS, **GOAL_TOTALS}.items()
    }


def _final_goals(conn: sqlite3.Connection, game_id: int, season: int) -> int | None:
    row = conn.execute(
        "SELECT home_goals + away_goals FROM games WHERE game_id = ? AND season = ?", (game_id, season)
    ).fetchone()
    return row[0] if row else None


def value_guard_violations(before: dict, after: dict, final_goals: int | None) -> dict[str, tuple]:
    """{total: (before, after[, final])} for every total the value guard
    refuses on; empty if the reparse may stand. Goal totals are skipped when
    the final score is unknown."""
    violations = {
        name: (before[name], after[name])
        for name in VALUE_TOTALS
        if before[name] > 0 and after[name] < VALUE_FLOOR * before[name]
    }
    if final_goals is not None:
        for name in GOAL_TOTALS:
            if abs(after[name] - final_goals) > abs(before[name] - final_goals):
                violations[name] = (before[name], after[name], final_goals)
    return violations


def reparse_with_shrink_guard(conn: sqlite3.Connection, *, game_id: int, season: int, endpoint_name: str, do_upsert) -> str:
    """Runs do_upsert() (a zero-arg callable performing the real parse+upsert,
    which commits internally same as backfill.py's flow), then compares
    per-table row counts before vs. after for every table this endpoint
    guards. If any table would end up with fewer rows, restores every
    guarded table for this game to its pre-upsert state (exact row-for-row,
    including original ids) and logs a WARNING with before/after counts per
    table. Compares per-table totals, not per-team split -- an event moving
    between the home/away arrays (seen in the 2026-08-24 finding) is a
    legitimate correction and must not trip this guard.

    For game_stats, a reparse that passes the row counts must also pass the
    value guard (value_guard_violations); a refusal restores the same way.
    Returns 'reparsed', 'shrink_guarded' or 'value_guarded'."""
    tables = GUARDED_TABLES[endpoint_name]
    before = {t: _count(conn, t, game_id, season) for t in tables}
    backups = {t: _snapshot_table(conn, t, game_id, season) for t in tables}
    totals_before = game_stats_totals(conn, game_id, season) if endpoint_name == "game_stats" else None

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
        return "shrink_guarded"

    if totals_before is not None:
        dropped = value_guard_violations(
            totals_before, game_stats_totals(conn, game_id, season), _final_goals(conn, game_id, season),
        )
        if dropped:
            for t in tables:
                _restore_table(conn, t, game_id, season, backups[t])
            logger.warning(
                "season=%s game_id=%s endpoint=%s: reparse SKIPPED, value guard: totals would collapse "
                "or goals move away from the final score (total: before->after[, final]): %s -- raw "
                "response saved to disk regardless; existing curated rows kept untouched",
                season, game_id, endpoint_name, dropped,
            )
            return "value_guarded"

    logger.info(
        "season=%s game_id=%s endpoint=%s: reparse applied, row counts: %s",
        season, game_id, endpoint_name, after,
    )
    return "reparsed"


def salvage_grow_from_zero(conn: sqlite3.Connection, *, game_id: int, season: int, endpoint_name: str, do_upsert) -> dict[str, tuple[int, int]]:
    """The one exception to the uniform guard (approved 2026-09-24, R5 in
    docs/RECOVERY_BACKLOG.md). The guard is all-or-nothing per game, so a
    refetch that recovers penalties 0->9 but carries one fewer roster row is
    reverted whole. This runs do_upsert() like the guard does, then keeps a
    guarded table's new rows only if the table had ZERO rows for this game
    before and has some now; every other guarded table is restored exactly.
    Returns {table: (before, after)} for the accepted tables."""
    tables = GUARDED_TABLES[endpoint_name]
    before = {t: _count(conn, t, game_id, season) for t in tables}
    backups = {t: _snapshot_table(conn, t, game_id, season) for t in tables}

    do_upsert()

    after = {t: _count(conn, t, game_id, season) for t in tables}
    accepted = {t: (before[t], after[t]) for t in tables if before[t] == 0 and after[t] > 0}
    for t in tables:
        if t not in accepted:
            _restore_table(conn, t, game_id, season, backups[t])
    logger.info(
        "season=%s game_id=%s endpoint=%s: salvage accepted %s, kept old rows for %s",
        season, game_id, endpoint_name, accepted or "nothing", [t for t in tables if t not in accepted],
    )
    return accepted


def load_latest_raw(conn: sqlite3.Connection, endpoint_name: str, season: int, game_id: int) -> tuple[dict, Path]:
    """The most recently saved raw response for this game, read from disk.
    No HTTP."""
    found = raw_cache._last_raw_response(conn, "liiga", endpoint_name, f"{season}:{game_id}")
    if found is None:
        raise LookupError(f"no saved raw {endpoint_name} response for season={season} game_id={game_id}")
    path = found[1]
    return json.loads(path.read_text(encoding="utf-8")), path


def penalty_players_missing_from_roster(conn: sqlite3.Connection, game_id: int, season: int) -> list[tuple]:
    """(team_id, event_id, player_id) for every penalty whose player_id is
    not in this game's game_rosters. player_id 0 is how liiga.fi marks a
    penalty with no individual player (mostly 'Joukkuerangaistus', team
    penalty) -- 2,152 such rows in hockey.db, no player 0 exists -- so it
    is not checked, nor is NULL."""
    return conn.execute(
        "SELECT p.team_id, p.event_id, p.player_id FROM game_penalty_events p "
        "WHERE p.game_id = ? AND p.season = ? AND p.player_id IS NOT NULL AND p.player_id <> 0 AND NOT EXISTS ("
        "  SELECT 1 FROM game_rosters r WHERE r.game_id = p.game_id AND r.season = p.season "
        "  AND r.player_id = p.player_id) ORDER BY p.event_id",
        (game_id, season),
    ).fetchall()


def salvage_game_detail(conn: sqlite3.Connection, season: int, game_id: int, *, dry_run: bool) -> dict:
    """Grow-from-zero salvage of one game from its saved raw game_detail.
    Returns {'outcome', 'accepted', 'raw', 'missing_roster_players'}; outcome
    is 'salvaged' or 'salvage_no_gain' ('would_salvage'/'would_not_gain' in
    a dry run, which writes nothing)."""
    data, path = load_latest_raw(conn, "game_detail", season, game_id)
    parsed = parsers.parse_game_detail(data, game_id, season)

    if dry_run:
        table_key = {"game_rosters": "rosters", "game_penalty_events": "penalties", "game_goalkeeper_events": "goalkeeper_events"}
        accepted = {
            t: (0, len(parsed[k])) for t, k in table_key.items()
            if _count(conn, t, game_id, season) == 0 and parsed[k]
        }
        return {"outcome": "would_salvage" if accepted else "would_not_gain", "accepted": accepted, "raw": str(path), "missing_roster_players": []}

    accepted = salvage_grow_from_zero(
        conn, game_id=game_id, season=season, endpoint_name="game_detail",
        do_upsert=lambda: parsers.upsert_game_detail(conn, game_id, season, parsed),
    )
    missing = penalty_players_missing_from_roster(conn, game_id, season) if "game_penalty_events" in accepted else []
    for team_id, event_id, player_id in missing:
        logger.warning(
            "season=%s game_id=%s: salvaged penalty event_id=%s (team %s) has player_id=%s, not in game_rosters",
            season, game_id, event_id, team_id, player_id,
        )
    return {"outcome": "salvaged" if accepted else "salvage_no_gain", "accepted": accepted, "raw": str(path), "missing_roster_players": missing}


def run_salvage(conn: sqlite3.Connection, *, targets: list[tuple[int, int]], dry_run: bool, outcomes_path: Path | None = None) -> dict:
    candidates = find_target_candidates(conn, targets)
    logger.info("salvage: %d of %d target game(s) are ended, dry_run=%s", len(candidates), len(targets), dry_run)
    games = []
    for game_id, season in candidates:
        result = salvage_game_detail(conn, season, game_id, dry_run=dry_run)
        games.append({"season": season, "game_id": game_id, **result})
        if outcomes_path is not None and not dry_run:
            new_file = not outcomes_path.exists()
            with open(outcomes_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if new_file:
                    writer.writerow(["season", "game_id", "endpoint", "outcome", "checked_at"])
                writer.writerow([season, game_id, "game_detail", result["outcome"], _iso_utc(_now())])
    outcomes: dict[str, int] = {}
    for g in games:
        outcomes[g["outcome"]] = outcomes.get(g["outcome"], 0) + 1
    return {
        "targets": len(targets), "candidates": len(candidates), "dry_run": dry_run, "outcomes": outcomes,
        "missing_roster_players": {f"{g['season']}:{g['game_id']}": g["missing_roster_players"] for g in games if g["missing_roster_players"]},
        "games": [{k: g[k] for k in ("season", "game_id", "outcome", "accepted")} for g in games],
    }


def resync_game_endpoint(
    conn: sqlite3.Connection, season: int, game_id: int, endpoint_name: str, *, min_hours_since_fetch: float
) -> str:
    """Returns one of: 'skipped_not_due', 'fetch_failed', 'unchanged',
    'reparsed', 'shrink_guarded', 'value_guarded'."""
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
        return reparse_with_shrink_guard(
            conn, game_id=game_id, season=season, endpoint_name="game_detail",
            do_upsert=lambda: parsers.upsert_game_detail(conn, game_id, season, parsed),
        )
    elif endpoint_name == "game_stats":
        parsed = parsers.parse_game_stats(result.data, game_id, season)
        return reparse_with_shrink_guard(
            conn, game_id=game_id, season=season, endpoint_name="game_stats",
            do_upsert=lambda: parsers.upsert_game_stats(conn, game_id, season, parsed),
        )
    elif endpoint_name == "shotmap":
        rows = parsers.parse_shotmap(result.data, game_id, season)
        return reparse_with_shrink_guard(
            conn, game_id=game_id, season=season, endpoint_name="shotmap",
            do_upsert=lambda: parsers.upsert_shot_events(conn, game_id, season, rows),
        )
    else:
        raise ValueError(f"resync not implemented for endpoint {endpoint_name!r}")


def run_resync(
    conn: sqlite3.Connection, *, endpoints: list[str], min_hours_since_fetch: float, dry_run: bool,
    days: int | None = None, targets: list[tuple[int, int]] | None = None,
    outcomes_path: Path | None = None,
) -> dict:
    """Exactly one of `days` (time window) or `targets` ((season, game_id)
    pairs from load_targets) selects the candidates."""
    if (days is None) == (targets is None):
        raise ValueError("pass exactly one of days or targets")

    if targets is not None:
        candidates = find_target_candidates(conn, targets)
        logger.info(
            "resync: %d of %d target game(s) are ended, endpoints=%s, dry_run=%s",
            len(candidates), len(targets), endpoints, dry_run,
        )
    else:
        candidates = find_resync_candidates(conn, days=days)
        logger.info(
            "resync: %d candidate game(s) with ended=1 in the last %d day(s), endpoints=%s, dry_run=%s",
            len(candidates), days, endpoints, dry_run,
        )

    outcomes_file = writer = None
    if outcomes_path is not None and not dry_run:
        new_file = not outcomes_path.exists()
        outcomes_path.parent.mkdir(parents=True, exist_ok=True)
        outcomes_file = open(outcomes_path, "a", newline="", encoding="utf-8")
        writer = csv.writer(outcomes_file)
        if new_file:
            writer.writerow(["season", "game_id", "endpoint", "outcome", "checked_at"])

    outcome_counts: dict[str, dict[str, int]] = {}
    by_season: dict[int, dict[str, dict[str, int]]] = {}
    try:
        for game_id, season in candidates:
            for endpoint_name in endpoints:
                if dry_run:
                    due = _is_due(conn, endpoint_name, season, game_id, min_hours_since_fetch=min_hours_since_fetch)
                    outcome = "would_check" if due else "skipped_not_due"
                else:
                    outcome = resync_game_endpoint(
                        conn, season, game_id, endpoint_name, min_hours_since_fetch=min_hours_since_fetch,
                    )
                    if writer is not None:
                        writer.writerow([season, game_id, endpoint_name, outcome, _iso_utc(_now())])
                        outcomes_file.flush()  # a Ctrl-C mid-run keeps every row written so far
                bucket = outcome_counts.setdefault(endpoint_name, {})
                bucket[outcome] = bucket.get(outcome, 0) + 1
                season_bucket = by_season.setdefault(season, {}).setdefault(endpoint_name, {})
                season_bucket[outcome] = season_bucket.get(outcome, 0) + 1
    finally:
        if outcomes_file is not None:
            outcomes_file.close()

    return {
        "days": days, "targets": len(targets) if targets is not None else None,
        "candidates": len(candidates), "dry_run": dry_run,
        "outcomes": outcome_counts, "by_season": dict(sorted(by_season.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--days", type=int, default=None, help=f"re-sync games with ended=1 and start_utc within the last N days (default {DEFAULT_DAYS})")
    selection.add_argument("--targets", type=Path, default=None, help="CSV with a 'season,game_id' header: re-sync exactly these games (ended=1 only)")
    parser.add_argument("--outcomes", type=Path, default=None, help="append one season,game_id,endpoint,outcome row per checked pair to this CSV (ignored with --dry-run)")
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
    parser.add_argument(
        "--salvage-from-raw", action="store_true",
        help="with --targets and --endpoints game_detail: grow-from-zero reparse of each game's saved raw "
        "response, zero HTTP (see salvage_grow_from_zero)",
    )
    args = parser.parse_args()

    endpoints = [e.strip() for e in args.endpoints.split(",") if e.strip()]
    unknown = set(endpoints) - set(RESYNC_ENDPOINTS)
    if unknown:
        parser.error(f"unknown endpoint(s): {sorted(unknown)}, must be from {RESYNC_ENDPOINTS}")

    if args.salvage_from_raw and (args.targets is None or endpoints != ["game_detail"]):
        parser.error("--salvage-from-raw needs --targets and --endpoints game_detail")

    _configure_logging()
    try:
        targets = load_targets(args.targets) if args.targets is not None else None
    except (OSError, ValueError) as e:
        parser.error(str(e))
    days = None if targets is not None else (args.days if args.days is not None else DEFAULT_DAYS)
    conn = db.get_connection()
    try:
        if args.salvage_from_raw:
            summary = run_salvage(conn, targets=targets, dry_run=args.dry_run, outcomes_path=args.outcomes)
            logger.info("salvage complete: %s", summary)
            print(summary)
            return
        summary = run_resync(
            conn, days=days, targets=targets, endpoints=endpoints,
            min_hours_since_fetch=args.min_hours_since_fetch, dry_run=args.dry_run,
            outcomes_path=args.outcomes,
        )
        logger.info("resync complete: %s", summary)
        print(summary)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
