"""Append-only SQLite storage for Layer 2 snapshot captures.

Tables mirror the "Storage shapes" section of docs/DATA_PIPELINE.md. Rows are
insert-only — never UPDATE or DELETE. "What was known before puck drop" is what
the model may see; append-only writes with `captured_at` are what make that
claim checkable later, so nothing here may rewrite history.
"""

import sqlite3
from pathlib import Path

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

CREATE TABLE IF NOT EXISTS lineup_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    source TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    goalie_confirmed INTEGER,
    raw_payload TEXT NOT NULL
);
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
