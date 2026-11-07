"""F2a reports on a built features.db: starter skew and fallback use.

    python -m hockey_edge.features.goalie_report

Read-only. The skew report compares, for games with both a pre-game snapshot
starter and a clean actual starter, the two ids and the starter_sv each
would give. The source report counts how each team-game's starter was
resolved per season; 'unknown' is split into unplayed games (no snapshot
yet) and ended games whose actual starter could not be derived.
"""

import sqlite3
from collections import Counter, defaultdict

from hockey_edge.features import db, goalie


def skew_report(conn: sqlite3.Connection, season: int = 2027) -> dict:
    rows = conn.execute(
        "SELECT st.*, f.starter_sv FROM game_starters st "
        "JOIN features_goalie f USING (game_id, season, team_role) "
        "WHERE st.season = ? AND st.snapshot_player_id IS NOT NULL "
        "AND st.actual_player_id IS NOT NULL ORDER BY st.game_id, st.team_role",
        (season,),
    ).fetchall()
    history = goalie.GoalieHistory(conn)
    mismatches = []
    for r in rows:
        if r["snapshot_player_id"] == r["actual_player_id"]:
            continue
        g = conn.execute(
            "SELECT * FROM game_spine WHERE season = ? AND game_id = ?", (r["season"], r["game_id"])
        ).fetchone()
        actual_sv = goalie.starter_sv_at(history, g, r["actual_player_id"])
        mismatches.append({
            "game_id": r["game_id"], "team_role": r["team_role"],
            "inferred": r["snapshot_player_id"], "actual": r["actual_player_id"],
            "sv_inferred": r["starter_sv"], "sv_actual": actual_sv,
            "diff": r["starter_sv"] - actual_sv,
        })
    counts = conn.execute(
        "SELECT COUNT(*) AS n, SUM(snapshot_player_id IS NOT NULL) AS with_snapshot, "
        "SUM(snapshot_player_id IS NOT NULL AND actual_player_id IS NULL AND actual_reason <> 'not_ended') "
        "AS snapshot_no_actual "
        "FROM game_starters WHERE season = ?",
        (season,),
    ).fetchone()
    return {
        "season": season,
        "team_games": counts["n"],
        "with_snapshot": counts["with_snapshot"],
        "snapshot_but_actual_undetermined": counts["snapshot_no_actual"],
        "both": len(rows),
        "mismatches": mismatches,
        "mean_abs_sv_diff": (sum(abs(m["diff"]) for m in mismatches) / len(rows)) if rows else None,
    }


def source_counts(conn: sqlite3.Connection) -> dict[int, Counter]:
    """Per season: resolved source counts, 'unknown' split by why."""
    out: dict[int, Counter] = defaultdict(Counter)
    for r in conn.execute(
        "SELECT st.season, st.resolved_source, st.actual_reason, s.ended "
        "FROM game_starters st JOIN game_spine s USING (game_id, season)"
    ):
        key = r["resolved_source"]
        if key == "unknown":
            key = "unknown_unplayed" if not r["ended"] else "unknown_" + r["actual_reason"].split(" ")[0]
        out[r["season"]][key] += 1
    return dict(sorted(out.items()))


def main() -> None:
    conn = db.connect()
    try:
        skew = skew_report(conn)
        print(f"Skew report, season {skew['season']}: {skew['team_games']} team-games, "
              f"{skew['with_snapshot']} with a snapshot starter, {skew['both']} with both a snapshot "
              f"and a clean actual starter ({skew['snapshot_but_actual_undetermined']} more with a "
              "snapshot but no clean actual).")
        print(f"  mismatches: {len(skew['mismatches'])}/{skew['both']}; "
              f"mean |starter_sv inferred - actual| over all {skew['both']}: {skew['mean_abs_sv_diff']:.5f}")
        for m in skew["mismatches"]:
            print(f"  game {m['game_id']} {m['team_role']}: inferred {m['inferred']} "
                  f"sv {m['sv_inferred']:.4f}, actual {m['actual']} sv {m['sv_actual']:.4f}")
        print("\nStarter source per season (team-games):")
        for season, counts in source_counts(conn).items():
            print(f"  {season}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
