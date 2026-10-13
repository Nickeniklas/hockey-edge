"""Market benchmark: stored predictions against closing lines.

The only harness module that reads odds (docs/ELO_HARNESS_PLAN.md, design
answer 9). It reads `benchmark_odds`, which already holds one vig-removed
row per game, and compares on exactly the games that have both a closing
line and a stored prediction.

A closing line is a row captured at most MAX_MINUTES before the start.
Pinnacle is the benchmark; games where only bet365 priced are a separate
group and are never pooled with it. No edge, ROI or staking is computed:
the sample cannot support it.
"""

import math
import sqlite3
from typing import NamedTuple

import numpy as np

from hockey_edge.validation import metrics

MAX_MINUTES = 60
# Log loss. The size of gap to the market the sample-size estimate is for.
TARGET_DIFFERENCE = 0.01
MARKET = "market"


class Line(NamedTuple):
    season: int
    game_id: int
    book: str
    is_fallback: int
    minutes_before_start: float
    p_home: float
    p_draw: float
    p_away: float


class BookComparison(NamedTuple):
    book: str
    is_fallback: int
    # (season, game_id) of the games compared.
    games: list[tuple[int, int]]
    # model (and MARKET) -> log loss per game, in the order of `games`.
    loss: dict[str, np.ndarray]


def closing_lines(features: sqlite3.Connection, max_minutes: float = MAX_MINUTES) -> list[Line]:
    return [
        Line(*row) for row in features.execute(
            "SELECT season, game_id, book, is_fallback, minutes_before_start, p_home, p_draw, p_away "
            "FROM benchmark_odds WHERE minutes_before_start <= ? ORDER BY season, game_id", (max_minutes,)
        )
    ]


def compare(backtest: sqlite3.Connection, features: sqlite3.Connection, run_id: int) -> list[BookComparison]:
    """One comparison per book, on ended regular-season games every model predicted."""
    eval_games = {
        (row[0], row[1]) for row in features.execute(
            "SELECT season, game_id FROM game_spine WHERE in_eval_sample = 1 AND ended = 1"
        )
    }
    stored: dict[tuple[int, int], dict[str, tuple]] = {}
    for model, season, game_id, *rest in backtest.execute(
        "SELECT model, season, game_id, p_home, p_draw, p_away, result FROM backtest_prediction "
        "WHERE run_id = ?", (run_id,)
    ):
        stored.setdefault((season, game_id), {})[model] = tuple(rest)
    models = sorted({m for by_model in stored.values() for m in by_model})

    by_book: dict[tuple[str, int], list[Line]] = {}
    for line in closing_lines(features):
        key = (line.season, line.game_id)
        if key in eval_games and set(stored.get(key, ())) == set(models):
            by_book.setdefault((line.book, line.is_fallback), []).append(line)

    comparisons = []
    for (book, is_fallback), lines in sorted(by_book.items(), key=lambda item: item[0][1]):
        results = [stored[line.season, line.game_id][models[0]][3] for line in lines]
        loss = {MARKET: metrics.game_log_loss([line[5:8] for line in lines], results)}
        for model in models:
            loss[model] = metrics.game_log_loss(
                [stored[line.season, line.game_id][model][:3] for line in lines], results
            )
        comparisons.append(BookComparison(book, is_fallback, [(line.season, line.game_id) for line in lines], loss))
    return comparisons


def games_needed(sd: float, difference: float = TARGET_DIFFERENCE) -> int:
    """Games for a mean per-game difference of `difference` to reach 2 SE."""
    return math.ceil((2 * sd / difference) ** 2)


def render(backtest: sqlite3.Connection, features: sqlite3.Connection, run_id: int, labels: dict[str, str]) -> list[str]:
    """Markdown lines for the results document."""
    lines = [
        "## Market benchmark", "",
        f"Closing line: the last parsed 1X2 row captured at most {MAX_MINUTES} minutes before the start, "
        "vig removed proportionally. Same games for every column. A positive difference means the market "
        "is better. Books are never pooled.", "",
    ]
    comparisons = compare(backtest, features, run_id)
    if not comparisons:
        return lines + ["No game has both a closing line and a stored prediction.", ""]
    for c in comparisons:
        models = [m for m in labels if m in c.loss]
        n = len(c.games)
        lines += [f"**{c.book}**{' (fallback: games Pinnacle did not price)' if c.is_fallback else ''}, {n} game{'s' if n != 1 else ''}", ""]
        lines += ["| Model | Log loss | Model − market (± SE) |", "|---|---|---|"]
        lines.append(f"| Market ({c.book}) | {c.loss[MARKET].mean():.4f} | |")
        for model in models:
            diff = "n/a"
            if n >= 2:
                paired = metrics.paired_difference(c.loss[model], c.loss[MARKET])
                diff = f"{paired.mean:+.4f} ± {paired.se:.4f}"
            lines.append(f"| {labels[model]} | {c.loss[model].mean():.4f} | {diff} |")
        lines.append("")
        if n >= 2 and "elo" in c.loss:
            sd = float((c.loss["elo"] - c.loss[MARKET]).std(ddof=1))
            lines += [
                f"Per-game SD of Elo − market: {sd:.4f}. A true gap of {TARGET_DIFFERENCE} would need about "
                f"{games_needed(sd):,} games to reach 2 SE; this sample has {n}.", "",
            ]
    return lines
