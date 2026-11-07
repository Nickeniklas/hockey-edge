"""Penalty events with the penalised team corrected: penalty_events_resolved (D1).

A derived per-game table (definition on its DDL in db.py). Feature code
reads it only through history.history_before.

liiga.fi lists a game's penalties under the wrong team's array in most
2017-2022 and 2024 games, per game rather than per season, and can move
them between the arrays on refetch (docs/FEATURE_AUDIT.md item 7). So the
stored team_id is not trusted. hockey.db keeps what liiga.fi sent; the
correction lives here and is redone on every build.

- A player penalty takes the penalised player's roster team in that game.
- A penalty with no single roster team takes the game's direction: the
  stored team if the game's roster-resolved penalties are mostly stored on
  the right team, the other team if mostly on the wrong one. That covers
  team/bench penalties (player_id 0, no such player exists) and the few
  players liiga.fi lists on both rosters of one game.
- A player not in either roster stays unresolved (team_id NULL), as does a
  bench or two-roster penalty in a game whose direction is unknown (no
  roster-resolved penalties, or as many kept as swapped).
"""

import sqlite3


def build(conn: sqlite3.Connection) -> None:
    conn.execute("""
        INSERT INTO penalty_events_resolved
        WITH pen AS (
            SELECT p.*, s.home_team_id, s.away_team_id
            FROM hockey.game_penalty_events p
            JOIN game_spine s USING (game_id, season)
            WHERE s.is_liiga_competitive = 1
        ),
        roster AS (
            SELECT pen.season, pen.game_id, pen.event_id,
                   COUNT(r.team_id) AS n_teams, MIN(r.team_id) AS roster_team
            FROM pen
            LEFT JOIN hockey.game_rosters r
                 ON r.game_id = pen.game_id AND r.season = pen.season
                AND r.player_id = pen.player_id AND pen.player_id > 0
            GROUP BY pen.season, pen.game_id, pen.event_id
        ),
        matched AS (
            SELECT pen.*, ro.n_teams,
                   CASE WHEN ro.n_teams = 1 THEN ro.roster_team END AS roster_team
            FROM pen JOIN roster ro USING (season, game_id, event_id)
        ),
        direction AS (
            SELECT season, game_id,
                   SUM(roster_team = team_id) AS n_kept,
                   SUM(roster_team <> team_id) AS n_swapped
            FROM matched WHERE roster_team IS NOT NULL
            GROUP BY season, game_id
        ),
        resolved AS (
            SELECT m.*,
                   CASE
                       WHEN m.roster_team IS NOT NULL THEN m.roster_team
                       WHEN m.player_id > 0 AND m.n_teams = 0 THEN NULL
                       WHEN d.n_kept > d.n_swapped THEN m.team_id
                       WHEN d.n_swapped > d.n_kept THEN
                           CASE WHEN m.team_id = m.home_team_id THEN m.away_team_id ELSE m.home_team_id END
                   END AS resolved_team,
                   CASE
                       WHEN m.roster_team IS NOT NULL THEN 'roster'
                       WHEN m.player_id > 0 AND m.n_teams = 0 THEN 'unresolved'
                       WHEN d.n_kept <> d.n_swapped THEN 'game_direction'
                       ELSE 'unresolved'
                   END AS team_source
            FROM matched m
            LEFT JOIN direction d USING (season, game_id)
        )
        SELECT game_id, season, event_id, player_id, resolved_team, team_source, team_id,
               CASE WHEN resolved_team IS NOT NULL THEN resolved_team <> team_id END,
               sufferer_player_id, period, game_time_seconds, penalty_begin_time, penalty_end_time,
               fault_name, fault_type, penalty_minutes
        FROM resolved
    """)
    # Every stored and every resolved team is one of the game's two teams
    # (true for competitive games; only PRACTICE games break it). A third
    # team would make "the other team" meaningless.
    stray = conn.execute("""
        SELECT COUNT(*) FROM penalty_events_resolved r JOIN game_spine s USING (game_id, season)
        WHERE r.stored_team_id NOT IN (s.home_team_id, s.away_team_id)
           OR r.team_id NOT IN (s.home_team_id, s.away_team_id)
    """).fetchone()[0]
    if stray:
        raise ValueError(f"{stray} competitive penalty events name a team outside their game")
