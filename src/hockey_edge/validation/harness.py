"""Walk-forward backtest (docs/ELO_HARNESS_PLAN.md, design answers 6, 7, 10).

Every model goes through the same folds. For a test season, everything
fitted (the Elo config, each link, the base rates) comes from the tuning
seasons before it. Ratings are online, so one pass over all history gives
each game its pre-game gap, and a tuning game's gap never depends on a
later game (tests/models/test_elo.py).

Output is data/backtest.db. Runs are append-only: a run gets a new run_id
and no row is ever updated. This is not the prediction log.
"""

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import NamedTuple

import numpy as np

from hockey_edge.models import CLASSES, baselines, elo, link
from hockey_edge.validation import BACKTEST_VERSION
from hockey_edge.validation.folds import Fold, make_folds

BACKTEST_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "backtest.db"

ELO, BASE_RATE, GD_RATING = "elo", "base_rate", "gd_rating"
# Elo's chosen config with first-season teams entering at the returning
# mean and learning at normal K. Run with Elo, never tuned on its own.
ELO_FLAT_ENTRY = "elo_flat_entry"
MODELS = (ELO, BASE_RATE, GD_RATING)

# The home edge is not tuned (decided 2026-10-13). The link's cutpoints carry
# a constant home edge, so the tuning loss cannot identify hfa: it fell by
# under 0.001 from 80 to -80 with no minimum, and every fold chose the grid's
# lowest value (docs/ELO_RESULTS.md).
FIXED_PARAMS = {"hfa": 0.0}

# r was widened from the plan's (0.2 ... 0.7) after the 2018 fold chose 0.7.
DEFAULT_GRID = {
    "k": (4, 6, 8, 10, 14, 18),
    "r": (0.2, 0.35, 0.5, 0.7, 0.85),
    "margin": (False, True),
    "s_ot": (0.0, 0.15),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_run (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    models TEXT NOT NULL,
    model_version TEXT NOT NULL,
    code_version TEXT NOT NULL,
    -- build_meta of the features.db the run read, without its build time.
    features_build TEXT NOT NULL,
    config_json TEXT NOT NULL
);

-- What each fold fitted on its tuning seasons. grid_edges_json names the
-- grid dimensions whose chosen value is the lowest or highest tried.
CREATE TABLE IF NOT EXISTS backtest_fold (
    run_id INTEGER NOT NULL REFERENCES backtest_run (run_id),
    model TEXT NOT NULL,
    test_season INTEGER NOT NULL,
    tuning_seasons TEXT NOT NULL,
    chosen_params_json TEXT,
    link_params_json TEXT,
    grid_edges_json TEXT,
    PRIMARY KEY (run_id, model, test_season)
);

-- One row per ended rating game of a test season, playoffs included.
-- gap is the model's input to its link; ratings are Elo only.
CREATE TABLE IF NOT EXISTS backtest_prediction (
    run_id INTEGER NOT NULL REFERENCES backtest_run (run_id),
    model TEXT NOT NULL,
    season INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    test_season INTEGER NOT NULL,
    p_home REAL NOT NULL,
    p_draw REAL NOT NULL,
    p_away REAL NOT NULL,
    gap REAL,
    rating_home_pre REAL,
    rating_away_pre REAL,
    result TEXT NOT NULL CHECK (result IN ('H', 'D', 'A')),
    PRIMARY KEY (run_id, model, season, game_id)
);
"""


@dataclass(frozen=True)
class BacktestConfig:
    models: tuple[str, ...] = MODELS
    # Leading seasons that only warm the ratings up.
    burn_in: int = 1
    first_test: int = 2018
    grid: dict = field(default_factory=lambda: dict(DEFAULT_GRID))
    # Elo parameters held at one value. A grid entry of the same name wins.
    fixed: dict = field(default_factory=lambda: dict(FIXED_PARAMS))

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


@dataclass
class Data:
    games: list[elo.Game]
    team_seasons: dict[int, dict[str, bool]]
    features_build: str


class Prediction(NamedTuple):
    season: int
    game_id: int
    p_home: float
    p_draw: float
    p_away: float
    gap: float | None
    rating_home_pre: float | None
    rating_away_pre: float | None
    result: str


@dataclass
class FoldResult:
    model: str
    fold: Fold
    chosen_params: dict | None
    link_params: dict | None
    grid_edges: dict | None
    predictions: list[Prediction]


def load(conn: sqlite3.Connection) -> Data:
    """Read a features.db. The only tables used are game_spine, team_season and build_meta."""
    meta = conn.execute(
        "SELECT code_version, hockey_max_fetched_at, snapshots_max_captured_at, row_counts FROM build_meta"
    ).fetchone()
    build = dict(zip(("code_version", "hockey_max_fetched_at", "snapshots_max_captured_at"), meta[:3]))
    build["row_counts"] = json.loads(meta[3])
    return Data(elo.load_games(conn), elo.load_team_seasons(conn), json.dumps(build, sort_keys=True))


class _Rows:
    """Positions in the game list, per season, plus results as class indices."""

    def __init__(self, games: list[elo.Game]) -> None:
        self.games = games
        self.y = np.array([CLASSES.index(g.result) if g.ended else -1 for g in games], dtype=int)
        self.eval: dict[int, list[int]] = {}
        self.ended: dict[int, list[int]] = {}
        for i, g in enumerate(games):
            if g.ended:
                self.ended.setdefault(g.season, []).append(i)
                if g.in_eval:
                    self.eval.setdefault(g.season, []).append(i)

    def tuning(self, fold: Fold) -> np.ndarray:
        """Ended regular-season games of the fold's tuning seasons."""
        return np.array([i for s in fold.tuning_seasons for i in self.eval.get(s, [])], dtype=int)

    def test(self, fold: Fold) -> list[int]:
        return self.ended.get(fold.test_season, [])


def _log_loss(probs: np.ndarray, y: np.ndarray) -> float:
    return float(-np.log(probs[np.arange(len(y)), y]).mean())


def _fit_link(gaps: np.ndarray, rows: _Rows, fold: Fold) -> tuple[link.LinkParams, float, int]:
    tuning = rows.tuning(fold)
    fitted = link.fit(gaps[tuning], rows.y[tuning])
    return fitted, _log_loss(link.predict(fitted, gaps[tuning]), rows.y[tuning]), len(tuning)


def _predict(rows: _Rows, fold: Fold, probs: np.ndarray, gaps=None, rated=None) -> list[Prediction]:
    """`probs` has one row per test game, in the order of rows.test(fold)."""
    out = []
    for p, i in zip(probs, rows.test(fold)):
        g = rows.games[i]
        out.append(Prediction(
            g.season, g.game_id, float(p[0]), float(p[1]), float(p[2]),
            None if gaps is None else float(gaps[i]),
            None if rated is None else rated[i].home_pre,
            None if rated is None else rated[i].away_pre,
            g.result,
        ))
    return out


def _link_json(fitted: link.LinkParams, loss: float, n: int) -> dict:
    return {**asdict(fitted), "tuning_log_loss": loss, "tuning_games": n}


def _elo_grid(grid: dict, fixed: dict) -> list[elo.EloParams]:
    return [elo.EloParams(**{**fixed, **dict(zip(grid, values))}) for values in product(*grid.values())]


def _grid_edges(params: elo.EloParams, grid: dict) -> dict:
    """Dimensions where the choice sits on the edge of what was tried."""
    edges = {}
    for name, values in grid.items():
        if len(values) > 2 and getattr(params, name) in (min(values), max(values)):
            edges[name] = "low" if getattr(params, name) == min(values) else "high"
    return edges


def _elo_fold(model: str, data: Data, rows: _Rows, fold: Fold, params: elo.EloParams, edges) -> FoldResult:
    result = elo.run(data.games, data.team_seasons, params)
    gaps = np.array([r.gap for r in result.games])
    fitted, loss, n = _fit_link(gaps, rows, fold)
    probs = link.predict(fitted, gaps[rows.test(fold)])
    return FoldResult(
        model, fold, asdict(params), _link_json(fitted, loss, n), edges,
        _predict(rows, fold, probs, gaps, result.games),
    )


def _elo(data: Data, rows: _Rows, folds: list[Fold], grid: dict, fixed: dict) -> list[FoldResult]:
    best: dict[int, tuple[float, elo.EloParams]] = {}
    for params in _elo_grid(grid, fixed):
        gaps = np.array([r.gap for r in elo.run(data.games, data.team_seasons, params).games])
        for fold in folds:
            loss = _fit_link(gaps, rows, fold)[1]
            # Strictly lower: a tie keeps the config that came first.
            if fold.test_season not in best or loss < best[fold.test_season][0]:
                best[fold.test_season] = (loss, params)
    results = []
    for fold in folds:
        params = best[fold.test_season][1]
        results.append(_elo_fold(ELO, data, rows, fold, params, _grid_edges(params, grid)))
        results.append(_elo_fold(
            ELO_FLAT_ENTRY, data, rows, fold, replace(params, cold_delta=0.0, cold_m=1.0), None
        ))
    return results


def _base_rate(rows: _Rows, folds: list[Fold]) -> list[FoldResult]:
    results = []
    for fold in folds:
        rates = baselines.base_rates([rows.games[i].result for i in rows.tuning(fold)])
        probs = np.tile(rates, (len(rows.test(fold)), 1))
        results.append(FoldResult(
            BASE_RATE, fold, dict(zip(("p_home", "p_draw", "p_away"), rates.tolist())), None, None,
            _predict(rows, fold, probs),
        ))
    return results


def _gd_rating(data: Data, rows: _Rows, folds: list[Fold]) -> list[FoldResult]:
    gaps = np.array(baselines.goal_diff_gaps(data.games))
    results = []
    for fold in folds:
        fitted, loss, n = _fit_link(gaps, rows, fold)
        probs = link.predict(fitted, gaps[rows.test(fold)])
        results.append(FoldResult(
            GD_RATING, fold,
            {"prior_games": baselines.GD_PRIOR_GAMES, "carry": baselines.GD_CARRY},
            _link_json(fitted, loss, n), None, _predict(rows, fold, probs, gaps),
        ))
    return results


def backtest(data: Data, config: BacktestConfig) -> list[FoldResult]:
    """Run every model in `config` through the folds. Pure: stores nothing."""
    unknown = set(config.models) - set(MODELS)
    if unknown:
        raise ValueError(f"unknown models {sorted(unknown)}; known: {MODELS}")
    folds = make_folds({g.season for g in data.games}, config.burn_in, config.first_test)
    rows = _Rows(data.games)
    results: list[FoldResult] = []
    if ELO in config.models:
        results += _elo(data, rows, folds, config.grid, config.fixed)
    if BASE_RATE in config.models:
        results += _base_rate(rows, folds)
    if GD_RATING in config.models:
        results += _gd_rating(data, rows, folds)
    return results


def connect(path: Path = BACKTEST_DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def model_version(config: BacktestConfig) -> str:
    digest = hashlib.sha1(f"{BACKTEST_VERSION}|{config.to_json()}".encode()).hexdigest()
    return f"elo-1-{digest[:8]}"


def store(conn: sqlite3.Connection, data: Data, config: BacktestConfig, results: list[FoldResult]) -> int:
    """Append one run. Returns its run_id."""
    with conn:
        run_id = conn.execute(
            "INSERT INTO backtest_run (created_at, models, model_version, code_version, "
            "features_build, config_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(), ",".join(sorted({r.model for r in results})),
                model_version(config), BACKTEST_VERSION, data.features_build, config.to_json(),
            ),
        ).lastrowid
        for r in results:
            conn.execute(
                "INSERT INTO backtest_fold VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id, r.model, r.fold.test_season, ",".join(map(str, r.fold.tuning_seasons)),
                    *(None if value is None else json.dumps(value, sort_keys=True)
                      for value in (r.chosen_params, r.link_params, r.grid_edges)),
                ),
            )
            conn.executemany(
                "INSERT INTO backtest_prediction VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(run_id, r.model, p.season, p.game_id, r.fold.test_season, *p[2:]) for p in r.predictions],
            )
    return run_id
