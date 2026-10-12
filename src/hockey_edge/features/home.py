"""Family 5: home advantage. League rate plus a shrunk home-team offset.

Column definitions are on features_home in db.py. The shrunk offset uses a
default k; the raw value and its game counts are stored so k can be tuned
inside training folds.
"""

import sqlite3

from hockey_edge.features.history import history_before, target_games

# This season and the two before it.
WINDOW_SEASONS = 3
# Games. A team's own home edge is mostly noise over one season.
HOME_OFFSET_K = 60

_REG_DIFF = "CASE WHEN h.regulation_result = 'D' THEN 0 ELSE h.home_goals - h.away_goals END"
_IN_WINDOW = "h.feeds_ratings = 1 AND h.season >= :first_season"

_LEAGUE_SELECT = f"""
    COUNT(*) AS n,
    AVG(h.regulation_result = 'H') AS home_win,
    AVG(h.regulation_result = 'D') AS draw,
    AVG(h.regulation_result = 'A') AS away_win,
    AVG({_REG_DIFF}) AS goal_diff
"""
_TEAM_SELECT = f"""
    COALESCE(SUM(h.home_team_id = :team), 0) AS n_home,
    COALESCE(SUM(h.away_team_id = :team), 0) AS n_away,
    AVG(CASE WHEN h.home_team_id = :team THEN {_REG_DIFF} END) AS diff_home,
    AVG(CASE WHEN h.away_team_id = :team THEN -({_REG_DIFF}) END) AS diff_away
"""


def build(conn: sqlite3.Connection) -> None:
    rows = []
    for g in target_games(conn):
        params = {"first_season": g["season"] - WINDOW_SEASONS + 1, "team": g["home_team_id"]}
        league = history_before(conn, g["season"], g["game_id"], _LEAGUE_SELECT, _IN_WINDOW, params)[0]
        team = history_before(
            conn, g["season"], g["game_id"], _TEAM_SELECT,
            f"{_IN_WINDOW} AND (h.home_team_id = :team OR h.away_team_id = :team)", params,
        )[0]
        raw, offset = None, 0.0
        if team["n_home"] and team["n_away"]:
            raw = (team["diff_home"] - team["diff_away"]) / 2
            n = team["n_home"] + team["n_away"]
            offset = (raw - league["goal_diff"]) * n / (n + HOME_OFFSET_K)
        rows.append((
            g["game_id"], g["season"], league["n"], league["home_win"], league["draw"],
            league["away_win"], league["goal_diff"], team["n_home"], team["n_away"], raw, offset,
        ))
    conn.executemany("INSERT INTO features_home VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
