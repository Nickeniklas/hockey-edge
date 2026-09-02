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

SCHEMA = """
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
    raw_payload TEXT NOT NULL
);

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

-- One row per (league, season, game_id, window). 'pending' until either the
-- window is satisfied by a successful capture poll, or the game's start_utc
-- passes with it still unsatisfied, at which point it becomes 'missed' --
-- never left as an absent row. due_at is recomputed from the latest known
-- start_utc on each discovery pass, but only while status='pending' (a
-- resolved window's due_at is a historical fact, not something a later
-- schedule change should rewrite).
CREATE TABLE IF NOT EXISTS capture_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league TEXT NOT NULL DEFAULT 'liiga',
    season INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    window TEXT NOT NULL CHECK (window IN ('opening', 'mid', 'closing')),
    due_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'satisfied', 'missed')),
    satisfied_at TEXT,
    missed_recorded_at TEXT,
    UNIQUE (league, season, game_id, window)
);
CREATE INDEX IF NOT EXISTS idx_capture_windows_due
    ON capture_windows (status, due_at);

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


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def insert_odds_snapshot(conn: sqlite3.Connection, snapshot: OddsSnapshot) -> None:
    conn.execute(
        "INSERT INTO odds_snapshots "
        "(league, book, market, fixture_ref, captured_at, home_odds, draw_odds, "
        "away_odds, parsed, raw_payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
    window: str,
    due_at: str,
) -> None:
    """Ensure a (league, season, game_id, window) row exists with this due_at.
    Only rewrites due_at while the window is still 'pending' -- a window that
    has already resolved (satisfied/missed) keeps the due_at that was true
    when it resolved, even if a later discovery poll sees a changed
    start_utc."""
    conn.execute(
        "INSERT INTO capture_windows (league, season, game_id, window, due_at, status) "
        "VALUES (?, ?, ?, ?, ?, 'pending') "
        "ON CONFLICT (league, season, game_id, window) DO UPDATE SET "
        "due_at = excluded.due_at WHERE capture_windows.status = 'pending'",
        (league, season, game_id, window, due_at),
    )
    conn.commit()


def get_due_windows(
    conn: sqlite3.Connection, *, now_iso: str, league: str = "liiga"
) -> list[sqlite3.Row]:
    """Windows that are due, still unsatisfied, and whose game has not yet
    started -- never a timestamp-proximity match. Joins against the latest
    known start_utc per (season, game_id) from discovered_fixtures."""
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
          AND cw.status = 'pending'
          AND cw.due_at <= ?
          AND latest.start_utc > ?
        ORDER BY cw.due_at
        """,
        (league, league, now_iso, now_iso),
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
    conn: sqlite3.Connection, window_ids: list[int], *, now_iso: str
) -> None:
    for window_id in window_ids:
        conn.execute(
            "UPDATE capture_windows SET status = 'satisfied', satisfied_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (now_iso, window_id),
        )
    conn.commit()


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


def count_api_usage_this_month(
    conn: sqlite3.Connection, *, provider: str, now_iso: str
) -> int:
    month_prefix = now_iso[:7]  # 'YYYY-MM'
    row = conn.execute(
        "SELECT COUNT(*) FROM api_usage WHERE provider = ? AND requested_at LIKE ?",
        (provider, f"{month_prefix}%"),
    ).fetchone()
    return row[0]
