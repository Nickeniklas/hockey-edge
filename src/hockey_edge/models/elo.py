"""Elo ratings for Liiga (docs/ELO_HARNESS_PLAN.md, design answers 2-5).

One pass over the rating games gives every game its pre-game gap. The pass
works one UTC date at a time: every game on a date is predicted from the
ratings as they stood after the previous date, then that date's results are
applied. That is the same cut as features/history.py, and
tests/models/test_elo.py checks the two agree game by game.

Pure: reads game_spine and team_season, writes nothing.
"""

import math
import sqlite3
from dataclasses import dataclass
from itertools import groupby
from typing import NamedTuple

START_RATING = 1500.0
# Goals. Empty-net goals inflate margins beyond this.
MARGIN_CAP = 3


@dataclass(frozen=True)
class EloParams:
    k: float = 8.0
    hfa: float = 40.0
    # Share of the way a returning team moves to the returning-team mean.
    r: float = 0.35
    margin: bool = False
    # A game drawn in regulation scores 0.5 +/- s_ot toward its OT/SO winner.
    s_ot: float = 0.0
    # A first-season team's K is k * cold_m for its first cold_n games.
    cold_m: float = 2.0
    cold_n: int = 15
    # Entry rating = returning mean - cold_delta. None derives it from
    # earlier first-season teams; a number fixes it (sensitivity runs).
    cold_delta: float | None = None


class Game(NamedTuple):
    season: int
    game_id: int
    start_date: str
    home: str
    away: str
    ended: int
    home_goals: int | None
    away_goals: int | None
    result: str | None
    in_eval: int


class Rated(NamedTuple):
    """A game as the pass saw it. Post ratings are None until it has ended."""
    season: int
    game_id: int
    gap: float
    home_pre: float
    away_pre: float
    home_post: float | None
    away_post: float | None
    # Number of entries of EloResult.applied that were in before this game.
    applied_before: int


class ColdStart(NamedTuple):
    season: int
    team_id: str
    path: str  # 'delta', 'lowest_returning' or 'fixed'
    entry: float
    returning_mean: float
    delta: float
    n_cases: int
    mean_goal_diff: float | None
    slope: float | None


@dataclass
class EloResult:
    params: EloParams
    games: list[Rated]
    applied: list[tuple[int, int]]
    cold_starts: list[ColdStart]
    ratings: dict[str, float]


def load_games(conn: sqlite3.Connection) -> list[Game]:
    """Every game that feeds ratings, played or not, in pass order."""
    return [
        Game(*row) for row in conn.execute(
            "SELECT season, game_id, start_date, home_team_id, away_team_id, ended, "
            "home_goals, away_goals, regulation_result, in_eval_sample "
            "FROM game_spine WHERE feeds_ratings = 1 ORDER BY start_date, season, game_id"
        )
    ]


def load_team_seasons(conn: sqlite3.Connection) -> dict[int, dict[str, bool]]:
    """season -> team_id -> is this the team's first Liiga season.

    Who is in the league is known before a season starts, so the pass may
    use a season's whole team list at its first game.
    """
    team_seasons: dict[int, dict[str, bool]] = {}
    for season, team_id, first in conn.execute(
        "SELECT season, team_id, is_first_liiga_season FROM team_season"
    ):
        team_seasons.setdefault(season, {})[team_id] = bool(first)
    return team_seasons


def expected_score(home_rating: float, away_rating: float, hfa: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-(home_rating + hfa - away_rating) / 400.0))


def actual_score(result: str, home_goals: int, away_goals: int, s_ot: float) -> float:
    """Home team's score. A tied final (2022:49294) has no OT/SO winner."""
    if result == "H":
        return 1.0
    if result == "A":
        return 0.0
    if home_goals > away_goals:
        return 0.5 + s_ot
    if away_goals > home_goals:
        return 0.5 - s_ot
    return 0.5


def margin_multiplier(margin: int, winner_gap: float) -> float:
    """winner_gap is the winner's pre-game Elo advantage, home edge included."""
    return math.log(min(abs(margin), MARGIN_CAP) + 1) * 2.2 / (2.2 + 0.001 * winner_gap)


class _Slope:
    """Running regression of final goal margin on pre-game rating gap."""

    def __init__(self) -> None:
        self.n = self.sx = self.sy = self.sxx = self.sxy = 0.0

    def add(self, x: float, y: float) -> None:
        self.n += 1
        self.sx += x
        self.sy += y
        self.sxx += x * x
        self.sxy += x * y

    def value(self) -> float | None:
        if not self.n:
            return None
        var = self.sxx - self.sx * self.sx / self.n
        if var <= 0:
            return None
        return (self.sxy - self.sx * self.sy / self.n) / var


def run(games: list[Game], team_seasons: dict[int, dict[str, bool]], params: EloParams) -> EloResult:
    """One pass. `games` must be in date order with seasons not interleaved."""
    ratings: dict[str, float] = {}
    rated: list[Rated] = []
    applied: list[tuple[int, int]] = []
    cold_starts: list[ColdStart] = []
    slope = _Slope()
    # (season, team_id) -> [goal difference, games] over that first season.
    cases: dict[tuple[int, str], list[float]] = {}
    seasons_done: set[int] = set()
    last_date = ""

    for season, season_iter in groupby(games, key=lambda g: g.season):
        if season in seasons_done:
            raise ValueError(f"season {season} is interleaved with another season")
        season_games = list(season_iter)
        ratings, entered = _start_season(
            season, team_seasons[season], ratings, params,
            [c for key, c in cases.items() if c[1]], slope.value(),
        )
        cold_starts.extend(entered)
        boost = {c.team_id: params.cold_n for c in entered}
        for c in entered:
            cases[(season, c.team_id)] = [0.0, 0]

        for date, batch_iter in groupby(season_games, key=lambda g: g.start_date):
            if date <= last_date:
                raise ValueError(f"games are not in date order at {date}")
            last_date = date
            batch = list(batch_iter)
            playing = [t for g in batch for t in (g.home, g.away)]
            if len(playing) != len(set(playing)):
                raise ValueError(f"a team plays twice on {date}")
            seen = len(applied)
            # No team is in two games of the batch, so each update below
            # touches only ratings no other game of the date reads.
            for g in batch:
                home_pre, away_pre = ratings[g.home], ratings[g.away]
                diff = home_pre + params.hfa - away_pre
                home_post = away_post = None
                if g.ended:
                    margin = g.home_goals - g.away_goals
                    step = actual_score(g.result, g.home_goals, g.away_goals, params.s_ot) - expected_score(
                        home_pre, away_pre, params.hfa
                    )
                    if params.margin and g.result != "D":
                        step *= margin_multiplier(margin, diff if margin > 0 else -diff)
                    home_post = home_pre + params.k * _k_factor(g.home, boost, params) * step
                    away_post = away_pre - params.k * _k_factor(g.away, boost, params) * step
                    ratings[g.home], ratings[g.away] = home_post, away_post
                    slope.add(home_pre - away_pre, margin)
                    for team, diff_for in ((g.home, margin), (g.away, -margin)):
                        if (season, team) in cases:
                            cases[(season, team)][0] += diff_for
                            cases[(season, team)][1] += 1
                    applied.append((g.season, g.game_id))
                rated.append(Rated(g.season, g.game_id, diff / 400.0, home_pre, away_pre, home_post, away_post, seen))
        seasons_done.add(season)

    return EloResult(params, rated, applied, cold_starts, ratings)


def _k_factor(team: str, boost: dict[str, int], params: EloParams) -> float:
    if boost.get(team, 0) > 0:
        boost[team] -= 1
        return params.cold_m
    return 1.0


def _start_season(
    season: int,
    teams: dict[str, bool],
    ratings: dict[str, float],
    params: EloParams,
    cases: list[list[float]],
    slope: float | None,
) -> tuple[dict[str, float], list[ColdStart]]:
    """Ratings at the start of `season`, and the teams that entered cold."""
    if not ratings:
        # First stored season: nobody has a history, including its newcomers.
        return {team: START_RATING for team in teams}, []

    returning = sorted(t for t, first in teams.items() if not first)
    newcomers = sorted(t for t, first in teams.items() if first)
    unknown = [t for t in returning if t not in ratings]
    if unknown:
        raise ValueError(f"season {season}: no rating and no first-season flag for {unknown}")

    # Teams that left are dropped here, so they never enter the mean.
    mean = sum(ratings[t] for t in returning) / len(returning)
    start = {t: ratings[t] + params.r * (mean - ratings[t]) for t in returning}

    entered = []
    mean_goal_diff = sum(c[0] / c[1] for c in cases) / len(cases) if cases else None
    for team in newcomers:
        if params.cold_delta is not None:
            path, delta = "fixed", params.cold_delta
        elif cases and slope is not None and slope > 0:
            path, delta = "delta", -mean_goal_diff / slope
        else:
            path, delta = "lowest_returning", mean - min(start[t] for t in returning)
        entered.append(ColdStart(
            season, team, path, mean - delta, mean, delta, len(cases), mean_goal_diff, slope
        ))
    for c in entered:
        start[c.team_id] = c.entry
    return start, entered
