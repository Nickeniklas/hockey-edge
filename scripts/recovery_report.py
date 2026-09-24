"""Before/after snapshot of hockey.db for the historical recovery (R4 acceptance).

Read-only against data/hockey.db. Two modes:

    python scripts/recovery_report.py --out data/recovery/before.json \
        --targets data/recovery/game_detail_targets.csv
    python scripts/recovery_report.py --diff data/recovery/before.json data/recovery/after_detail.json \
        --outcomes data/recovery/game_detail_outcomes.csv

`--out` records, per season x phase over ended games: row counts for every
table resync's no-shrink guard protects plus game_goal_events, zero-penalty
game counts, penalties per game (mean, median), and goalkeeper-event
coverage. With `--targets`, it also records each listed game's per-table
counts, which `--diff` needs to classify outcomes.

`--diff` prints the acceptance checks: any season where a guarded table
shrank, any change in game_goal_events, zero-penalty RUNKOSARJA rates against
the clean 2025/2026 reference, penalties per game, goalkeeper coverage, and
(with `--outcomes`, the CSV written by `resync.py --outcomes`) one outcome
class per targeted game.
"""

import argparse
import csv
import json
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from hockey_edge.ingest.liiga.resync import GUARDED_TABLES

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO / "data" / "hockey.db"

TABLES = [t for tables in GUARDED_TABLES.values() for t in tables] + ["game_goal_events"]
REFERENCE_SEASONS = ("2025", "2026")  # fetched in a clean run, see RECOVERY_BACKLOG.md

# The table whose growth counts as "recovered" for each endpoint's pass.
RECOVERY_TABLE = {"game_detail": "game_penalty_events", "game_stats": "game_puck_control"}


def connect_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)


def _per_game_counts(conn: sqlite3.Connection, table: str) -> dict[tuple[int, int], int]:
    return {
        (season, game_id): n
        for game_id, season, n in conn.execute(
            f"SELECT game_id, season, COUNT(*) FROM {table} GROUP BY game_id, season"
        )
    }


def build_report(conn: sqlite3.Connection, targets: list[tuple[int, int]] | None = None) -> dict:
    games = conn.execute(
        "SELECT season, game_id, phase FROM games WHERE ended = 1 ORDER BY season, phase"
    ).fetchall()
    counts = {t: _per_game_counts(conn, t) for t in TABLES}

    groups: dict[str, dict[str, dict]] = {}
    penalties: dict[tuple[str, str], list[int]] = {}
    for season, game_id, phase in games:
        key = (season, game_id)
        g = groups.setdefault(str(season), {}).setdefault(phase, {
            "games": 0, "rows": {t: 0 for t in TABLES},
            "zero_penalty_games": 0, "games_with_goalkeeper_events": 0,
        })
        g["games"] += 1
        for t in TABLES:
            g["rows"][t] += counts[t].get(key, 0)
        n_pen = counts["game_penalty_events"].get(key, 0)
        g["zero_penalty_games"] += n_pen == 0
        g["games_with_goalkeeper_events"] += counts["game_goalkeeper_events"].get(key, 0) > 0
        penalties.setdefault((str(season), phase), []).append(n_pen)

    for (season, phase), values in penalties.items():
        g = groups[season][phase]
        g["penalties_per_game_mean"] = round(statistics.fmean(values), 3)
        g["penalties_per_game_median"] = statistics.median(values)
        g["goalkeeper_event_coverage"] = round(g["games_with_goalkeeper_events"] / g["games"], 4)

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tables": TABLES,
        "by_season_phase": groups,
    }
    if targets is not None:
        report["targets"] = {
            f"{season}:{game_id}": {t: counts[t].get((season, game_id), 0) for t in TABLES}
            for season, game_id in targets
        }
    return report


def season_totals(report: dict) -> dict[str, dict[str, int]]:
    return {
        season: {t: sum(p["rows"][t] for p in phases.values()) for t in report["tables"]}
        for season, phases in report["by_season_phase"].items()
    }


def load_outcomes(path: Path) -> dict[tuple[str, str], str]:
    """(entity 'season:game_id', endpoint) -> last outcome that did something.
    A resumed run appends 'skipped_not_due' rows for games already handled,
    so those never override an earlier real outcome."""
    last: dict[tuple[str, str], str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (f"{row['season']}:{row['game_id']}", row["endpoint"])
            if row["outcome"] != "skipped_not_due" or key not in last:
                last[key] = row["outcome"]
    return last


def classify_targets(before: dict, after: dict, outcomes: dict[tuple[str, str], str]) -> dict[str, dict[str, dict[str, int]]]:
    """endpoint -> season -> class -> count. Classes: recovered (reparsed and
    the endpoint's recovery table grew), changed_no_gain (reparsed, it did
    not), refused (shrink guard), unchanged_hash, fetch_failed, and
    not_attempted (a target with no outcome row)."""
    result: dict[str, dict[str, dict[str, int]]] = {}
    endpoints = sorted({ep for _, ep in outcomes})
    for endpoint in endpoints:
        table = RECOVERY_TABLE.get(endpoint)
        for entity, before_counts in before.get("targets", {}).items():
            outcome = outcomes.get((entity, endpoint))
            if outcome is None:
                cls = "not_attempted"
            elif outcome == "reparsed":
                grew = table is not None and after.get("targets", {}).get(entity, {}).get(table, 0) > before_counts.get(table, 0)
                cls = "recovered" if grew else "changed_no_gain"
            else:
                cls = {"shrink_guarded": "refused", "unchanged": "unchanged_hash"}.get(outcome, outcome)
            season = entity.split(":")[0]
            bucket = result.setdefault(endpoint, {}).setdefault(season, {})
            bucket[cls] = bucket.get(cls, 0) + 1
    return result


def _rate(group: dict) -> float:
    return group["zero_penalty_games"] / group["games"] if group["games"] else 0.0


def print_diff(before: dict, after: dict, outcomes: dict | None) -> bool:
    """Prints the R4 checks. Returns False if a hard invariant failed
    (a guarded table shrank, or game_goal_events changed)."""
    ok = True
    tb, ta = season_totals(before), season_totals(after)
    guarded = [t for t in before["tables"] if t != "game_goal_events"]

    print("== 1. Outcome per targeted game ==")
    if outcomes is None:
        print("  (no --outcomes given)")
    else:
        for endpoint, seasons in classify_targets(before, after, outcomes).items():
            print(f"  {endpoint}:")
            for season, classes in sorted(seasons.items()):
                print(f"    {season}: " + ", ".join(f"{c}={n}" for c, n in sorted(classes.items())))

    print("\n== 2. Guarded table totals per season (must never shrink) ==")
    for season in sorted(set(tb) | set(ta)):
        for t in guarded:
            b, a = tb.get(season, {}).get(t, 0), ta.get(season, {}).get(t, 0)
            if a < b:
                ok = False
                print(f"  SHRANK {season} {t}: {b} -> {a}")
            elif a != b:
                print(f"  {season} {t}: {b} -> {a} (+{a - b})")
    print("  (no shrink)" if ok else "")

    print("\n== 3. game_goal_events per season (must be identical) ==")
    goals_ok = True
    for season in sorted(set(tb) | set(ta)):
        b, a = tb.get(season, {}).get("game_goal_events", 0), ta.get(season, {}).get("game_goal_events", 0)
        if a != b:
            goals_ok = False
            print(f"  CHANGED {season}: {b} -> {a}")
    print("  (identical)" if goals_ok else "")
    ok = ok and goals_ok

    print("\n== 4. Zero-penalty RUNKOSARJA rate (reference: 2025/2026) ==")
    ref = [_rate(after["by_season_phase"][s]["RUNKOSARJA"]) for s in REFERENCE_SEASONS
           if "RUNKOSARJA" in after["by_season_phase"].get(s, {})]
    lo, hi = (min(ref), max(ref)) if ref else (0.0, 0.0)
    print(f"  reference range {lo:.3%} - {hi:.3%}")
    for season in sorted(after["by_season_phase"]):
        if season in REFERENCE_SEASONS or "RUNKOSARJA" not in after["by_season_phase"][season]:
            continue
        g_a = after["by_season_phase"][season]["RUNKOSARJA"]
        g_b = before["by_season_phase"].get(season, {}).get("RUNKOSARJA", g_a)
        flag = "  ABOVE RANGE" if _rate(g_a) > hi else ""
        print(f"  {season}: {g_b['zero_penalty_games']}/{g_b['games']} -> "
              f"{g_a['zero_penalty_games']}/{g_a['games']} ({_rate(g_a):.3%}){flag}")

    print("\n== 5/6. Penalties per game (mean / median) and goalkeeper-event coverage, after ==")
    for season, phases in sorted(after["by_season_phase"].items()):
        for phase, g in sorted(phases.items()):
            gb = before["by_season_phase"].get(season, {}).get(phase, g)
            print(f"  {season} {phase:<15} n={g['games']:>4}  pen/game {gb['penalties_per_game_mean']:.2f} -> "
                  f"{g['penalties_per_game_mean']:.2f} (median {g['penalties_per_game_median']})  "
                  f"gk coverage {gb['goalkeeper_event_coverage']:.1%} -> {g['goalkeeper_event_coverage']:.1%}")

    print("\nRESULT:", "invariants hold" if ok else "INVARIANT FAILED")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out", type=Path, help="write a report JSON")
    mode.add_argument("--diff", nargs=2, type=Path, metavar=("BEFORE", "AFTER"), help="compare two report JSONs")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--targets", type=Path, help="with --out: record per-game counts for these season,game_id rows")
    parser.add_argument("--outcomes", type=Path, help="with --diff: the resync --outcomes CSV")
    args = parser.parse_args()

    if args.out:
        targets = None
        if args.targets:
            with open(args.targets, newline="", encoding="utf-8") as f:
                targets = [(int(r["season"]), int(r["game_id"])) for r in csv.DictReader(f)]
        conn = connect_ro(args.db)
        try:
            report = build_report(conn, targets)
        finally:
            conn.close()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        before, after = (json.loads(p.read_text(encoding="utf-8")) for p in args.diff)
        outcomes = load_outcomes(args.outcomes) if args.outcomes else None
        sys.exit(0 if print_diff(before, after, outcomes) else 1)


if __name__ == "__main__":
    main()
