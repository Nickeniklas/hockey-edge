"""Family 3: special teams, rolling per team.

Column definitions are on features_special_teams in db.py. History is read
only through history.history_before with game_team_special_teams joined,
so a game's windows hold only games from earlier dates.

PP% = power-play goals / opportunities, PK% = 1 - power-play goals against
/ times shorthanded, both summed over the window and shrunk hard toward the
league PP% (k opportunities; PK% toward 1 - league PP%, the same league
rate seen from the other side). The window's raw counts are stored, so k
can be tuned inside training folds. Penalties taken and drawn are per game,
not shrunk. Games with no penalty data (NULL counts) are skipped, not
counted as 0.
"""

import sqlite3

from hockey_edge.features.history import history_before, target_games

WINDOWS = (10, 25, 40)
# This season and the one before it, for the team windows and the league
# rate alike.
LOOKBACK_SEASONS = 2
# Opportunities. A team draws about three a game, so a 40-game window
# (~120) is still shrunk about halfway: PP% over a few dozen chances is
# mostly noise.
PP_K = 150

_RATED = "h.feeds_ratings = 1 AND p.season >= :first_season AND p.pp_opportunities IS NOT NULL"
_LEAGUE_SELECT = "SUM(p.pp_goals) AS goals, SUM(p.pp_opportunities) AS opps"
_TEAM_SELECT = (
    "h.start_utc AS start_utc, h.season AS season, "
    "p.pp_opportunities AS pp_opp, p.pp_goals AS pp_goals, "
    "p.pk_opportunities AS pk_opp, p.pp_goals_against AS pk_ga, "
    "p.penalties_taken AS taken, p.penalties_drawn AS drawn"
)


def shrunk(goals: int, opps: int, league: float | None) -> float | None:
    """(goals + k * league) / (opps + k); the raw rate without a league rate."""
    if league is None:
        return goals / opps if opps else None
    return (goals + PP_K * league) / (opps + PP_K)


def _per_game(games: list[sqlite3.Row], key: str) -> float | None:
    return sum(g[key] for g in games) / len(games) if games else None


class LeagueRate:
    """League PP% before a date, kept per (date, season)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._cache: dict = {}

    def __call__(self, g: sqlite3.Row) -> float | None:
        key = (g["start_date"], g["season"])
        if key not in self._cache:
            row = history_before(
                self.conn, g["season"], g["game_id"], _LEAGUE_SELECT, _RATED,
                {"first_season": g["season"] - LOOKBACK_SEASONS + 1}, per_game="game_team_special_teams",
            )[0]
            self._cache[key] = (row["goals"] / row["opps"]) if row["opps"] else None
        return self._cache[key]


def team_row(conn: sqlite3.Connection, g: sqlite3.Row, team_id: str, league: float | None) -> list:
    """features_special_teams values after league_pp_pct, in column order."""
    games = sorted(
        history_before(
            conn, g["season"], g["game_id"], _TEAM_SELECT, f"{_RATED} AND p.team_id = :team",
            {"first_season": g["season"] - LOOKBACK_SEASONS + 1, "team": team_id},
            per_game="game_team_special_teams",
        ),
        key=lambda r: r["start_utc"], reverse=True,
    )
    league_pk = None if league is None else 1 - league
    row = [sum(r["season"] == g["season"] for r in games)]
    for n in WINDOWS:
        w = games[:n]
        pp_opp, pp_goals = sum(r["pp_opp"] for r in w), sum(r["pp_goals"] for r in w)
        pk_opp, pk_ga = sum(r["pk_opp"] for r in w), sum(r["pk_ga"] for r in w)
        pp_pct = shrunk(pp_goals, pp_opp, league)
        # PK% = share of times shorthanded without a goal against.
        pk_pct = shrunk(pk_opp - pk_ga, pk_opp, league_pk)
        row += [
            len(w), pp_opp, pp_goals, pp_pct, pk_opp, pk_ga, pk_pct,
            _per_game(w, "taken"), _per_game(w, "drawn"),
        ]
    return row


def build(conn: sqlite3.Connection) -> None:
    league_rate = LeagueRate(conn)
    rows = []
    for g in target_games(conn):
        league = league_rate(g)
        for role in ("home", "away"):
            team_id = g[f"{role}_team_id"]
            rows.append([g["game_id"], g["season"], role, team_id, league, *team_row(conn, g, team_id, league)])
    if rows:
        conn.executemany(
            f"INSERT INTO features_special_teams VALUES ({', '.join('?' * len(rows[0]))})", rows
        )
