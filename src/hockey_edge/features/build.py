"""Full rebuild of data/features.db.

    python -m hockey_edge.features.build

Builds into a temporary file next to the target and swaps it in at the end,
so a failed build leaves the previous features.db in place.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from hockey_edge.features import FEATURE_VERSION, benchmark, db, home, schedule, season_format, spine

# spine first: everything else reads game_spine.
STEPS = (spine, season_format, home, schedule, benchmark)


def build(
    features_path: Path = db.FEATURES_DB_PATH,
    hockey_path: Path = db.HOCKEY_DB_PATH,
    snapshots_path: Path = db.SNAPSHOTS_DB_PATH,
) -> dict[str, int]:
    """Rebuild every table. Returns row counts per table."""
    features_path = Path(features_path)
    features_path.parent.mkdir(parents=True, exist_ok=True)
    building = features_path.with_name(features_path.name + ".building")
    building.unlink(missing_ok=True)

    conn = db.connect(building, hockey_path, snapshots_path)
    try:
        conn.executescript(db.SCHEMA)
        for step in STEPS:
            step.build(conn)
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in db.TABLES}
        conn.execute(
            "INSERT INTO build_meta VALUES (?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                FEATURE_VERSION,
                conn.execute("SELECT MAX(fetched_at) FROM hockey.sync_state").fetchone()[0],
                conn.execute(
                    "SELECT MAX(captured_at) FROM ("
                    "SELECT captured_at FROM snapshots.odds_snapshots "
                    "UNION ALL SELECT captured_at FROM snapshots.lineup_snapshots)"
                ).fetchone()[0],
                json.dumps(counts),
            ),
        )
        conn.commit()
    except BaseException:
        conn.close()
        building.unlink(missing_ok=True)
        raise
    conn.close()
    os.replace(building, features_path)
    return counts


def main() -> None:
    counts = build()
    print(f"built {db.FEATURES_DB_PATH}")
    for table, n in counts.items():
        print(f"  {table}: {n}")


if __name__ == "__main__":
    main()
