"""Run the walk-forward backtest and append it to data/backtest.db.

    python -m hockey_edge.validation.run --models elo,base_rate,gd_rating

Reads data/features.db (rebuild it first: python -m hockey_edge.features.build).
Print the tables with python -m hockey_edge.validation.report --run latest.
"""

import argparse
import sqlite3
import time
from pathlib import Path

from hockey_edge.features.db import FEATURES_DB_PATH
from hockey_edge.validation import harness


def run(features_path: Path, backtest_path: Path, config: harness.BacktestConfig) -> tuple[int, float]:
    """(run_id, seconds)."""
    started = time.perf_counter()
    features = sqlite3.connect(Path(features_path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        data = harness.load(features)
    finally:
        features.close()
    results = harness.backtest(data, config)
    out = harness.connect(backtest_path)
    try:
        run_id = harness.store(out, data, config, results)
    finally:
        out.close()
    return run_id, time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--models", default=",".join(harness.MODELS))
    parser.add_argument("--features", type=Path, default=FEATURES_DB_PATH)
    parser.add_argument("--out", type=Path, default=harness.BACKTEST_DB_PATH)
    args = parser.parse_args()
    config = harness.BacktestConfig(models=tuple(args.models.split(",")))
    run_id, seconds = run(args.features, args.out, config)
    print(f"run {run_id} ({harness.model_version(config)}) stored in {args.out} in {seconds:.0f} s")


if __name__ == "__main__":
    main()
