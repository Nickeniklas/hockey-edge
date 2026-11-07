"""Shot attempts and game xG per team per game: the game_team_shots table.

A derived per-game table (definition on its DDL in db.py). Feature code
reads it only through history.history_before; shot_quality.py builds the
rolling windows.

Corsi is every shot_events row, whatever its result type (D4): from 2023
liiga.fi labels as blocked many attempts it earlier labelled missed, so only
the total is comparable across seasons. shot_events.shooting_team_id is the
bare team number ('933686567'), while games and every other table use
'933686567:saipa'; teams are matched on the number. A game without any
shot_events row is missing data, so its Corsi stays NULL, not 0.
"""

import sqlite3


def team_number(team_id: str) -> str:
    """'933686567:saipa' -> '933686567', the form shot_events uses."""
    return team_id.split(":", 1)[0]


def _number(column: str) -> str:
    """SQL for team_number()."""
    return f"substr({column}, 1, instr({column}, ':') - 1)"


def build(conn: sqlite3.Connection) -> None:
    conn.execute(f"""
        INSERT INTO game_team_shots
        WITH sides AS (
            SELECT s.game_id, s.season, s.home_team_id AS team_id, s.away_team_id AS opponent_id,
                   g.home_expected_goals AS xg_for, g.away_expected_goals AS xg_against
            FROM game_spine s JOIN hockey.games g USING (game_id, season)
            WHERE s.ended = 1
            UNION ALL
            SELECT s.game_id, s.season, s.away_team_id, s.home_team_id,
                   g.away_expected_goals, g.home_expected_goals
            FROM game_spine s JOIN hockey.games g USING (game_id, season)
            WHERE s.ended = 1
        ),
        attempts AS (
            SELECT season, game_id, shooting_team_id AS team_number, COUNT(*) AS corsi,
                   SUM(strength_type = 'EvenStrengthShot') AS ev_corsi
            FROM hockey.shot_events
            GROUP BY season, game_id, shooting_team_id
        ),
        has_shots AS (SELECT DISTINCT season, game_id FROM hockey.shot_events)
        SELECT d.game_id, d.season, d.team_id, d.opponent_id,
               CASE WHEN h.game_id IS NOT NULL THEN COALESCE(f.corsi, 0) END,
               CASE WHEN h.game_id IS NOT NULL THEN COALESCE(a.corsi, 0) END,
               CASE WHEN h.game_id IS NOT NULL THEN COALESCE(f.ev_corsi, 0) END,
               CASE WHEN h.game_id IS NOT NULL THEN COALESCE(a.ev_corsi, 0) END,
               CASE WHEN d.xg_for > 0 AND d.xg_against > 0 THEN d.xg_for END,
               CASE WHEN d.xg_for > 0 AND d.xg_against > 0 THEN d.xg_against END
        FROM sides d
        LEFT JOIN has_shots h ON h.season = d.season AND h.game_id = d.game_id
        LEFT JOIN attempts f ON f.season = d.season AND f.game_id = d.game_id
             AND f.team_number = {_number('d.team_id')}
        LEFT JOIN attempts a ON a.season = d.season AND a.game_id = d.game_id
             AND a.team_number = {_number('d.opponent_id')}
    """)
    # Every attempt belongs to one of the game's two teams (checked in the
    # F2 pre-check). A stray one would silently drop out of both counts.
    stray = conn.execute(f"""
        SELECT COUNT(*) FROM hockey.shot_events e
        JOIN game_spine s USING (game_id, season)
        WHERE e.shooting_team_id NOT IN ({_number('s.home_team_id')}, {_number('s.away_team_id')})
    """).fetchone()[0]
    if stray:
        raise ValueError(f"{stray} shot_events rows match neither team of their game")
