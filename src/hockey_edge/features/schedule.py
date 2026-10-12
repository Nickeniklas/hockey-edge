"""Family 4, Liiga subset: rest days and games in the last 6 days.

No travel or time zones (irrelevant in Liiga). Column definitions are on
features_schedule in db.py.
"""

import sqlite3
from datetime import date, timedelta

from hockey_edge.features.history import history_before, target_games

RECENT_DAYS = 6

_SELECT = "MAX(h.start_date) AS last_date, COALESCE(SUM(h.start_date >= :since), 0) AS recent"
_WHERE = (
    "h.is_liiga_competitive = 1 AND h.season = :season "
    "AND (h.home_team_id = :team OR h.away_team_id = :team)"
)


def _team(conn: sqlite3.Connection, g: sqlite3.Row, team_id: str) -> tuple[int | None, int]:
    game_day = date.fromisoformat(g["start_date"])
    row = history_before(conn, g["season"], g["game_id"], _SELECT, _WHERE, {
        "season": g["season"],
        "team": team_id,
        "since": (game_day - timedelta(days=RECENT_DAYS)).isoformat(),
    })[0]
    if row["last_date"] is None:
        return None, 0
    return (game_day - date.fromisoformat(row["last_date"])).days, row["recent"]


def build(conn: sqlite3.Connection) -> None:
    rows = []
    for g in target_games(conn):
        home_rest, home_recent = _team(conn, g, g["home_team_id"])
        away_rest, away_recent = _team(conn, g, g["away_team_id"])
        rows.append((g["game_id"], g["season"], home_rest, away_rest, home_recent, away_recent))
    conn.executemany("INSERT INTO features_schedule VALUES (?, ?, ?, ?, ?, ?)", rows)
