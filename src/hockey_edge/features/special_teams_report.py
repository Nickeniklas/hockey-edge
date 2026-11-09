"""F3b report on a built features.db: special teams per season.

    python -m hockey_edge.features.special_teams_report

Read-only. Regular season only, games with penalty data:
- opportunities, power-play goals and penalties taken per team-game, and
  league PP%;
- the derived opportunities against liiga.fi's stored powerplay_instances
  (exact / within one / correlation), and the labelled power-play goals
  against the stored powerplay_goals, where those are stored (not
  2025/2026, stripped);
- PP-goal sources: liiga.fi's YV/YV2 label against the man advantage
  derived from the resolved penalties' timing, per goal;
- the games with no penalty data.
"""

import json
import sqlite3
import statistics
from collections import Counter, defaultdict

from hockey_edge.features import db, team_penalties

STRIPPED = (2025, 2026)


def per_season(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT t.season, COUNT(*) AS team_games,
               AVG(t.pp_opportunities) AS opp, AVG(t.pp_goals) AS ppg, AVG(t.penalties_taken) AS taken,
               SUM(t.pp_goals) * 1.0 / SUM(t.pp_opportunities) AS league_pp_pct
        FROM game_team_special_teams t JOIN game_spine s USING (game_id, season)
        WHERE s.phase = 'RUNKOSARJA' AND t.pp_opportunities IS NOT NULL
        GROUP BY t.season ORDER BY t.season
    """).fetchall()


def _corr(x: list, y: list) -> float | None:
    try:
        return statistics.correlation(x, y)
    except statistics.StatisticsError:
        return None


def against_stored(conn: sqlite3.Connection) -> dict[int, dict]:
    rows = conn.execute("""
        SELECT t.season, t.pp_opportunities AS opp, t.pp_goals AS ppg,
               SUM(p.powerplay_instances) AS stored_opp, SUM(p.powerplay_goals) AS stored_ppg
        FROM game_team_special_teams t JOIN game_spine s USING (game_id, season)
        JOIN hockey.game_team_period_stats p
             ON p.season = t.season AND p.game_id = t.game_id AND p.team_id = t.team_id
        WHERE s.phase = 'RUNKOSARJA' AND t.pp_opportunities IS NOT NULL
        GROUP BY t.season, t.game_id, t.team_id
    """).fetchall()
    out = {}
    for season in sorted({r["season"] for r in rows} - set(STRIPPED)):
        rs = [r for r in rows if r["season"] == season]
        opp, stored = [r["opp"] for r in rs], [r["stored_opp"] for r in rs]
        out[season] = {
            "n": len(rs),
            "stored_opp": statistics.mean(stored),
            "exact": sum(a == b for a, b in zip(opp, stored)) / len(rs),
            "within1": sum(abs(a - b) <= 1 for a, b in zip(opp, stored)) / len(rs),
            "corr": _corr(opp, stored),
            "ppg_exact": sum(r["ppg"] == r["stored_ppg"] for r in rs) / len(rs),
        }
    return out


def pp_goal_sources(conn: sqlite3.Connection) -> dict[int, Counter]:
    """Per season, real goals in games with penalty data counted by
    (label, timing), each 'pp', 'sh' or 'ev' for the scoring team. Timing:
    counted penalties running at the goal (begin < t <= end), the team's
    against the opponent's."""
    pens = defaultdict(list)
    for r in conn.execute(
        f"SELECT season, game_id, team_id, penalty_begin_time AS b, penalty_end_time AS e "
        f"FROM penalty_events_resolved WHERE {team_penalties.COUNTED}"
    ):
        pens[(r["season"], r["game_id"])].append(r)
    out: dict[int, Counter] = defaultdict(Counter)
    for e in conn.execute(f"""
        SELECT e.season, e.game_id, e.team_id, e.game_time_seconds AS t, e.goal_types
        FROM hockey.game_goal_events e JOIN game_spine s USING (game_id, season)
        JOIN (SELECT DISTINCT season, game_id FROM game_team_special_teams
              WHERE pp_opportunities IS NOT NULL) d USING (season, game_id)
        WHERE {team_penalties.REAL_GOAL}
    """):
        running = [p for p in pens[(e["season"], e["game_id"])] if p["b"] < e["t"] <= p["e"]]
        own = sum(p["team_id"] == e["team_id"] for p in running)
        opp = len(running) - own
        timing = "pp" if opp > own else "sh" if own > opp else "ev"
        labels = set(json.loads(e["goal_types"]))
        label = "pp" if labels & {"YV", "YV2"} else "sh" if labels & {"AV", "AV2"} else "ev"
        out[e["season"]][(label, timing)] += 1
    return dict(sorted(out.items()))


def main() -> None:
    conn = db.connect()
    try:
        print("Regular season, per team-game (games with penalty data):")
        print("  season  team-games  opp   PP goals  taken  league PP%")
        for r in per_season(conn):
            print(f"  {r['season']}  {r['team_games']:10}  {r['opp']:.2f}  {r['ppg']:8.2f}  "
                  f"{r['taken']:5.2f}  {r['league_pp_pct']:.1%}")
        print("\nDerived vs stored (liiga.fi team period stats; 2025/2026 stripped, left out):")
        print("  season  stored opp  opp exact / within 1 / corr   PP goals exact")
        for season, d in against_stored(conn).items():
            corr = f"{d['corr']:.2f}" if d["corr"] is not None else "-"
            print(f"  {season}  {d['stored_opp']:10.2f}  {d['exact']:5.0%} / {d['within1']:4.0%} / {corr}"
                  f"          {d['ppg_exact']:.1%}")
        print("\nPP-goal sources, real goals (label vs penalty timing):")
        print("  season  agree   label PP  timing PP  label PP/timing ev  label ev/timing PP")
        for season, t in pp_goal_sources(conn).items():
            n = sum(t.values())
            agree = sum(v for (a, b), v in t.items() if a == b) / n
            label_pp = sum(v for (a, _), v in t.items() if a == "pp")
            timing_pp = sum(v for (_, b), v in t.items() if b == "pp")
            print(f"  {season}  {agree:.1%}  {label_pp:8}  {timing_pp:9}  {t[('pp', 'ev')]:18}  {t[('ev', 'pp')]:18}")
        missing = conn.execute(
            "SELECT DISTINCT season, game_id FROM game_team_special_teams WHERE pp_opportunities IS NULL "
            "ORDER BY season, game_id"
        ).fetchall()
        print(f"\nGames with no penalty data (counts NULL): {len(missing)}: "
              + ", ".join(f"{r['season']}:{r['game_id']}" for r in missing))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
