"""Walk-forward folds (docs/ELO_HARNESS_PLAN.md, design answer 6).

A fold tests one season with everything fitted on earlier seasons only.
Seasons are never shuffled.
"""

from typing import NamedTuple


class Fold(NamedTuple):
    test_season: int
    tuning_seasons: tuple[int, ...]


def make_folds(seasons, burn_in: int, first_test: int) -> list[Fold]:
    """One fold per season from `first_test` on.

    The first `burn_in` seasons only warm the ratings up: they are never
    tuned on or tested. Every fold tunes on all seasons between the burn-in
    and its test season.
    """
    seasons = sorted(seasons)
    usable = seasons[burn_in:]
    folds = [Fold(n, tuple(s for s in usable if s < n)) for n in usable if n >= first_test]
    if not folds:
        raise ValueError(f"no season from {first_test} on in {seasons}")
    if not folds[0].tuning_seasons:
        raise ValueError(f"test season {folds[0].test_season} has no tuning season before it")
    return folds
