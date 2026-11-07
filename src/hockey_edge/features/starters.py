"""Who started in goal: the actual starter after the game, and the resolver.

Two derived tables (definitions on their DDL in db.py):
- game_goalies: one row per goalie per game, totals over all periods. Goalie
  history (goalie.py) reads it only through history.history_before.
- game_starters: one row per team per competitive game, with the actual
  starter, the pre-game snapshot starter and the resolved one.

The actual starter comes from game_goalie_period_stats: the one goalie with
period-1 shots on goal (or saves) > 0. game_goalkeeper_events cannot say who
started: 2025 onwards it has no opening rows at all, and many games have no
rows. It is used only to flag an early change. scripts/verify_starters.py
uses the same function.

Table names in derive_actual_starter are unqualified so it runs on a plain
hockey.db connection (the script) and on the feature build's connection,
where hockey.db is attached.
"""

import sqlite3
from datetime import datetime

EARLY_CHANGE_THRESHOLD_SECONDS = 300  # 5 minutes into period 1

SNAPSHOT_INFERRED = "snapshot_inferred"
ACTUAL_POSTGAME = "actual_postgame"
UNKNOWN = "unknown"


def derive_actual_starter(
    conn: sqlite3.Connection, season: int, game_id: int, team_id: str
) -> tuple[int | None, str]:
    """Returns (actual_starter_player_id_or_None, reason). reason is 'ok' on
    a clean single-candidate derivation, or why it is None (ambiguous / no
    data / flagged early change) -- never a guess.

    An early change is a goalkeeper event in the first 5 minutes for a
    goalie other than the derived starter. 2015-2024 log the starter's own
    opening assignment at begin_time 0; that is not a change.
    """
    period1 = conn.execute(
        "SELECT player_id, shots_on_goal, saves FROM game_goalie_period_stats "
        "WHERE season = ? AND game_id = ? AND team_id = ? AND period = 1",
        (season, game_id, team_id),
    ).fetchall()
    if not period1:
        return None, "no_period1_stats"

    candidates = [r[0] for r in period1 if (r[1] or 0) > 0]
    if len(candidates) == 0:
        candidates = [r[0] for r in period1 if (r[2] or 0) > 0]
    if len(candidates) != 1:
        return None, f"ambiguous_period1_stats ({len(candidates)} candidates)"

    starter_id = candidates[0]

    early_change = conn.execute(
        "SELECT COUNT(*) FROM game_goalkeeper_events "
        "WHERE season = ? AND game_id = ? AND team_id = ? "
        "AND begin_time < ? AND player_id != 0 AND player_id != ?",
        (season, game_id, team_id, EARLY_CHANGE_THRESHOLD_SECONDS, starter_id),
    ).fetchone()
    if early_change[0] > 0:
        return None, "flagged_early_change"

    return starter_id, "ok"


def _snapshot_starter(conn: sqlite3.Connection, g: sqlite3.Row, role: str) -> tuple[int, str] | None:
    """The latest parsed snapshot with a starter, captured before the start."""
    start = datetime.fromisoformat(g["start_utc"].replace("Z", "+00:00"))
    captured = [
        (datetime.fromisoformat(r["captured_at"]), r["captured_at"], r["starter_player_id"])
        for r in conn.execute(
            "SELECT captured_at, starter_player_id FROM snapshots.lineup_snapshots "
            "WHERE league = 'liiga' AND season = ? AND game_id = ? AND team_role = ? "
            "AND parsed = 1 AND starter_player_id IS NOT NULL",
            (g["season"], g["game_id"], role),
        )
    ]
    before = [c for c in captured if c[0] < start]
    if not before:
        return None
    _, captured_at, player_id = max(before)
    return player_id, captured_at


def resolve_starter(
    conn: sqlite3.Connection, season: int, game_id: int, team_role: str
) -> tuple[int | None, str]:
    """(player_id, source) for one team's goalie in one game.

    The pre-game snapshot when one exists (live games, and every 2027 game
    the job captured); else the actual starter for an ended game (historical
    training only); else (None, 'unknown'), and goalie.py uses the team's
    expected-goalie fallback. Reads the row build() stored.
    """
    row = conn.execute(
        "SELECT resolved_player_id, resolved_source FROM game_starters "
        "WHERE season = ? AND game_id = ? AND team_role = ?",
        (season, game_id, team_role),
    ).fetchone()
    if row is None:
        raise KeyError(f"no game_starters row for season={season} game_id={game_id} {team_role}")
    return row[0], row[1]


def _build_game_goalies(conn: sqlite3.Connection) -> None:
    conn.execute("""
        INSERT INTO game_goalies
        SELECT p.game_id, p.season, p.team_id, p.player_id,
               SUM(COALESCE(p.shots_on_goal, 0)),
               SUM(COALESCE(p.goals_allowed, 0)),
               SUM(COALESCE(p.time_on_ice_seconds, 0)),
               0, 0, NULL
        FROM hockey.game_goalie_period_stats p
        JOIN game_spine s ON s.season = p.season AND s.game_id = p.game_id
        GROUP BY p.season, p.game_id, p.team_id, p.player_id
    """)
    # The only goalie of his team who faced a shot played the whole game.
    # Shots, not ice time: 2026 has no goalie ice time at all and 2025 only
    # some. Where both exist they disagree in 0-6 team-games a season.
    conn.execute("""
        UPDATE game_goalies SET whole_game = 1
        WHERE shots_against > 0 AND NOT EXISTS (
            SELECT 1 FROM game_goalies o
            WHERE o.season = game_goalies.season AND o.game_id = game_goalies.game_id
              AND o.team_id = game_goalies.team_id AND o.player_id <> game_goalies.player_id
              AND o.shots_against > 0)
    """)
    # The opponent's game xG, only where both teams have one (from 2021).
    conn.execute("""
        UPDATE game_goalies SET opponent_xg = (
            SELECT CASE WHEN g.home_team_id = game_goalies.team_id
                        THEN g.away_expected_goals ELSE g.home_expected_goals END
            FROM hockey.games g
            WHERE g.season = game_goalies.season AND g.game_id = game_goalies.game_id
              AND g.home_expected_goals > 0 AND g.away_expected_goals > 0)
    """)


def build(conn: sqlite3.Connection) -> None:
    _build_game_goalies(conn)
    rows = []
    for g in conn.execute(
        "SELECT * FROM game_spine WHERE is_liiga_competitive = 1 ORDER BY season, game_id"
    ).fetchall():
        for role in ("home", "away"):
            team_id = g[f"{role}_team_id"]
            actual, reason = (None, "not_ended")
            if g["ended"]:
                actual, reason = derive_actual_starter(conn, g["season"], g["game_id"], team_id)
            snapshot = _snapshot_starter(conn, g, role)
            if snapshot is not None:
                resolved, source = snapshot[0], SNAPSHOT_INFERRED
            elif actual is not None:
                resolved, source = actual, ACTUAL_POSTGAME
            else:
                resolved, source = None, UNKNOWN
            rows.append((
                g["game_id"], g["season"], role, team_id, actual, reason,
                snapshot[0] if snapshot else None, snapshot[1] if snapshot else None,
                resolved, source,
            ))
            if actual is not None:
                conn.execute(
                    "UPDATE game_goalies SET is_actual_starter = 1 "
                    "WHERE season = ? AND game_id = ? AND team_id = ? AND player_id = ?",
                    (g["season"], g["game_id"], team_id, actual),
                )
    conn.executemany("INSERT INTO game_starters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
