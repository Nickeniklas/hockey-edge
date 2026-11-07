"""F3a report on a built features.db: penalty direction per season.

    python -m hockey_edge.features.penalty_report

Read-only. The first table is comparable with docs/FEATURE_AUDIT.md item 7:
RUNKOSARJA player penalties resolved from the roster, stored on the
penalised player's own team (kept) or on the other team (swapped). The
second covers every competitive penalty by source. Then the unresolved rows
with the reason, and the games with mixed direction (roster-resolved
penalties both kept and swapped).
"""

import sqlite3

from hockey_edge.features import db


def audit_table(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT r.season, SUM(r.was_swapped = 0) AS kept, SUM(r.was_swapped = 1) AS swapped
        FROM penalty_events_resolved r JOIN game_spine s USING (game_id, season)
        WHERE s.phase = 'RUNKOSARJA' AND r.team_source = 'roster'
        GROUP BY r.season ORDER BY r.season
    """).fetchall()


def source_table(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT season, COUNT(*) AS n,
               SUM(team_source = 'roster') AS roster,
               SUM(team_source = 'game_direction') AS game_direction,
               SUM(team_source = 'unresolved') AS unresolved,
               SUM(was_swapped = 1) AS swapped,
               SUM(player_id = 0) AS bench,
               SUM(player_id = 0 AND was_swapped = 1) AS bench_swapped
        FROM penalty_events_resolved GROUP BY season ORDER BY season
    """).fetchall()


def unresolved(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for r in conn.execute("""
        SELECT r.*, s.phase,
               (SELECT COUNT(*) FROM penalty_events_resolved o WHERE o.season = r.season
                AND o.game_id = r.game_id AND o.team_source = 'roster') AS n_roster,
               (SELECT COUNT(*) FROM hockey.game_rosters g WHERE g.season = r.season
                AND g.game_id = r.game_id AND g.player_id = r.player_id) AS in_rosters
        FROM penalty_events_resolved r JOIN game_spine s USING (game_id, season)
        WHERE r.team_source = 'unresolved' ORDER BY r.season, r.game_id, r.event_id
    """):
        if r["player_id"] > 0 and r["in_rosters"] == 0:
            reason = "player in neither roster"
        elif r["n_roster"] == 0:
            reason = "no roster-resolved penalty in the game"
        else:
            reason = "game direction tied"
        out.append({**dict(r), "reason": reason})
    return out


def mixed_games(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT r.season, r.game_id, s.phase,
               SUM(r.was_swapped = 0) AS kept, SUM(r.was_swapped = 1) AS swapped
        FROM penalty_events_resolved r JOIN game_spine s USING (game_id, season)
        WHERE r.team_source = 'roster'
        GROUP BY r.season, r.game_id HAVING kept > 0 AND swapped > 0
        ORDER BY r.season, r.game_id
    """).fetchall()


def main() -> None:
    conn = db.connect()
    try:
        print("RUNKOSARJA player penalties, roster-resolved (cf. FEATURE_AUDIT item 7):")
        print("  season   kept  swapped  swapped share")
        for r in audit_table(conn):
            print(f"  {r['season']}  {r['kept']:5}  {r['swapped']:7}  {r['swapped'] / (r['kept'] + r['swapped']):.1%}")
        print("\nAll competitive penalties by source:")
        print("  season      n  roster  game_dir  unresolved  swapped  bench  bench_swapped")
        for r in source_table(conn):
            print(f"  {r['season']}  {r['n']:5}  {r['roster']:6}  {r['game_direction']:8}  "
                  f"{r['unresolved']:10}  {r['swapped']:7}  {r['bench']:5}  {r['bench_swapped']:13}")
        rows = unresolved(conn)
        print(f"\nUnresolved rows: {len(rows)}")
        for r in rows:
            print(f"  {r['season']}:{r['game_id']} {r['phase']} event {r['event_id']} "
                  f"player {r['player_id']} stored {r['stored_team_id']}: {r['reason']}")
        mixed = mixed_games(conn)
        print(f"\nGames with mixed direction: {len(mixed)}")
        for r in mixed:
            print(f"  {r['season']}:{r['game_id']} {r['phase']}: kept {r['kept']}, swapped {r['swapped']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
