"""F3c reports on a built features.db: ice time, positions, lineup skew.

    python -m hockey_edge.features.availability_report

Read-only. Sections:
- ice time per season: skater rows with ice time > 0, gap team-games
  (skater total 0, toi_s NULL), and partial ones (total above 0 but below
  PARTIAL_SHARE x 5 skaters x game time). Report only, no rule.
- positions that do not map: game_rosters roles behind a NULL pos, stats
  rows without a roster row, snapshot roles that do not map.
- skew, 2027: team-games with both a pre-game snapshot lineup and an actual
  one; lineup set mismatches and the feature values each lineup gives.
- fallback gap, 2027: mean |missing_share_10 - missing_share_10_fallback|,
  what a missed lineup window costs.
- per season: lineup source counts, NULL missing_share_10 by reason, and
  its distribution.
"""

import json
import sqlite3
from collections import Counter, defaultdict

from hockey_edge.features import availability, db, lineups

PARTIAL_SHARE = 0.8
SKATERS = 5


def ice_time(conn: sqlite3.Connection) -> dict:
    per_season = {
        r["season"]: dict(r) for r in conn.execute("""
            SELECT season, COUNT(*) AS skater_rows, SUM(toi_s > 0) AS with_toi,
                   COUNT(DISTINCT game_id || ':' || team_id) AS team_games,
                   COUNT(DISTINCT CASE WHEN toi_s IS NULL THEN game_id || ':' || team_id END) AS gap_team_games
            FROM game_skater_toi GROUP BY season ORDER BY season
        """)
    }
    no_stats = conn.execute("""
        SELECT s.season, COUNT(*) FROM game_spine s
        WHERE s.is_liiga_competitive = 1 AND s.ended = 1 AND NOT EXISTS (
            SELECT 1 FROM game_skater_toi t WHERE t.season = s.season AND t.game_id = s.game_id)
        GROUP BY s.season
    """).fetchall()
    partial = [dict(r) for r in conn.execute(f"""
        SELECT t.season, t.game_id, t.team_id, s.end_type, g.game_time_seconds,
               SUM(t.toi_s) AS toi_s, COUNT(*) AS skaters,
               ROUND(1.0 * SUM(t.toi_s) / ({SKATERS} * g.game_time_seconds), 3) AS share
        FROM game_skater_toi t
        JOIN game_spine s USING (season, game_id)
        JOIN hockey.games g USING (season, game_id)
        WHERE t.toi_s IS NOT NULL
        GROUP BY t.season, t.game_id, t.team_id
        HAVING SUM(t.toi_s) < {PARTIAL_SHARE} * {SKATERS} * g.game_time_seconds
        ORDER BY t.season, t.game_id, t.team_id
    """)]
    return {"per_season": per_season, "games_without_stats": dict(no_stats), "partial": partial}


def unmapped_positions(conn: sqlite3.Connection) -> dict:
    roster = [dict(r) for r in conn.execute("""
        SELECT t.season, r.role, r.role_code, COUNT(*) AS n
        FROM game_skater_toi t
        JOIN hockey.game_rosters r ON r.season = t.season AND r.game_id = t.game_id
             AND r.team_id = t.team_id AND r.player_id = t.player_id
        WHERE t.pos IS NULL
        GROUP BY t.season, r.role, r.role_code ORDER BY t.season, n DESC
    """)]
    no_roster = conn.execute("""
        SELECT COUNT(*) FROM game_skater_toi t WHERE NOT EXISTS (
            SELECT 1 FROM hockey.game_rosters r WHERE r.season = t.season AND r.game_id = t.game_id
            AND r.team_id = t.team_id AND r.player_id = t.player_id)
    """).fetchone()[0]
    snapshot_roles: Counter = Counter()
    for (roster_json,) in conn.execute(
        "SELECT roster_json FROM snapshots.lineup_snapshots WHERE parsed = 1 AND starter_player_id IS NOT NULL"
    ):
        for p in json.loads(roster_json):
            if p["role"] != lineups.GOALIE_ROLE and lineups.position(p["role"]) is None:
                snapshot_roles[p["role"]] += 1
    return {"roster": roster, "no_roster_row": no_roster, "snapshot": dict(snapshot_roles)}


def skew_report(conn: sqlite3.Connection, season: int = 2027) -> dict:
    """Team-games with both lineups: the sets, and the features each gives."""
    both, mismatches, diffs = 0, [], defaultdict(list)
    for g in conn.execute(
        "SELECT * FROM game_spine WHERE season = ? AND is_liiga_competitive = 1 AND ended = 1 "
        "ORDER BY start_utc, game_id", (season,),
    ).fetchall():
        for role in ("home", "away"):
            team_id = g[f"{role}_team_id"]
            snapshot = lineups.snapshot_lineup(conn, g, team_id)
            actual = lineups.actual_lineup(conn, g["season"], g["game_id"], team_id)
            if snapshot is None or actual is None:
                continue
            both += 1
            if set(snapshot) != set(actual):
                mismatches.append({
                    "game_id": g["game_id"], "team_role": role, "team_id": team_id,
                    "snapshot_only": sorted(set(snapshot) - set(actual)),
                    "actual_only": sorted(set(actual) - set(snapshot)),
                })
            history = availability.team_history(conn, g, team_id)
            values = {
                name: availability.team_values(history, lineup)
                for name, lineup in (("snapshot", snapshot), ("actual", actual))
            }
            columns = [c for n in availability.WINDOWS for c in (f"n_window_games_{n}", *(
                f"{name}_{n}" for name in ("missing_share", "missing_share_f", "missing_share_d")))]
            for column, s, a in zip(columns, values["snapshot"], values["actual"]):
                if column.startswith("missing") and s is not None and a is not None:
                    diffs[column].append(abs(s - a))
    return {
        "season": season, "both": both, "mismatches": mismatches,
        "value_diffs": {
            c: {"n": len(d), "differ": sum(x > 1e-12 for x in d), "max": max(d, default=0.0),
                "mean": sum(d) / len(d) if d else None}
            for c, d in diffs.items()
        },
    }


def fallback_gap(conn: sqlite3.Connection, season: int = 2027) -> dict:
    out = {}
    for r in conn.execute("""
        SELECT lineup_source, COUNT(*) AS n,
               AVG(ABS(missing_share_10 - missing_share_10_fallback)) AS mean_abs,
               MAX(ABS(missing_share_10 - missing_share_10_fallback)) AS max_abs
        FROM features_availability
        WHERE season = ? AND missing_share_10 IS NOT NULL AND missing_share_10_fallback IS NOT NULL
        GROUP BY lineup_source
    """, (season,)):
        out[r["lineup_source"]] = dict(r)
    return out


def _quantile(values: list[float], q: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def season_summary(conn: sqlite3.Connection) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for r in conn.execute(
        "SELECT f.season, f.lineup_source, f.n_window_games_10, f.missing_share_10, s.ended "
        "FROM features_availability f JOIN game_spine s USING (game_id, season)"
    ):
        season = out.setdefault(r["season"], {"sources": Counter(), "nulls": Counter(), "values": []})
        source = r["lineup_source"]
        if source == lineups.UNKNOWN:
            source += "_unplayed" if not r["ended"] else "_no_stats"
        season["sources"][source] += 1
        if r["missing_share_10"] is None:
            season["nulls"]["empty_window" if r["n_window_games_10"] == 0 else "no_lineup"] += 1
        else:
            season["values"].append(r["missing_share_10"])
    for season in out.values():
        v = season.pop("values")
        season["missing_share_10"] = {
            "n": len(v), "mean": sum(v) / len(v), "zero": sum(x == 0 for x in v),
            "p10": _quantile(v, 0.1), "p50": _quantile(v, 0.5), "p90": _quantile(v, 0.9), "max": max(v),
        } if v else None
    return dict(sorted(out.items()))


def main() -> None:
    conn = db.connect()
    try:
        ice = ice_time(conn)
        print("Skater ice time per season (competitive ended games with stats rows):")
        for season, r in ice["per_season"].items():
            print(f"  {season}: {r['with_toi']}/{r['skater_rows']} skater rows with ice time > 0 "
                  f"({r['with_toi'] / r['skater_rows']:.1%}); {r['team_games']} team-games, "
                  f"{r['gap_team_games']} gap (toi_s NULL: total 0, or a GAP_DATES day)")
        print("  ended competitive games without any stats row: "
              + ", ".join(f"{s} {n}" for s, n in ice["games_without_stats"].items()))
        print(f"\nPartial ice time (total > 0 but < {PARTIAL_SHARE} x {SKATERS} x game time), report only: "
              f"{len(ice['partial'])} team-games")
        for p in ice["partial"]:
            print(f"  {p['season']}:{p['game_id']} {p['team_id']} {p['end_type']}: {p['toi_s']} s over "
                  f"{p['skaters']} skaters, game time {p['game_time_seconds']} s, share {p['share']}")

        unmapped = unmapped_positions(conn)
        print("\nPositions that do not map (pos NULL):")
        for r in unmapped["roster"]:
            print(f"  {r['season']}: role {r['role']!r} role_code {r['role_code']!r}: {r['n']} skater rows")
        print(f"  stats rows without a roster row: {unmapped['no_roster_row']}")
        print(f"  snapshot roles that do not map: {unmapped['snapshot'] or 'none'}")

        skew = skew_report(conn)
        print(f"\nSkew report, season {skew['season']}: {skew['both']} team-games with both a snapshot "
              f"and an actual lineup; lineup set mismatches: {len(skew['mismatches'])}")
        for m in skew["mismatches"]:
            print(f"  {m['game_id']} {m['team_role']} {m['team_id']}: snapshot only {m['snapshot_only']}, "
                  f"actual only {m['actual_only']}")
        for column, d in skew["value_diffs"].items():
            mean = f"{d['mean']:.5f}" if d["mean"] is not None else "-"
            print(f"  {column}: differs in {d['differ']}/{d['n']}, mean |diff| {mean}, max {d['max']:.5f}")

        print("\nFallback gap, 2027: |missing_share_10 - missing_share_10_fallback|")
        for source, r in fallback_gap(conn).items():
            print(f"  {source}: n {r['n']}, mean {r['mean_abs']:.4f}, max {r['max_abs']:.4f}")

        print("\nPer season: lineup source, NULL missing_share_10 by reason, distribution")
        for season, s in season_summary(conn).items():
            dist = s["missing_share_10"]
            dist_text = (
                f"n {dist['n']}, mean {dist['mean']:.3f}, p10 {dist['p10']:.3f}, p50 {dist['p50']:.3f}, "
                f"p90 {dist['p90']:.3f}, max {dist['max']:.3f}, exactly 0: {dist['zero']}"
            ) if dist else "none"
            print(f"  {season}: " + ", ".join(f"{k} {v}" for k, v in sorted(s["sources"].items()))
                  + " | NULL: " + (", ".join(f"{k} {v}" for k, v in sorted(s["nulls"].items())) or "0")
                  + f" | {dist_text}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
