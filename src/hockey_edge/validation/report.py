"""Tables for one backtest run (docs/ELO_HARNESS_PLAN.md, design answer 8).

    python -m hockey_edge.validation.report --run latest

Prints the tables and writes them to docs/ELO_RESULTS.md below the marker
line; whatever is above the marker (the written summary) is kept. Metrics
are computed from the stored predictions; nothing is stored back.

Only regular-season games are reported. Playoff predictions are stored but
not evaluated (open decision in the plan). Every comparison is paired, on
the same games, and carries its standard error.
"""

import argparse
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

import numpy as np

from hockey_edge.features.db import FEATURES_DB_PATH
from hockey_edge.models import CLASSES, versions
from hockey_edge.validation import harness, market, metrics

RESULTS_PATH = Path(__file__).resolve().parents[3] / "docs" / "ELO_RESULTS.md"
MARKER = "<!-- Generated below by python -m hockey_edge.validation.report. Edits below this line are overwritten. -->"

# A game is early-season while either team has played fewer than this many
# regular-season games that season.
EARLY_GAMES = 10
LABELS = {
    harness.ELO: "Elo", harness.BASE_RATE: "Base rates", harness.GD_RATING: "Goal-diff rating",
    harness.ELO_FLAT_ENTRY: "Elo, flat entry",
}


class _Run:
    """Stored predictions of one run, joined to the games they are for."""

    def __init__(self, backtest: sqlite3.Connection, features: sqlite3.Connection, run_id: int) -> None:
        self.run = backtest.execute("SELECT * FROM backtest_run WHERE run_id = ?", (run_id,)).fetchone()
        if self.run is None:
            raise ValueError(f"no backtest run {run_id}")
        self.folds = backtest.execute(
            "SELECT * FROM backtest_fold WHERE run_id = ? ORDER BY model, test_season", (run_id,)
        ).fetchall()
        spine = {
            (g["season"], g["game_id"]): g for g in features.execute(
                "SELECT season, game_id, start_date, home_team_id, away_team_id FROM game_spine "
                "WHERE in_eval_sample = 1 AND ended = 1"
            )
        }
        self.first_season = {
            (r[0], r[1]): r[2] for r in features.execute(
                "SELECT season, team_id, team_name FROM team_season WHERE is_first_liiga_season = 1"
            )
        }
        self.complete = {
            r[0] for r in features.execute(
                "SELECT season FROM season_format WHERE runkosarja_games = runkosarja_ended"
            )
        }
        stored: dict[str, dict] = {}
        for p in backtest.execute(
            "SELECT model, season, game_id, p_home, p_draw, p_away, result FROM backtest_prediction "
            "WHERE run_id = ?", (run_id,)
        ):
            if (p["season"], p["game_id"]) in spine:
                stored.setdefault(p["model"], {})[p["season"], p["game_id"]] = p
        self.models = [m for m in LABELS if m in stored]
        keys = {m: set(rows) for m, rows in stored.items()}
        if len({frozenset(k) for k in keys.values()}) != 1:
            raise ValueError("the models of this run did not predict the same games")
        self.games = sorted(
            (spine[k] for k in keys[self.models[0]]), key=lambda g: (g["start_date"], g["season"], g["game_id"])
        )
        self.results = [stored[self.models[0]][g["season"], g["game_id"]]["result"] for g in self.games]
        self.probs = {
            m: np.array([tuple(stored[m][g["season"], g["game_id"]])[3:6] for g in self.games])
            for m in self.models
        }
        self.loss = {m: metrics.game_log_loss(self.probs[m], self.results) for m in self.models}
        self.season = np.array([g["season"] for g in self.games])
        self.seasons = sorted(set(self.season.tolist()))
        self.pooled = np.isin(self.season, sorted(self.complete))
        self.early = self._early()

    def _early(self) -> np.ndarray:
        played: dict[tuple[int, str], int] = {}
        flags = []
        pending: list = []
        last_date = None
        for g in self.games:
            if g["start_date"] != last_date:
                # Games of one date count only from the next date on.
                for done in pending:
                    for team in (done["home_team_id"], done["away_team_id"]):
                        played[done["season"], team] = played.get((done["season"], team), 0) + 1
                pending, last_date = [], g["start_date"]
            flags.append(min(
                played.get((g["season"], g["home_team_id"]), 0), played.get((g["season"], g["away_team_id"]), 0)
            ) < EARLY_GAMES)
            pending.append(g)
        return np.array(flags, dtype=bool)

    def involves(self, season: int, team_id: str) -> np.ndarray:
        return np.array([
            g["season"] == season and team_id in (g["home_team_id"], g["away_team_id"]) for g in self.games
        ], dtype=bool)

    def pooled_label(self) -> str:
        seasons = [s for s in self.seasons if s in self.complete]
        return f"{seasons[0]}–{seasons[-1]}" if len(seasons) > 1 else str(seasons[0])


def _paired(run: _Run, a: str, b: str, mask: np.ndarray) -> str:
    if a not in run.loss or b not in run.loss or mask.sum() < 2:
        return "n/a"
    diff = metrics.paired_difference(run.loss[a][mask], run.loss[b][mask])
    return f"{diff.mean:+.4f} ± {diff.se:.4f}"


def _mean(run: _Run, model: str, mask: np.ndarray) -> str:
    return f"{run.loss[model][mask].mean():.4f}" if model in run.loss and mask.any() else "n/a"


def _table(header: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return lines + [""]


def _loss_row(run: _Run, label: str, mask: np.ndarray) -> list:
    return [
        label, int(mask.sum()), _mean(run, harness.ELO, mask), _mean(run, harness.BASE_RATE, mask),
        _mean(run, harness.GD_RATING, mask), _paired(run, harness.ELO, harness.BASE_RATE, mask),
        _paired(run, harness.ELO, harness.GD_RATING, mask),
    ]


LOSS_HEADER = ["", "Games", "Elo", "Base rates", "Goal-diff", "Elo − base (± SE)", "Elo − goal-diff (± SE)"]


def _log_loss_section(run: _Run) -> list[str]:
    rows = [_loss_row(run, str(s), run.season == s) for s in run.seasons if s in run.complete]
    rows.append(_loss_row(run, f"**Pooled {run.pooled_label()}**", run.pooled))
    rows += [_loss_row(run, f"{s} (in progress)", run.season == s) for s in run.seasons if s not in run.complete]
    return ["## Log loss by test season", "", "Lower is better. A negative difference favours Elo.", ""] + _table(
        LOSS_HEADER, rows
    )


def _secondary_section(run: _Run) -> list[str]:
    lines = ["## Other metrics", "", "Accuracy is reported, never optimised. ECE is the mean over the three classes.", ""]
    scopes = [(f"Pooled {run.pooled_label()}", run.pooled)]
    scopes += [(f"{s} (in progress)", run.season == s) for s in run.seasons if s not in run.complete]
    rows = []
    for label, mask in scopes:
        results = [r for r, keep in zip(run.results, mask) if keep]
        for model in run.models:
            probs = run.probs[model][mask]
            tables = metrics.calibration(probs, results)
            ece = np.mean([metrics.expected_calibration_error(tables[c]) for c in CLASSES])
            rows.append([
                label, LABELS[model], int(mask.sum()), f"{metrics.log_loss(probs, results):.4f}",
                f"{metrics.brier(probs, results):.4f}", f"{metrics.rps(probs, results):.4f}",
                f"{metrics.accuracy(probs, results):.3f}", f"{ece:.4f}",
            ])
    return lines + _table(["Games from", "Model", "Games", "Log loss", "Brier", "RPS", "Accuracy", "ECE"], rows)


def _folds_section(run: _Run) -> list[str]:
    lines = ["## What each fold chose", "", "Fitted on the tuning seasons only. An edge is a chosen value at the end of its grid.", ""]
    rows = []
    for f in run.folds:
        if f["model"] != harness.ELO:
            continue
        p, fitted, edges = (json.loads(f[c]) for c in ("chosen_params_json", "link_params_json", "grid_edges_json"))
        tuning = f["tuning_seasons"].split(",")
        rows.append([
            f["test_season"], f"{tuning[0]}–{tuning[-1]}" if len(tuning) > 1 else tuning[0], p["k"], p["hfa"],
            p["r"], "on" if p["margin"] else "off", p["s_ot"], f"{fitted['c1']:.3f}", f"{fitted['c2']:.3f}",
            f"{fitted['beta']:.3f}", f"{fitted['tuning_log_loss']:.4f}",
            ", ".join(f"{k} {v}" for k, v in edges.items()) or "none",
        ])
    lines += _table(
        ["Test season", "Tuned on", "K", "HFA", "r", "Margin", "s_ot", "c1", "c2", "β", "Tuning log loss", "Edges"],
        rows,
    )
    config = json.loads(run.run["config_json"])
    fixed = {k: v for k, v in config.get("fixed", {}).items() if k not in config["grid"]}
    lines += ["Grid: " + "; ".join(f"{k} {v}" for k, v in config["grid"].items()) + "."
              + (" Not tuned: " + ", ".join(f"{k} {v}" for k, v in fixed.items()) + "." if fixed else ""), ""]

    rows = []
    by_fold = {(f["model"], f["test_season"]): f for f in run.folds}
    for season in sorted({f["test_season"] for f in run.folds}):
        base, goal = by_fold.get((harness.BASE_RATE, season)), by_fold.get((harness.GD_RATING, season))
        row = [season]
        if base:
            rates = json.loads(base["chosen_params_json"])
            row.append(" / ".join(f"{rates[k]:.3f}" for k in ("p_home", "p_draw", "p_away")))
        else:
            row.append("n/a")
        if goal:
            fitted = json.loads(goal["link_params_json"])
            row += [f"{fitted['c1']:.3f}", f"{fitted['c2']:.3f}", f"{fitted['beta']:.3f}", f"{fitted['tuning_log_loss']:.4f}"]
        else:
            row += ["n/a"] * 4
        rows.append(row)
    return lines + ["Baselines, same folds:", ""] + _table(
        ["Test season", "Base rates H / D / A", "Goal-diff c1", "c2", "β (per goal/game)", "Tuning log loss"], rows
    )


def _calibration_section(run: _Run) -> list[str]:
    if harness.ELO not in run.probs:
        return []
    lines = [f"## Calibration, Elo, pooled {run.pooled_label()}", "",
             f"Bins of width {metrics.BIN_WIDTH}, merged until each holds {metrics.MIN_BIN_GAMES} games.", ""]
    results = [r for r, keep in zip(run.results, run.pooled) if keep]
    tables = metrics.calibration(run.probs[harness.ELO][run.pooled], results)
    names = {"H": "Home win", "D": "Draw", "A": "Away win"}
    for c in CLASSES:
        lines += [f"**{names[c]}** (ECE {metrics.expected_calibration_error(tables[c]):.4f})", ""]
        lines += _table(
            ["Predicted", "Games", "Mean predicted", "Observed"],
            [[f"{b.low:.2f}–{b.high:.2f}", b.n, f"{b.mean_predicted:.3f}", f"{b.observed:.3f}"] for b in tables[c]],
        )
    lines += ["Mean predicted against observed rate, per season:", ""]
    rows = []
    for season in run.seasons:
        mask = run.season == season
        large = metrics.calibration_in_the_large(
            run.probs[harness.ELO][mask], [r for r, keep in zip(run.results, mask) if keep]
        )
        rows.append([season, int(mask.sum())] + [f"{large[c][0]:.3f} / {large[c][1]:.3f}" for c in CLASSES])
    return lines + _table(["Season", "Games", "Home pred / obs", "Draw pred / obs", "Away pred / obs"], rows)


def _slices_section(run: _Run) -> list[str]:
    first = np.zeros(len(run.games), dtype=bool)
    for season, team_id in run.first_season:
        first |= run.involves(season, team_id)
    rows = [
        _loss_row(run, f"Early season, pooled (a team under {EARLY_GAMES} games)", run.pooled & run.early),
        _loss_row(run, "Rest of season, pooled", run.pooled & ~run.early),
        _loss_row(run, "Games with a first-Liiga-season team, all test seasons", first),
    ]
    rows += [_loss_row(run, f"{s} (in progress)", run.season == s) for s in run.seasons if s not in run.complete]
    lines = ["## Slices", ""] + _table(LOSS_HEADER, rows)

    if harness.ELO_FLAT_ENTRY in run.loss:
        lines += [
            "First-season teams, and the same games with a flat entry (enter at the returning-team mean, "
            "normal K). A negative difference favours the cold-start rule.", "",
        ]
        rows = []
        for (season, team_id), name in sorted(run.first_season.items()):
            for label, mask in ((f"{name} {season}, its games", run.involves(season, team_id)),
                                (f"{season}, all games", run.season == season)):
                if mask.any():
                    rows.append([
                        label, int(mask.sum()), _mean(run, harness.ELO, mask),
                        _mean(run, harness.ELO_FLAT_ENTRY, mask), _mean(run, harness.BASE_RATE, mask),
                        _paired(run, harness.ELO, harness.ELO_FLAT_ENTRY, mask),
                        _paired(run, harness.ELO, harness.BASE_RATE, mask),
                    ])
        lines += _table(
            ["", "Games", "Elo", "Flat entry", "Base rates", "Elo − flat entry (± SE)", "Elo − base (± SE)"], rows
        )
    return lines


def _version_section(run: _Run) -> list[str]:
    """The last fold is the config a live prediction would be logged under."""
    last = [f for f in run.folds if f["model"] == harness.ELO]
    if not last:
        return []
    last = last[-1]
    params, fitted = json.loads(last["chosen_params_json"]), json.loads(last["link_params_json"])
    tagged = (
        params == asdict(versions.ELO_1_PARAMS)
        and all(fitted[k] == v for k, v in asdict(versions.ELO_1_LINK).items())
    )
    tuning = last["tuning_seasons"].split(",")
    return [
        f"## Tagged version: `{versions.ELO_1_VERSION}`", "",
        f"The {last['test_season']} fold: tuned on {tuning[0]}–{tuning[-1]}, {fitted['tuning_games']:,} games.", "",
        "- Elo: " + ", ".join(f"{k} {v}" for k, v in params.items() if v is not None),
        f"- Link: c1 {fitted['c1']!r}, c2 {fitted['c2']!r}, beta {fitted['beta']!r}",
        f"- Tuning log loss {fitted['tuning_log_loss']:.4f}",
        "- " + ("Matches" if tagged else "**DOES NOT match**")
        + f" `ELO_1_PARAMS` and `ELO_1_LINK` in `src/hockey_edge/models/versions.py`.", "",
    ]


def render(backtest: sqlite3.Connection, features: sqlite3.Connection, run_id: int) -> str:
    """The generated part of the results document, as Markdown."""
    run = _Run(backtest, features, run_id)
    build = json.loads(run.run["features_build"])
    lines = [
        f"Run {run_id}, {run.run['created_at'][:19]} UTC. Model version `{run.run['model_version']}`, "
        f"harness `{run.run['code_version']}`, features `{build['code_version']}` "
        f"(hockey.db as of {str(build['hockey_max_fetched_at'])[:10]}). "
        f"{len(run.games)} regular-season games in {len(run.seasons)} test seasons.",
        "",
    ]
    for section in (_log_loss_section, _secondary_section, _folds_section, _calibration_section, _slices_section):
        lines += section(run)
    lines += market.render(backtest, features, run_id, LABELS)
    lines += _version_section(run)
    return "\n".join(lines).rstrip() + "\n"


def latest_run(backtest: sqlite3.Connection) -> int:
    run_id = backtest.execute("SELECT MAX(run_id) FROM backtest_run").fetchone()[0]
    if run_id is None:
        raise ValueError("backtest.db has no runs")
    return run_id


def write(path: Path, generated: str) -> None:
    """Replace everything below the marker; keep what is above it."""
    head = "# Elo baseline results\n\n"
    if path.exists():
        head = path.read_text(encoding="utf-8").split(MARKER)[0]
    path.write_text(f"{head.rstrip()}\n\n{MARKER}\n\n{generated}", encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--run", default="latest", help="run_id, or 'latest'")
    parser.add_argument("--backtest", type=Path, default=harness.BACKTEST_DB_PATH)
    parser.add_argument("--features", type=Path, default=FEATURES_DB_PATH)
    parser.add_argument("--out", type=Path, default=RESULTS_PATH)
    parser.add_argument("--no-write", action="store_true", help="print only")
    args = parser.parse_args()

    backtest = sqlite3.connect(Path(args.backtest).resolve().as_uri() + "?mode=ro", uri=True)
    backtest.row_factory = sqlite3.Row
    features = sqlite3.connect(Path(args.features).resolve().as_uri() + "?mode=ro", uri=True)
    features.row_factory = sqlite3.Row
    try:
        run_id = latest_run(backtest) if args.run == "latest" else int(args.run)
        generated = render(backtest, features, run_id)
    finally:
        backtest.close()
        features.close()
    print(generated)
    if not args.no_write:
        write(args.out, generated)
        print(f"written to {args.out}")


if __name__ == "__main__":
    main()
