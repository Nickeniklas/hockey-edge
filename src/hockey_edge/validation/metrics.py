"""Metrics for three-class predictions (docs/ELO_HARNESS_PLAN.md, design answer 8).

Pure functions. `probs` is an (n, 3) array in the order of models.CLASSES
(home, draw, away); `results` holds 'H', 'D' or 'A' per game. Log loss is the
metric; accuracy is reported and never optimised.
"""

from typing import NamedTuple

import numpy as np

from hockey_edge.models import CLASSES

BIN_WIDTH = 0.05
MIN_BIN_GAMES = 30


class Paired(NamedTuple):
    mean: float
    se: float
    n: int


class CalibrationBin(NamedTuple):
    low: float
    high: float
    n: int
    mean_predicted: float
    observed: float


def outcomes(results) -> np.ndarray:
    """(n, 3) array with a 1 in the column of each game's result."""
    index = np.array([CLASSES.index(r) for r in results], dtype=int)
    onehot = np.zeros((len(index), len(CLASSES)))
    onehot[np.arange(len(index)), index] = 1.0
    return onehot


def game_log_loss(probs, results) -> np.ndarray:
    """Natural-log loss per game."""
    return -np.log((np.asarray(probs, dtype=float) * outcomes(results)).sum(axis=1))


def log_loss(probs, results) -> float:
    return float(game_log_loss(probs, results).mean())


def brier(probs, results) -> float:
    """Multi-class: squared error summed over the three classes."""
    return float(((np.asarray(probs, dtype=float) - outcomes(results)) ** 2).sum(axis=1).mean())


def rps(probs, results) -> float:
    """Ranked probability score over the order home > draw > away."""
    gaps = np.cumsum(np.asarray(probs, dtype=float), axis=1) - np.cumsum(outcomes(results), axis=1)
    return float((gaps[:, :-1] ** 2).sum(axis=1).mean() / (len(CLASSES) - 1))


def accuracy(probs, results) -> float:
    return float((np.asarray(probs).argmax(axis=1) == outcomes(results).argmax(axis=1)).mean())


def paired_difference(a, b) -> Paired:
    """Mean of per-game a - b with its standard error. Same games, same order."""
    diff = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    if len(diff) < 2:
        raise ValueError("a paired difference needs at least two games")
    return Paired(float(diff.mean()), float(diff.std(ddof=1) / np.sqrt(len(diff))), len(diff))


def calibration_bins(predicted, happened) -> list[CalibrationBin]:
    """Bins of width 0.05, merged upward until each holds MIN_BIN_GAMES.

    A short remainder at the top joins the bin below it, so only a sample
    smaller than MIN_BIN_GAMES gives a bin under the minimum.
    """
    predicted = np.asarray(predicted, dtype=float)
    happened = np.asarray(happened, dtype=float)
    n_bins = round(1 / BIN_WIDTH)
    which = np.minimum((predicted / BIN_WIDTH).astype(int), n_bins - 1)
    groups: list[list[int]] = []  # [first bin, last bin, games]
    open_group = None
    for b in range(n_bins):
        count = int((which == b).sum())
        if open_group is None:
            open_group = [b, b, 0]
        open_group[1] = b
        open_group[2] += count
        if open_group[2] >= MIN_BIN_GAMES:
            groups.append(open_group)
            open_group = None
    if open_group is not None and open_group[2]:
        if groups:
            groups[-1][1], groups[-1][2] = open_group[1], groups[-1][2] + open_group[2]
        else:
            groups.append(open_group)
    elif open_group is not None and groups:
        groups[-1][1] = open_group[1]

    bins = []
    for first, last, count in groups:
        inside = (which >= first) & (which <= last)
        bins.append(CalibrationBin(
            round(first * BIN_WIDTH, 2), round((last + 1) * BIN_WIDTH, 2), count,
            float(predicted[inside].mean()), float(happened[inside].mean()),
        ))
    return bins


def expected_calibration_error(bins: list[CalibrationBin]) -> float:
    total = sum(b.n for b in bins)
    return sum(b.n * abs(b.observed - b.mean_predicted) for b in bins) / total


def calibration(probs, results) -> dict[str, list[CalibrationBin]]:
    """Calibration bins per class."""
    probs, onehot = np.asarray(probs, dtype=float), outcomes(results)
    return {c: calibration_bins(probs[:, i], onehot[:, i]) for i, c in enumerate(CLASSES)}


def calibration_in_the_large(probs, results) -> dict[str, tuple[float, float]]:
    """Per class: (mean predicted, observed rate)."""
    probs, onehot = np.asarray(probs, dtype=float), outcomes(results)
    return {c: (float(probs[:, i].mean()), float(onehot[:, i].mean())) for i, c in enumerate(CLASSES)}
