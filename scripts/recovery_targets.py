"""Write the target lists for the historical recovery (docs/RECOVERY_BACKLOG.md).

Read-only against data/hockey.db. Writes two `season,game_id` CSVs for
`resync.py --targets`:

- game_detail_targets.csv: seasons 2015-2024, ended, zero game_penalty_events
  (any phase) -- the 2026-07-19/21 bad-fetch window's damage.
- game_stats_targets.csv: seasons 2015-2024, ended, fewer than 3
  game_puck_control rows.

Usage (from repo root, venv active):

    python scripts/recovery_targets.py
    python scripts/recovery_targets.py --out-dir data/recovery

2025/2026 are deliberately out of scope: fetched 2026-08-22 in a clean run,
their zero-penalty games are more likely genuine (RECOVERY_BACKLOG section 1).
"""

import argparse
import csv
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO / "data" / "hockey.db"
DEFAULT_OUT_DIR = REPO / "data" / "recovery"
FIRST_SEASON, LAST_SEASON = 2015, 2024

GAME_DETAIL_SQL = """
SELECT g.season, g.game_id FROM games g
WHERE g.season BETWEEN ? AND ? AND g.ended = 1
  AND NOT EXISTS (SELECT 1 FROM game_penalty_events p WHERE p.game_id = g.game_id AND p.season = g.season)
ORDER BY g.season, g.start_utc, g.game_id
"""

GAME_STATS_SQL = """
SELECT g.season, g.game_id FROM games g
WHERE g.season BETWEEN ? AND ? AND g.ended = 1
  AND (SELECT COUNT(*) FROM game_puck_control c WHERE c.game_id = g.game_id AND c.season = g.season) < 3
ORDER BY g.season, g.start_utc, g.game_id
"""


def connect_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)


def select_targets(conn: sqlite3.Connection) -> dict[str, list[tuple[int, int]]]:
    """(season, game_id) lists keyed by output file name."""
    bounds = (FIRST_SEASON, LAST_SEASON)
    return {
        "game_detail_targets.csv": conn.execute(GAME_DETAIL_SQL, bounds).fetchall(),
        "game_stats_targets.csv": conn.execute(GAME_STATS_SQL, bounds).fetchall(),
    }


def write_targets(path: Path, rows: list[tuple[int, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["season", "game_id"])
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    conn = connect_ro(args.db)
    try:
        selections = select_targets(conn)
    finally:
        conn.close()

    for name, rows in selections.items():
        write_targets(args.out_dir / name, rows)
        per_season: dict[int, int] = {}
        for season, _ in rows:
            per_season[season] = per_season.get(season, 0) + 1
        print(f"{name}: {len(rows)} games -> {args.out_dir / name}")
        print("  " + ", ".join(f"{s}: {n}" for s, n in sorted(per_season.items())))


if __name__ == "__main__":
    main()
