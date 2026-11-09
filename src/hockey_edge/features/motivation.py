"""Family 7: motivation, the team's place relative to the playoff and
relegation lines.

Column definitions are on features_motivation in db.py. The table before a
game is built from the season's RUNKOSARJA games read through
history.history_before, so a game on the same UTC date never counts. The
lines come from season_format (curated spots); the team list from
team_season. games_remaining uses the nominal schedule
(season_format.games_per_team), which was known before the season; counting
listed games instead would leak the 2020 cut and the 2021-2022
cancellations, which came mid-season. In every complete season the two are
the same.
"""

import sqlite3

from hockey_edge.features import season_format
from hockey_edge.features.history import history_before, target_games

PHASE = "RUNKOSARJA"
POINTS_PER_GAME_LEFT = 3

_GAMES_SELECT = (
    "h.home_team_id AS home, h.away_team_id AS away, h.home_goals AS home_goals, "
    "h.away_goals AS away_goals, h.end_type AS end_type"
)
_SEASON = "h.season = :season AND h.phase = 'RUNKOSARJA'"


def game_points(end_type: str, goals_for: int, goals_against: int) -> int:
    """3 for a regulation win, 2 for an OT/SO win, 1 for an OT/SO loss, 0
    for a regulation loss."""
    if goals_for == goals_against:
        raise ValueError(f"a regular-season game cannot end tied ({end_type} {goals_for}-{goals_against})")
    won = goals_for > goals_against
    if end_type == "REG":
        return 3 if won else 0
    return 2 if won else 1


def table(teams: list[str], games) -> list[dict]:
    """The table from ended games, best first: points, then goal difference
    (team id last, only to make the order deterministic)."""
    rows = {t: {"team_id": t, "games_played": 0, "points": 0, "goal_diff": 0} for t in teams}
    for g in games:
        for team, gf, ga in ((g["home"], g["home_goals"], g["away_goals"]),
                             (g["away"], g["away_goals"], g["home_goals"])):
            row = rows[team]
            row["games_played"] += 1
            row["points"] += game_points(g["end_type"], gf, ga)
            row["goal_diff"] += gf - ga
    ranked = sorted(rows.values(), key=lambda r: (-r["points"], -r["goal_diff"], r["team_id"]))
    for rank, row in enumerate(ranked, 1):
        row["table_rank"] = rank
    return ranked


def team_values(ranked: list[dict], team_id: str, fmt) -> list:
    """features_motivation values after team_id, in column order."""
    n, spots, zone = len(ranked), fmt["playoff_spots"], fmt["relegation_spots"]
    me = next(r for r in ranked if r["team_id"] == team_id)
    others = [r for r in ranked if r["team_id"] != team_id]
    points = [r["points"] for r in ranked]

    def remaining(r):
        return max(fmt["games_per_team"] - r["games_played"], 0)

    def best(r):
        return r["points"] + POINTS_PER_GAME_LEFT * remaining(r)

    rank = me["table_rank"]
    can_reach = sum(best(r) >= me["points"] for r in others)
    cannot_reach = len(others) - can_reach
    already_above = sum(r["points"] > best(me) for r in others)

    playoff_gap = me["points"] - (points[spots] if rank <= spots else points[spots - 1])
    values = [
        me["games_played"], me["points"], me["goal_diff"], rank, remaining(me),
        playoff_gap, int(can_reach <= spots - 1), int(already_above >= spots),
    ]
    if not zone:
        return values + [0, None, None, None]
    safe_places = n - zone
    relegation_gap = me["points"] - (points[safe_places] if rank <= safe_places else points[safe_places - 1])
    return values + [1, relegation_gap, int(cannot_reach >= zone), int(already_above >= safe_places)]


class Standings:
    """The table depends only on the target's season and date."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._tables: dict = {}
        self._teams: dict = {}
        self._formats: dict = {}

    def format(self, season: int):
        if season not in self._formats:
            self._formats[season] = season_format.season_row(self.conn, season)
        return self._formats[season]

    def __call__(self, g: sqlite3.Row) -> list[dict]:
        key = (g["season"], g["start_date"])
        if key not in self._tables:
            if g["season"] not in self._teams:
                self._teams[g["season"]] = season_format.season_teams(self.conn, g["season"])
            games = history_before(
                self.conn, g["season"], g["game_id"], _GAMES_SELECT, _SEASON, {"season": g["season"]}
            )
            self._tables[key] = table(self._teams[g["season"]], games)
        return self._tables[key]


def build(conn: sqlite3.Connection) -> None:
    standings = Standings(conn)
    rows = []
    for g in target_games(conn):
        for role in ("home", "away"):
            team_id = g[f"{role}_team_id"]
            values = [None] * 12
            if g["phase"] == PHASE:
                values = team_values(standings(g), team_id, standings.format(g["season"]))
            rows.append([g["game_id"], g["season"], role, team_id, *values])
    if rows:
        conn.executemany(
            f"INSERT INTO features_motivation VALUES ({', '.join('?' * len(rows[0]))})", rows
        )
