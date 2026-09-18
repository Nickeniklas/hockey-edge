"""Append-only SQLite storage for Layer 2 snapshot captures.

Tables mirror the "Storage shapes" section of docs/DATA_PIPELINE.md. Rows are
insert-only — never UPDATE or DELETE. "What was known before puck drop" is what
the model may see; append-only writes with `captured_at` are what make that
claim checkable later, so nothing here may rewrite history.
"""

import json
import sqlite3
from pathlib import Path

from hockey_edge.snapshot.lineups import LineupSnapshot
from hockey_edge.snapshot.odds.base import OddsSnapshot

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "snapshots.db"

CAPTURE_KINDS = ("odds", "lineups")

SCHEMA = """
-- season/game_id are the liiga.fi game this odds row was resolved to at
-- capture time (NULL = unresolved: unknown participant, or no single
-- matching discovered fixture). home/away_participant_id and start_utc are
-- the provider's own view of the fixture, kept so resolution can be redone.
CREATE TABLE IF NOT EXISTS odds_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league TEXT NOT NULL,
    book TEXT NOT NULL,
    market TEXT,
    fixture_ref TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    home_odds REAL,
    draw_odds REAL,
    away_odds REAL,
    parsed INTEGER NOT NULL,
    raw_payload TEXT NOT NULL,
    season INTEGER,
    game_id INTEGER,
    home_participant_id INTEGER,
    away_participant_id INTEGER,
    start_utc TEXT
);
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_game
    ON odds_snapshots (league, season, game_id);

-- One row per (league, season, game_id, team_role) per capture poll --
-- append-only, same as odds_snapshots: a later poll writes a fresh row, it
-- never updates a prior one, so "what the lineup looked like at time T" stays
-- reconstructable. starter_* fields are always an INFERENCE, never a fact --
-- see hockey_edge.snapshot.lineups module docstring for why (no literal
-- "starter" field exists in the source payload) and scripts/verify_starters.py
-- for the periodic check against real results that starter_source's choice
-- (currently 'goalie_line_value') depends on. raw_payload retains the full
-- game_detail (+ game_preview, when available) response regardless of
-- whether parsing succeeded, same append-only-friendly contract as odds.
CREATE TABLE IF NOT EXISTS lineup_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league TEXT NOT NULL DEFAULT 'liiga',
    season INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    team_role TEXT NOT NULL CHECK (team_role IN ('home', 'away')),
    team_name TEXT NOT NULL,
    window TEXT,
    captured_at TEXT NOT NULL,
    source TEXT NOT NULL,
    confirmed_player_count INTEGER NOT NULL,
    confirmed_goalie_count INTEGER NOT NULL,
    roster_json TEXT NOT NULL,
    starter_player_id INTEGER,
    starter_name TEXT,
    starter_jersey INTEGER,
    starter_source TEXT,
    starter_confidence TEXT,
    parsed INTEGER NOT NULL,
    raw_payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lineup_snapshots_game
    ON lineup_snapshots (league, season, game_id);

-- Append-only: every discovery poll writes a fresh row per fixture it saw,
-- never updates one. This makes the schedule the job acted on auditable
-- after the fact (docs/DATA_PIPELINE.md Layer 2) -- distinct from
-- capture_windows below, which is job bookkeeping/state, not observational
-- data, and is updated in place the same way ingest/raw_cache.py's
-- sync_state is.
CREATE TABLE IF NOT EXISTS discovered_fixtures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league TEXT NOT NULL DEFAULT 'liiga',
    season INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    serie TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    started INTEGER NOT NULL,
    ended INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    source TEXT NOT NULL,
    raw_payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_discovered_fixtures_game
    ON discovered_fixtures (league, season, game_id);

-- One row per (league, season, game_id, kind, window). 'pending' until either
-- the window is satisfied by a successful capture poll of that kind, or the
-- game's start_utc passes with it still unsatisfied, at which point it
-- becomes 'missed' -- never left as an absent row. due_at is recomputed from
-- the latest known start_utc on each discovery pass, but only while
-- status='pending' (a resolved window's due_at is a historical fact, not
-- something a later schedule change should rewrite). `kind` exists because
-- odds and lineups succeed independently: with one shared status, a
-- successful odds poll used to mark a window satisfied and hide it from
-- lineup capture (docs/ODDS_PLAN.md). Rows from before 2026-09-17 are all
-- kind='lineups' -- odds capture didn't exist when they resolved.
-- satisfied_by (odds only, added 2026-09-18): comma-joined books that priced
-- the game on the poll that satisfied the window, in ODDS_BOOKS order, e.g.
-- 'pinnacle,bet365' or 'bet365'. A window is satisfied by ANY book, so this
-- is what tells a benchmark (pinnacle) capture from a fallback one. NULL on
-- lineup rows, and on the two odds windows satisfied before it existed
-- (both pinnacle-only by construction then).
CREATE TABLE IF NOT EXISTS capture_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league TEXT NOT NULL DEFAULT 'liiga',
    season INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('odds', 'lineups')),
    window TEXT NOT NULL CHECK (window IN ('opening', 'mid', 'closing')),
    due_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'satisfied', 'missed')),
    satisfied_at TEXT,
    missed_recorded_at TEXT,
    satisfied_by TEXT,
    UNIQUE (league, season, game_id, kind, window)
);
CREATE INDEX IF NOT EXISTS idx_capture_windows_due
    ON capture_windows (kind, status, due_at);

-- Append-only log of every outbound odds-provider HTTP request. outcome
-- distinguishes hard failure / 200-with-content / 200-but-empty per
-- constraint 6 -- the empty-vs-failure collapse that already burned this
-- project once (sync_state, season 2025 shot_events). The monthly ceiling
-- the job enforces is computed by counting rows here, not stored as state.
CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    http_status INTEGER,
    outcome TEXT NOT NULL CHECK (outcome IN ('success_content', 'success_empty', 'failure'))
);
CREATE INDEX IF NOT EXISTS idx_api_usage_provider_time
    ON api_usage (provider, requested_at);
"""


MIGRATION_BACKUP_NAME = "snapshots.pre-kind-migration.db"

_ODDS_SNAPSHOT_NEW_COLUMNS = (
    ("season", "INTEGER"),
    ("game_id", "INTEGER"),
    ("home_participant_id", "INTEGER"),
    ("away_participant_id", "INTEGER"),
    ("start_utc", "TEXT"),
)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection, db_path: Path) -> None:
    """2026-09-17 (docs/ODDS_PLAN.md Phase 2): per-kind capture_windows and
    odds_snapshots resolution columns. Runs before SCHEMA so SCHEMA's new
    indexes don't hit old-shape tables. No-op on a fresh or already-migrated
    DB. Backs the file up first (never overwriting an existing backup: if one
    exists, an earlier attempt already captured the true pre-migration state),
    then does everything in one transaction.

    Existing capture_windows rows all become kind='lineups' with ids and
    statuses kept -- NullOddsProvider never satisfied anything, so every
    status there is a lineup outcome. Each still-pending window also gets a
    pending kind='odds' twin; resolved windows get none (no odds capture
    existed for them).

    2026-09-18: capture_windows.satisfied_by, a nullable column added with
    ALTER TABLE -- no rebuild, no existing row touched, so no backup is taken
    when it is the only change (the structural 2026-09-17 migration above is
    what the backup guards)."""
    cw_cols = _columns(conn, "capture_windows")
    odds_cols = _columns(conn, "odds_snapshots")
    needs_cw = bool(cw_cols) and "kind" not in cw_cols
    needs_satisfied_by = bool(cw_cols) and "satisfied_by" not in cw_cols
    missing_odds = [c for c in _ODDS_SNAPSHOT_NEW_COLUMNS if odds_cols and c[0] not in odds_cols]
    if not needs_cw and not missing_odds and not needs_satisfied_by:
        return

    backup_path = db_path.parent / MIGRATION_BACKUP_NAME
    if (needs_cw or missing_odds) and not backup_path.exists():
        backup = sqlite3.connect(backup_path)
        try:
            conn.backup(backup)
        finally:
            backup.close()

    previous_isolation = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        for name, sql_type in missing_odds:
            conn.execute(f"ALTER TABLE odds_snapshots ADD COLUMN {name} {sql_type}")
        if needs_cw:
            conn.execute("""
                CREATE TABLE capture_windows_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    league TEXT NOT NULL DEFAULT 'liiga',
                    season INTEGER NOT NULL,
                    game_id INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('odds', 'lineups')),
                    window TEXT NOT NULL CHECK (window IN ('opening', 'mid', 'closing')),
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'satisfied', 'missed')),
                    satisfied_at TEXT,
                    missed_recorded_at TEXT,
                    UNIQUE (league, season, game_id, kind, window)
                )""")
            conn.execute("""
                INSERT INTO capture_windows_new
                    (id, league, season, game_id, kind, window, due_at, status,
                     satisfied_at, missed_recorded_at)
                SELECT id, league, season, game_id, 'lineups', window, due_at, status,
                       satisfied_at, missed_recorded_at
                FROM capture_windows""")
            conn.execute("""
                INSERT INTO capture_windows_new (league, season, game_id, kind, window, due_at, status)
                SELECT league, season, game_id, 'odds', window, due_at, 'pending'
                FROM capture_windows WHERE status = 'pending'""")
            conn.execute("DROP TABLE capture_windows")
            conn.execute("ALTER TABLE capture_windows_new RENAME TO capture_windows")
        if needs_satisfied_by:
            conn.execute("ALTER TABLE capture_windows ADD COLUMN satisfied_by TEXT")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.isolation_level = previous_isolation


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    _migrate(conn, db_path)
    conn.executescript(SCHEMA)
    return conn


def insert_odds_snapshot(conn: sqlite3.Connection, snapshot: OddsSnapshot) -> None:
    conn.execute(
        "INSERT INTO odds_snapshots "
        "(league, book, market, fixture_ref, captured_at, home_odds, draw_odds, "
        "away_odds, parsed, raw_payload, season, game_id, home_participant_id, "
        "away_participant_id, start_utc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            snapshot.league,
            snapshot.book,
            snapshot.market,
            snapshot.fixture_ref,
            snapshot.captured_at.isoformat(),
            snapshot.home_odds,
            snapshot.draw_odds,
            snapshot.away_odds,
            int(snapshot.parsed),
            snapshot.raw_payload,
            snapshot.season,
            snapshot.game_id,
            snapshot.home_participant_id,
            snapshot.away_participant_id,
            snapshot.start_utc,
        ),
    )
    conn.commit()


def insert_lineup_snapshot(conn: sqlite3.Connection, snapshot: LineupSnapshot) -> None:
    conn.execute(
        "INSERT INTO lineup_snapshots "
        "(league, season, game_id, team_role, team_name, window, captured_at, source, "
        "confirmed_player_count, confirmed_goalie_count, roster_json, starter_player_id, "
        "starter_name, starter_jersey, starter_source, starter_confidence, parsed, raw_payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            snapshot.league,
            snapshot.season,
            snapshot.game_id,
            snapshot.team_role,
            snapshot.team_name,
            snapshot.window,
            snapshot.captured_at.isoformat(),
            snapshot.source,
            snapshot.confirmed_player_count,
            snapshot.confirmed_goalie_count,
            json.dumps(snapshot.roster, ensure_ascii=False),
            snapshot.starter_player_id,
            snapshot.starter_name,
            snapshot.starter_jersey,
            snapshot.starter_source,
            snapshot.starter_confidence,
            int(snapshot.parsed),
            snapshot.raw_payload,
        ),
    )
    conn.commit()


def insert_discovered_fixture(
    conn: sqlite3.Connection,
    *,
    league: str,
    season: int,
    game_id: int,
    serie: str,
    start_utc: str,
    home_team: str,
    away_team: str,
    started: bool,
    ended: bool,
    captured_at: str,
    source: str,
    raw_payload: str,
) -> None:
    conn.execute(
        "INSERT INTO discovered_fixtures "
        "(league, season, game_id, serie, start_utc, home_team, away_team, "
        "started, ended, captured_at, source, raw_payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            league, season, game_id, serie, start_utc, home_team, away_team,
            int(started), int(ended), captured_at, source, raw_payload,
        ),
    )
    conn.commit()


def upsert_capture_window(
    conn: sqlite3.Connection,
    *,
    league: str,
    season: int,
    game_id: int,
    kind: str,
    window: str,
    due_at: str,
) -> None:
    """Ensure a (league, season, game_id, kind, window) row exists with this
    due_at. Only rewrites due_at while the window is still 'pending' -- a
    window that has already resolved (satisfied/missed) keeps the due_at that
    was true when it resolved, even if a later discovery poll sees a changed
    start_utc."""
    conn.execute(
        "INSERT INTO capture_windows (league, season, game_id, kind, window, due_at, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending') "
        "ON CONFLICT (league, season, game_id, kind, window) DO UPDATE SET "
        "due_at = excluded.due_at WHERE capture_windows.status = 'pending'",
        (league, season, game_id, kind, window, due_at),
    )
    conn.commit()


def get_due_windows(
    conn: sqlite3.Connection, *, kind: str, now_iso: str, league: str = "liiga"
) -> list[sqlite3.Row]:
    """Windows of one capture kind that are due, still unsatisfied, and whose
    game has not yet started -- never a timestamp-proximity match. Joins
    against the latest known start_utc per (season, game_id) from
    discovered_fixtures. `kind` is required so no caller can see (and
    satisfy) another kind's windows."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT cw.*, latest.start_utc AS game_start_utc
        FROM capture_windows cw
        JOIN (
            SELECT season, game_id, MAX(start_utc) AS start_utc
            FROM discovered_fixtures
            WHERE league = ?
            GROUP BY season, game_id
        ) latest ON latest.season = cw.season AND latest.game_id = cw.game_id
        WHERE cw.league = ?
          AND cw.kind = ?
          AND cw.status = 'pending'
          AND cw.due_at <= ?
          AND latest.start_utc > ?
        ORDER BY cw.due_at
        """,
        (league, league, kind, now_iso, now_iso),
    ).fetchall()
    return rows


def mark_missed_windows(
    conn: sqlite3.Connection, *, now_iso: str, league: str = "liiga"
) -> list[sqlite3.Row]:
    """Windows that are due, still unsatisfied, and whose game HAS started --
    genuinely gone, recorded explicitly as 'missed' rather than left pending
    forever. Returns the rows just marked, for logging."""
    conn.row_factory = sqlite3.Row
    to_miss = conn.execute(
        """
        SELECT cw.*, latest.start_utc AS game_start_utc
        FROM capture_windows cw
        JOIN (
            SELECT season, game_id, MAX(start_utc) AS start_utc
            FROM discovered_fixtures
            WHERE league = ?
            GROUP BY season, game_id
        ) latest ON latest.season = cw.season AND latest.game_id = cw.game_id
        WHERE cw.league = ?
          AND cw.status = 'pending'
          AND cw.due_at <= ?
          AND latest.start_utc <= ?
        """,
        (league, league, now_iso, now_iso),
    ).fetchall()
    for row in to_miss:
        conn.execute(
            "UPDATE capture_windows SET status = 'missed', missed_recorded_at = ? WHERE id = ?",
            (now_iso, row["id"]),
        )
    conn.commit()
    return to_miss


def mark_windows_satisfied(
    conn: sqlite3.Connection,
    window_ids: list[int],
    *,
    now_iso: str,
    satisfied_by: str | None = None,
) -> None:
    for window_id in window_ids:
        conn.execute(
            "UPDATE capture_windows SET status = 'satisfied', satisfied_at = ?, satisfied_by = ? "
            "WHERE id = ? AND status = 'pending'",
            (now_iso, satisfied_by, window_id),
        )
    conn.commit()


def find_liiga_games(
    conn: sqlite3.Connection, *, home_team_id: str, away_team_id: str, league: str = "liiga"
) -> list[sqlite3.Row]:
    """Latest discovered_fixtures row per (season, game_id) whose liiga.fi
    home/away teamId match exactly. teamId is read from raw_payload rather
    than the home_team/away_team display-name columns: it's liiga.fi's stable
    key. The caller narrows by start time."""
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT df.season, df.game_id, df.start_utc
        FROM discovered_fixtures df
        JOIN (
            SELECT MAX(id) AS id FROM discovered_fixtures
            WHERE league = ? GROUP BY season, game_id
        ) latest ON latest.id = df.id
        WHERE json_extract(df.raw_payload, '$.homeTeam.teamId') = ?
          AND json_extract(df.raw_payload, '$.awayTeam.teamId') = ?
        """,
        (league, home_team_id, away_team_id),
    ).fetchall()


def insert_api_usage(
    conn: sqlite3.Connection,
    *,
    provider: str,
    endpoint: str,
    requested_at: str,
    http_status: int | None,
    outcome: str,
) -> None:
    conn.execute(
        "INSERT INTO api_usage (provider, endpoint, requested_at, http_status, outcome) "
        "VALUES (?, ?, ?, ?, ?)",
        (provider, endpoint, requested_at, http_status, outcome),
    )
    conn.commit()


def last_successful_odds_poll(
    conn: sqlite3.Connection, *, provider: str, endpoint: str = "odds-by-tournaments"
) -> str | None:
    """requested_at of the most recent poll that actually reached the board
    (content or empty), or None. A failed request is not an attempt: it tells
    us nothing about whether a fixture is posted, so it must not hold off a
    retry."""
    row = conn.execute(
        "SELECT MAX(requested_at) FROM api_usage WHERE provider = ? AND endpoint = ? "
        "AND outcome IN ('success_content', 'success_empty')",
        (provider, endpoint),
    ).fetchone()
    return row[0]


def count_api_usage_this_month(
    conn: sqlite3.Connection, *, provider: str, now_iso: str
) -> int:
    month_prefix = now_iso[:7]  # 'YYYY-MM'
    row = conn.execute(
        "SELECT COUNT(*) FROM api_usage WHERE provider = ? AND requested_at LIKE ?",
        (provider, f"{month_prefix}%"),
    ).fetchone()
    return row[0]
