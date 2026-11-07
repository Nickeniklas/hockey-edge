"""Family 1: goalie quality for the resolved starter, and the team fallback.

Column definitions are on features_goalie in db.py. Who starts comes from
starters.resolve_starter; how good he is comes only from his earlier games,
read through history.history_before with game_goalies joined. The quality
number is leak-free whichever way the starter was resolved.

Windows count shots, not games, and run across seasons and teams. Save% is
shrunk toward the league rate with a default k; shots and raw save% are
stored so k can be tuned inside training folds.
"""

import sqlite3
from collections import Counter

from hockey_edge.features import starters
from hockey_edge.features.history import history_before, target_games

# This season and the two before it, as in home.py.
WINDOW_SEASONS = 3
# Goalie history: this season and the three before it. Enough to fill the
# long window for any regular starter; it keeps the build fast.
GOALIE_SEASONS = 4
SHORT_SHOTS = 500
LONG_SHOTS = 1500
# Shots. Save% is mostly noise below a thousand or so.
SV_K = 1000
# Team games with a known starter, this season or last, for the
# expected-goalie fallback.
EXPECTED_GAMES = 10
# Whole games with opponent xG, for gsax_per_game.
GSAX_GAMES = 20

_RATED = "h.feeds_ratings = 1"
_LEAGUE_SELECT = "SUM(p.shots_against - p.goals_against) AS saves, SUM(p.shots_against) AS shots"
_GOALIE_SELECT = (
    "h.start_utc AS start_utc, p.shots_against AS shots, p.goals_against AS goals, "
    "p.whole_game AS whole_game, p.opponent_xg AS opponent_xg"
)
_STARTS_SELECT = "h.start_utc AS start_utc, p.player_id AS player_id"


def _window(games: list[sqlite3.Row], shots_wanted: int) -> tuple[int, float | None]:
    """(shots, save%) over the most recent games until the shots are reached."""
    shots = goals = 0
    for g in games:
        if shots >= shots_wanted:
            break
        shots += g["shots"]
        goals += g["goals"]
    return shots, ((shots - goals) / shots if shots else None)


def _shrunk(shots: int, sv: float | None, league_sv: float | None) -> float | None:
    if league_sv is None:
        return sv
    saves = sv * shots if shots else 0.0
    return (saves + SV_K * league_sv) / (shots + SV_K)


class GoalieHistory:
    """History depends only on the target's date, so it is kept per date.

    The game passed in only locates the date; any game on it gives the same
    history.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._league: dict = {}
        self._goalies: dict = {}

    def league_sv(self, g: sqlite3.Row) -> float | None:
        key = (g["start_date"], g["season"])
        if key not in self._league:
            row = history_before(
                self.conn, g["season"], g["game_id"], _LEAGUE_SELECT,
                f"{_RATED} AND h.season >= :first_season",
                {"first_season": g["season"] - WINDOW_SEASONS + 1}, per_game="game_goalies",
            )[0]
            self._league[key] = (row["saves"] / row["shots"]) if row["shots"] else None
        return self._league[key]

    def goalie(self, g: sqlite3.Row, player_id: int) -> dict:
        key = (g["start_date"], g["season"], player_id)
        if key not in self._goalies:
            self._goalies[key] = self._goalie(g, player_id)
        return self._goalies[key]

    def _goalie(self, g: sqlite3.Row, player_id: int) -> dict:
        games = sorted(
            history_before(
                self.conn, g["season"], g["game_id"], _GOALIE_SELECT,
                f"{_RATED} AND p.season >= :first_season AND p.player_id = :player",
                {"first_season": g["season"] - GOALIE_SEASONS + 1, "player": player_id},
                per_game="game_goalies",
            ),
            key=lambda r: r["start_utc"], reverse=True,
        )
        shots_short, sv_short = _window(games, SHORT_SHOTS)
        shots_long, sv_long = _window(games, LONG_SHOTS)
        gsax = [r["opponent_xg"] - r["goals"] for r in games
                if r["whole_game"] and r["opponent_xg"] is not None][:GSAX_GAMES]
        return {
            "shots_short": shots_short, "sv_short": sv_short,
            "shots_long": shots_long, "sv_long": sv_long,
            "starter_sv": _shrunk(shots_long, sv_long, self.league_sv(g)),
            "gsax_n": len(gsax), "gsax_per_game": (sum(gsax) / len(gsax)) if gsax else None,
        }


def starter_sv_at(cache: GoalieHistory, g: sqlite3.Row, player_id: int) -> float | None:
    """starter_sv for any goalie as of game g (the skew report uses it)."""
    return cache.goalie(g, player_id)["starter_sv"]


def _expected(cache: GoalieHistory, g: sqlite3.Row, team_id: str, league_sv: float | None) -> tuple[int, float | None]:
    starts = sorted(
        history_before(
            cache.conn, g["season"], g["game_id"], _STARTS_SELECT,
            f"{_RATED} AND p.season >= :first_season AND p.team_id = :team AND p.is_actual_starter = 1",
            {"first_season": g["season"] - 1, "team": team_id},
            per_game="game_goalies",
        ),
        key=lambda r: r["start_utc"], reverse=True,
    )[:EXPECTED_GAMES]
    weights = Counter(r["player_id"] for r in starts)
    values = {pid: starter_sv_at(cache, g, pid) for pid in weights}
    weighted = [(w, values[pid]) for pid, w in weights.items() if values[pid] is not None]
    if not weighted:
        return len(starts), league_sv
    return len(starts), sum(w * v for w, v in weighted) / sum(w for w, _ in weighted)


def build(conn: sqlite3.Connection) -> None:
    cache = GoalieHistory(conn)
    rows = []
    for g in target_games(conn):
        league_sv = cache.league_sv(g)
        for role in ("home", "away"):
            team_id = g[f"{role}_team_id"]
            player_id, source = starters.resolve_starter(conn, g["season"], g["game_id"], role)
            n_starts, expected_sv = _expected(cache, g, team_id, league_sv)
            goalie = {"shots_short": None, "sv_short": None, "shots_long": None, "sv_long": None,
                      "starter_sv": None, "gsax_n": None, "gsax_per_game": None}
            if player_id is not None:
                goalie = cache.goalie(g, player_id)
            goalie_sv = expected_sv if source == starters.UNKNOWN else goalie["starter_sv"]
            rows.append((
                g["game_id"], g["season"], role, team_id, player_id, source, league_sv,
                goalie["shots_short"], goalie["sv_short"], goalie["shots_long"], goalie["sv_long"],
                goalie["starter_sv"], n_starts, expected_sv, goalie_sv,
                goalie["gsax_n"], goalie["gsax_per_game"],
            ))
    conn.executemany(
        "INSERT INTO features_goalie VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
