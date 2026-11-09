"""F3a report on a built features.db: penalty direction per season.

    python -m hockey_edge.features.penalty_report

Read-only. The first table is comparable with docs/FEATURE_AUDIT.md item 7:
RUNKOSARJA player penalties resolved from the roster, stored on the
penalised player's own team (kept) or on the other team (swapped). The
second covers every competitive penalty by source. Then the unresolved rows
with the reason, the games with mixed direction (roster-resolved player
penalties both kept and swapped), and the goal-shortened check.

Goal-shortened check: a two-minute penalty that ends early at the second of
a goal was ended by that goal, so it was conceded by the team that did not
score. That gives a team independent of rosters and of liiga.fi's arrays,
for the penalties a power-play goal cut short.
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
               SUM(team_source = 'sufferer_roster') AS sufferer_roster,
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
                AND o.game_id = r.game_id AND o.team_source = 'roster' AND o.player_id > 0) AS n_roster,
               (SELECT COUNT(*) FROM hockey.game_rosters g WHERE g.season = r.season
                AND g.game_id = r.game_id AND g.player_id = r.player_id) AS in_rosters
        FROM penalty_events_resolved r JOIN game_spine s USING (game_id, season)
        WHERE r.team_source = 'unresolved' ORDER BY r.season, r.game_id, r.event_id
    """):
        if r["player_id"] > 0 and r["in_rosters"] == 0:
            reason = "player in neither roster"
        elif r["player_id"] > 0:
            reason = "player on both rosters, " + (
                "no roster-resolved player penalty in the game" if r["n_roster"] == 0 else "game direction tied")
        elif r["n_roster"] == 0:
            reason = "bench, no sufferer on one roster, no roster-resolved player penalty in the game"
        else:
            reason = "game direction tied"
        out.append({**dict(r), "reason": reason})
    return out


def goal_shortened(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Per season, kind (player/bench) and team_source: right / wrong counts.

    Goals exclude VT0 and RL0 events (no goal: the score does not change)
    and everything after regulation: an overtime goal ends the game, and so
    every penalty running, whoever scored. A penalty ended at a second with
    goals by both teams is left out.
    """
    return conn.execute("""
        WITH goals AS (
            SELECT e.season, e.game_id, e.game_time_seconds AS t,
                   MIN(e.team_id) AS team_id, COUNT(DISTINCT e.team_id) AS n_teams
            FROM hockey.game_goal_events e
            WHERE e.goal_types NOT LIKE '%"VT0"%' AND e.goal_types NOT LIKE '%"RL0"%'
              AND e.period <= 3
            GROUP BY e.season, e.game_id, e.game_time_seconds
        )
        SELECT r.season, CASE WHEN r.player_id > 0 THEN 'player' ELSE 'bench' END AS kind,
               r.team_source,
               SUM(r.team_id IS NOT NULL AND r.team_id <> g.team_id) AS right,
               SUM(r.team_id IS NULL OR r.team_id = g.team_id) AS wrong
        FROM penalty_events_resolved r
        JOIN goals g ON g.season = r.season AND g.game_id = r.game_id AND g.t = r.penalty_end_time
        WHERE r.penalty_minutes = 2 AND r.penalty_end_time - r.penalty_begin_time BETWEEN 1 AND 119
          AND g.n_teams = 1
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """).fetchall()


def mixed_games(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT r.season, r.game_id, s.phase,
               SUM(r.was_swapped = 0) AS kept, SUM(r.was_swapped = 1) AS swapped
        FROM penalty_events_resolved r JOIN game_spine s USING (game_id, season)
        WHERE r.team_source = 'roster' AND r.player_id > 0
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
        print("  season      n  roster  sufferer  game_dir  unresolved  swapped  bench  bench_swapped")
        for r in source_table(conn):
            print(f"  {r['season']}  {r['n']:5}  {r['roster']:6}  {r['sufferer_roster']:8}  {r['game_direction']:8}  "
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
        print("\nGoal-shortened check (2-min penalties ended by a goal; right = the team that did not score):")
        totals: dict = {}
        for r in goal_shortened(conn):
            print(f"  {r['season']} {r['kind']:6} {r['team_source']:15} right {r['right']:4}  wrong {r['wrong']}")
            t = totals.setdefault((r["kind"], r["team_source"]), [0, 0])
            t[0] += r["right"]
            t[1] += r["wrong"]
        for (kind, source), (right, wrong) in sorted(totals.items()):
            print(f"  all  {kind:6} {source:15} right {right:4}  wrong {wrong}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
