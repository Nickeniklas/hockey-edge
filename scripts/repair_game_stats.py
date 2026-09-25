"""Repair the 2026-09-25 game_stats recovery pass (docs/RECOVERY_BACKLOG.md).

That pass refetched game_stats for 5,512 games from 2015 to 2024. liiga.fi now
serves those games with the per-period stats stripped: player time on ice
and corsi are 0, faceoffs are null, power-play lists are empty, and some
goals are missing. The row counts did not change, so the no-shrink guard
let 3,953 reparses through and the stripped values replaced good ones.
The `puckStats` section, by contrast, gained periods.

This script, with no HTTP:

1. Restores game_team_period_stats, game_player_period_stats,
   game_goalie_period_stats and game_puck_control for every game the run
   reparsed, exactly as they were in the pre-run backup (ids included).
2. Adds game_puck_control periods from each targeted game's latest saved raw
   response, but only rows that carry a value (placeholder rows, all null,
   are what 2015-2022 return) and only when every stored row for that game
   appears unchanged in the new response. It never deletes or rewrites an
   existing puck-control row.
3. Verifies that the three period-stats tables equal the backup exactly and
   that game_puck_control is the backup plus the added rows.

    python scripts/repair_game_stats.py --backup data/backups/hockey_pre_gamestats.db \
        --outcomes data/recovery/game_stats_outcomes.csv            # dry run, no writes
    python scripts/repair_game_stats.py ... --apply
"""

import argparse
import csv
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from hockey_edge.ingest.liiga import parsers
from hockey_edge.ingest.liiga.resync import GUARDED_TABLES, load_latest_raw

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO / "data" / "hockey.db"

TABLES = GUARDED_TABLES["game_stats"]
PERIOD_TABLES = [t for t in TABLES if t != "game_puck_control"]
PUCK_COLS = ("game_id", "season", "period", "home_control_seconds",
             "away_control_seconds", "contested_control_seconds")


def open_db(path: Path) -> sqlite3.Connection:
    """Autocommit mode (repair() manages its own transaction) with URI
    filenames on, which the read-only ATTACH of the backup needs."""
    conn = sqlite3.connect(f"file:{path.as_posix()}", uri=True, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def last_outcomes(path: Path) -> dict[tuple[int, int], str]:
    """(season, game_id) -> last game_stats outcome that did something."""
    last: dict[tuple[int, int], str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["endpoint"] != "game_stats":
                continue
            key = (int(row["season"]), int(row["game_id"]))
            if row["outcome"] != "skipped_not_due" or key not in last:
                last[key] = row["outcome"]
    return last


def _puck_rows(conn: sqlite3.Connection, schema: str, season: int, game_id: int) -> set[tuple]:
    return set(conn.execute(
        f"SELECT {', '.join(PUCK_COLS)} FROM {schema}.game_puck_control WHERE season = ? AND game_id = ?",
        (season, game_id),
    ))


def plan_puck_additions(stored: set[tuple], parsed: list[tuple]) -> tuple[str, list[tuple]]:
    """('added' | 'nothing_to_add' | 'conflict', rows to insert). A conflict is
    a stored row the new response does not contain unchanged."""
    parsed_set = {tuple(r) for r in parsed}
    if not stored <= parsed_set:
        return "conflict", []
    have = {r[2] for r in stored}
    add = sorted(r for r in parsed_set if r[2] not in have and any(v is not None for v in r[3:]))
    return ("added" if add else "nothing_to_add"), add


def repair(conn: sqlite3.Connection, backup: Path, outcomes: dict[tuple[int, int], str], apply: bool) -> dict:
    """Runs in one transaction; rolls back unless apply is True and the
    verification passes. Returns the summary."""
    conn.execute("ATTACH DATABASE ? AS bak", (f"file:{backup.as_posix()}?mode=ro",))
    try:
        reparsed = [k for k, o in outcomes.items() if o == "reparsed"]
        conn.execute("BEGIN")
        # Delete every reparsed game's rows first, then copy the backup's back:
        # a game-by-game swap could collide with ids the run handed to another game.
        for season, game_id in reparsed:
            for t in TABLES:
                conn.execute(f"DELETE FROM main.{t} WHERE season = ? AND game_id = ?", (season, game_id))
        for season, game_id in reparsed:
            for t in TABLES:
                conn.execute(
                    f"INSERT INTO main.{t} SELECT * FROM bak.{t} WHERE season = ? AND game_id = ?", (season, game_id)
                )

        per_game: dict[tuple[int, int], tuple[str, int]] = {}
        added_rows: set[tuple] = set()
        for season, game_id in sorted(outcomes):
            try:
                data, _ = load_latest_raw(conn, "game_stats", season, game_id)
            except LookupError:
                per_game[(season, game_id)] = ("no_raw", 0)
                continue
            parsed = parsers.parse_game_stats(data, game_id, season)["puck"]
            status, add = plan_puck_additions(_puck_rows(conn, "main", season, game_id), parsed)
            conn.executemany(
                f"INSERT INTO main.game_puck_control ({', '.join(PUCK_COLS)}) VALUES (?,?,?,?,?,?)", add
            )
            added_rows.update(add)
            per_game[(season, game_id)] = (status, len(add))

        problems = verify(conn, added_rows)
        conn.execute("COMMIT" if apply and not problems else "ROLLBACK")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("DETACH DATABASE bak")

    by_season: dict[int, Counter] = {}
    for (season, _), (status, n) in per_game.items():
        c = by_season.setdefault(season, Counter())
        c[status] += 1
        c["rows_added"] += n
    return {"restored_games": len(reparsed), "by_season": by_season, "problems": problems,
            "per_game": per_game, "applied": apply and not problems}


def verify(conn: sqlite3.Connection, added_rows: set[tuple]) -> list[str]:
    problems = []
    for t in PERIOD_TABLES:
        for a, b in (("main", "bak"), ("bak", "main")):
            n = conn.execute(f"SELECT COUNT(*) FROM (SELECT * FROM {a}.{t} EXCEPT SELECT * FROM {b}.{t})").fetchone()[0]
            if n:
                problems.append(f"{t}: {n} rows in {a} not in {b}")
    cols = ", ".join(PUCK_COLS)
    missing = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT {cols} FROM bak.game_puck_control EXCEPT SELECT {cols} FROM main.game_puck_control)"
    ).fetchone()[0]
    if missing:
        problems.append(f"game_puck_control: {missing} backup rows missing")
    extra = set(conn.execute(
        f"SELECT {cols} FROM main.game_puck_control EXCEPT SELECT {cols} FROM bak.game_puck_control"
    ))
    if extra != added_rows:
        problems.append(f"game_puck_control: {len(extra ^ added_rows)} rows differ from backup + additions")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--backup", type=Path, required=True, help="the pre-run hockey.db backup")
    parser.add_argument("--outcomes", type=Path, required=True, help="the run's resync --outcomes CSV")
    parser.add_argument("--apply", action="store_true", help="commit (default: dry run, rolled back)")
    parser.add_argument("--report", type=Path, help="write one season,game_id,puck_status,rows_added row per game")
    args = parser.parse_args()

    conn = open_db(args.db)
    try:
        summary = repair(conn, args.backup, last_outcomes(args.outcomes), args.apply)
    finally:
        conn.close()

    print(f"restored from backup: {summary['restored_games']} reparsed games")
    for season, c in sorted(summary["by_season"].items()):
        print(f"  {season}: " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())))
    for p in summary["problems"]:
        print("VERIFY FAILED:", p)
    if args.report:
        with open(args.report, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["season", "game_id", "puck_status", "rows_added"])
            for (season, game_id), (status, n) in sorted(summary["per_game"].items()):
                w.writerow([season, game_id, status, n])
    print("APPLIED" if summary["applied"] else "rolled back (dry run)" if not summary["problems"] else "rolled back")
    sys.exit(1 if summary["problems"] else 0)


if __name__ == "__main__":
    main()
