"""Power plays and penalties per team per game: the game_team_special_teams table.

A derived per-game table (definition on its DDL in db.py). Feature code
reads it only through history.history_before; special_teams.py builds the
rolling windows.

Penalties come from penalty_events_resolved (teams corrected, F3a);
unresolved rows are left out. A penalty counts when it is 2 or 5 minutes
with a non-zero duration: minors and majors that changed the strength.
Misconducts (10, 20 minutes) and the extra or untyped entries liiga.fi adds
in some games never do.

- Power-play opportunities, coincident-adjusted (FEATURE_AUDIT item 7):
  the opponent's counted penalties, minus those starting at the same second
  as one of the team's own. The same rule for every season; liiga.fi's
  stored powerplay_instances is not used (stripped for 2025/2026).
- Power-play goals: goals the team scored with liiga.fi's own strength
  label YV or YV2 (5-on-4, 5-on-3). The label exists for every season and
  agrees with the advantage derived from penalty timing on 99.2-99.6% of
  goals per season (special_teams_report.pp_goal_sources). Goals exclude VT0 and
  RL0 events (no goal: the score does not change) and shootout attempts.
- A competitive game with no penalty event at all is missing data, not a
  clean game (six such games, plus the 2022:49298 tiebreak, which feeds no
  rating anyway): every count is NULL.
"""

import sqlite3

# A penalty that changes the strength.
COUNTED = (
    "team_id IS NOT NULL AND penalty_minutes IN (2, 5) "
    "AND penalty_end_time > penalty_begin_time"
)
# A real goal, not a shootout attempt. goal_types is a JSON list.
REAL_GOAL = (
    "e.goal_types NOT LIKE '%\"VT0\"%' AND e.goal_types NOT LIKE '%\"RL0\"%' "
    "AND NOT (s.end_type = 'SO' AND e.period >= 5)"
)
PP_LABEL = "(e.goal_types LIKE '%\"YV\"%' OR e.goal_types LIKE '%\"YV2\"%')"


def build(conn: sqlite3.Connection) -> None:
    conn.execute(f"""
        INSERT INTO game_team_special_teams
        WITH sides AS (
            SELECT game_id, season, home_team_id AS team_id, away_team_id AS opponent_id
            FROM game_spine WHERE ended = 1 AND is_liiga_competitive = 1
            UNION ALL
            SELECT game_id, season, away_team_id, home_team_id
            FROM game_spine WHERE ended = 1 AND is_liiga_competitive = 1
        ),
        has_penalties AS (SELECT DISTINCT season, game_id FROM hockey.game_penalty_events),
        counted AS (
            SELECT season, game_id, team_id, penalty_begin_time AS t
            FROM penalty_events_resolved WHERE {COUNTED}
        ),
        taken AS (
            SELECT season, game_id, team_id, COUNT(*) AS n FROM counted GROUP BY 1, 2, 3
        ),
        -- Penalties that give the opponent a power play: no penalty of the
        -- other team starts at the same second.
        seconds AS (
            SELECT season, game_id, t, COUNT(DISTINCT team_id) AS n_teams
            FROM counted GROUP BY 1, 2, 3
        ),
        uncancelled AS (
            SELECT c.season, c.game_id, c.team_id, COUNT(*) AS n
            FROM counted c JOIN seconds x USING (season, game_id, t)
            WHERE x.n_teams = 1
            GROUP BY 1, 2, 3
        ),
        pp_goals AS (
            SELECT e.season, e.game_id, e.team_id, COUNT(*) AS n
            FROM hockey.game_goal_events e JOIN game_spine s USING (game_id, season)
            WHERE {REAL_GOAL} AND {PP_LABEL}
            GROUP BY 1, 2, 3
        )
        SELECT d.game_id, d.season, d.team_id, d.opponent_id,
               CASE WHEN h.game_id IS NOT NULL THEN COALESCE(uo.n, 0) END,
               CASE WHEN h.game_id IS NOT NULL THEN COALESCE(ut.n, 0) END,
               CASE WHEN h.game_id IS NOT NULL THEN COALESCE(gf.n, 0) END,
               CASE WHEN h.game_id IS NOT NULL THEN COALESCE(ga.n, 0) END,
               CASE WHEN h.game_id IS NOT NULL THEN COALESCE(tt.n, 0) END,
               CASE WHEN h.game_id IS NOT NULL THEN COALESCE(td.n, 0) END
        FROM sides d
        LEFT JOIN has_penalties h ON h.season = d.season AND h.game_id = d.game_id
        LEFT JOIN uncancelled uo ON uo.season = d.season AND uo.game_id = d.game_id AND uo.team_id = d.opponent_id
        LEFT JOIN uncancelled ut ON ut.season = d.season AND ut.game_id = d.game_id AND ut.team_id = d.team_id
        LEFT JOIN pp_goals gf ON gf.season = d.season AND gf.game_id = d.game_id AND gf.team_id = d.team_id
        LEFT JOIN pp_goals ga ON ga.season = d.season AND ga.game_id = d.game_id AND ga.team_id = d.opponent_id
        LEFT JOIN taken tt ON tt.season = d.season AND tt.game_id = d.game_id AND tt.team_id = d.team_id
        LEFT JOIN taken td ON td.season = d.season AND td.game_id = d.game_id AND td.team_id = d.opponent_id
    """)
    # Every goal belongs to one of its game's two teams (true today); a
    # third team would drop out of both sides' counts.
    stray = conn.execute("""
        SELECT COUNT(*) FROM hockey.game_goal_events e JOIN game_spine s USING (game_id, season)
        WHERE s.is_liiga_competitive = 1 AND e.team_id NOT IN (s.home_team_id, s.away_team_id)
    """).fetchone()[0]
    if stray:
        raise ValueError(f"{stray} competitive goal events name a team outside their game")
