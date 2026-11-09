"""Which lineup snapshot counts as the pre-game one.

Shared by starters.py (the starting goalie) and lineups.py (the skaters),
so both read the same snapshot: the latest parsed one with a confirmed
lineup, captured before the start. A confirmed lineup is a snapshot with a
starter: liiga.fi sets `line` on the dressed players only close to puck
drop, and the snapshot job infers the starter from it, so a snapshot
without a starter has no lineup (every 'opening' window row).
"""

import sqlite3
from datetime import datetime


def latest_pregame_snapshot(conn: sqlite3.Connection, g: sqlite3.Row, role: str) -> sqlite3.Row | None:
    """The lineup_snapshots row for one side of game g (a game_spine row),
    or None when nothing was captured before the start."""
    start = datetime.fromisoformat(g["start_utc"].replace("Z", "+00:00"))
    before = [
        (datetime.fromisoformat(r["captured_at"]), r["captured_at"], r["starter_player_id"], r["id"], r)
        for r in conn.execute(
            "SELECT id, captured_at, starter_player_id, roster_json FROM snapshots.lineup_snapshots "
            "WHERE league = 'liiga' AND season = ? AND game_id = ? AND team_role = ? "
            "AND parsed = 1 AND starter_player_id IS NOT NULL",
            (g["season"], g["game_id"], role),
        )
    ]
    before = [b for b in before if b[0] < start]
    if not before:
        return None
    return max(before, key=lambda b: b[:4])[4]
