"""The two simple baselines (docs/ELO_HARNESS_PLAN.md, design answer 7).

B0, base rates: the floor. B1, a goal-difference rating: a naive form table,
there to show whether Elo beats one. Neither is tuned. Both read the same
rating games as Elo and follow the same date-batch rule.
"""

from itertools import groupby

import numpy as np

from hockey_edge.models import CLASSES
from hockey_edge.models.elo import Game

# Games. A team's rating is its season's goal difference per game with this
# many games of prior mixed in.
GD_PRIOR_GAMES = 10
# The prior is last season's goal difference per game, pulled this far to 0.
GD_CARRY = 0.5


def base_rates(results) -> np.ndarray:
    """H/D/A frequencies of the given results."""
    return np.array([sum(r == c for r in results) for c in CLASSES]) / len(results)


def goal_diff_gaps(games: list[Game]) -> list[float]:
    """Home rating minus away rating before each game, in goals per game.

    Final-score goal difference over the season's earlier rating games,
    shrunk to the prior by n / (n + GD_PRIOR_GAMES). A team with no
    previous season starts from 0. `games` must be in pass order.
    """
    gaps = []
    previous: dict[str, float] = {}
    for _, season_iter in groupby(games, key=lambda g: g.season):
        prior = {team: GD_CARRY * value for team, value in previous.items()}
        total: dict[str, list[float]] = {}  # team -> [goal difference, games]

        def rating(team: str) -> float:
            diff, n = total.get(team, (0.0, 0))
            return (diff + GD_PRIOR_GAMES * prior.get(team, 0.0)) / (n + GD_PRIOR_GAMES)

        for _, batch_iter in groupby(season_iter, key=lambda g: g.start_date):
            batch = list(batch_iter)
            gaps.extend(rating(g.home) - rating(g.away) for g in batch)
            for g in batch:
                if g.ended:
                    margin = g.home_goals - g.away_goals
                    for team, diff in ((g.home, margin), (g.away, -margin)):
                        entry = total.setdefault(team, [0.0, 0])
                        entry[0] += diff
                        entry[1] += 1
        # A team that sat a season out comes back with no prior.
        previous = {team: diff / n for team, (diff, n) in total.items()}
    return gaps
