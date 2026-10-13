"""Ordered-logit link from a rating gap to H/D/A probabilities.

    P(A)      = s(c1 - beta * x)
    P(A or D) = s(c2 - beta * x)        c1 < c2, beta > 0

(docs/ELO_HARNESS_PLAN.md, design answer 1). Fitted on tuning seasons only
and frozen for the test season; the caller decides which games those are.
"""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

from hockey_edge.models import CLASSES

_EPS = 1e-12


@dataclass(frozen=True)
class LinkParams:
    c1: float
    c2: float
    beta: float


class LinkFitError(RuntimeError):
    pass


def predict(params: LinkParams, x) -> np.ndarray:
    """Rows of (P(H), P(D), P(A)) for each gap in x."""
    x = np.asarray(x, dtype=float)
    away = expit(params.c1 - params.beta * x)
    away_or_draw = expit(params.c2 - params.beta * x)
    return np.column_stack([1.0 - away_or_draw, away_or_draw - away, away])


def _unpack(theta) -> LinkParams:
    # c2 - c1 and beta are fitted on a log scale, so both stay positive.
    return LinkParams(theta[0], theta[0] + np.exp(theta[1]), np.exp(theta[2]))


def fit(x, results) -> LinkParams:
    """Maximum likelihood.

    `results` holds 'H', 'D' or 'A' per game, or an integer array of
    positions in CLASSES.
    """
    x = np.asarray(x, dtype=float)
    if isinstance(results, np.ndarray) and results.dtype.kind == "i":
        y = results
    else:
        y = np.array([CLASSES.index(r) for r in results], dtype=int)
    if len(x) != len(y) or not len(x):
        raise ValueError("x and results must be the same non-zero length")
    rates = np.bincount(y, minlength=3) / len(y)
    if (rates == 0).any():
        raise LinkFitError(f"a class has no games: H/D/A rates {rates}")
    rows = np.arange(len(y))

    def loss(theta):
        return -np.log(np.clip(predict(_unpack(theta), x)[rows, y], _EPS, None)).mean()

    c1, c2 = logit(rates[2]), logit(rates[2] + rates[1])
    fitted = minimize(loss, [c1, np.log(c2 - c1), np.log(np.log(10))], method="BFGS")
    if not np.isfinite(fitted.fun) or np.abs(fitted.jac).max() > 1e-3:
        raise LinkFitError(f"link fit did not converge: {fitted.message}")
    params = _unpack(fitted.x)
    return LinkParams(float(params.c1), float(params.c2), float(params.beta))
