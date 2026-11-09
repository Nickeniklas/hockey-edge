"""F3d reports on a built features.db: lines, final tables, clinching.

    python -m hockey_edge.features.motivation_report

Read-only. Per season:
- the lines used: playoff spots and relegation zone from season_format,
  checked against who played PLAYOFFS (the top N of the derived final
  table) and, for 2025, PLAYOUT (the bottom N);
- the final-table check: the derived final table (3/2/1/0 points, then goal
  difference) against liiga.fi's own standings in hockey.standings (phase
  'season'): points and games per team, and the order. A derived team
  missing from liiga.fi's rows is matched to a stray row with the same games
  and points, if there is exactly one (2015-2016 file Espoo Blues' record
  under K-Espoo's id);
- clinched / eliminated / safe / doomed team-games in each team's last 10
  regular-season games (games_remaining <= LAST_ROUNDS).
"""

import sqlite3

from hockey_edge.features import db, motivation

LAST_ROUNDS = 10


def final_table(conn: sqlite3.Connection, season: int) -> list[dict]:
    teams = [r[0] for r in conn.execute("SELECT team_id FROM team_season WHERE season = ?", (season,))]
    games = conn.execute(
        "SELECT home_team_id AS home, away_team_id AS away, home_goals, away_goals, end_type "
        "FROM game_spine WHERE season = ? AND phase = 'RUNKOSARJA' AND ended = 1", (season,),
    ).fetchall()
    return motivation.table(teams, games)


def _phase_teams(conn: sqlite3.Connection, season: int, phase: str) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT home_team_id FROM game_spine WHERE season = :s AND phase = :p "
        "UNION SELECT away_team_id FROM game_spine WHERE season = :s AND phase = :p",
        {"s": season, "p": phase},
    )}


def lines_check(conn: sqlite3.Connection, fmt: sqlite3.Row, ranked: list[dict]) -> dict:
    season = fmt["season"]
    complete = fmt["runkosarja_games"] == fmt["runkosarja_ended"]
    out = {"playoffs": None, "playout": None}
    playoff = _phase_teams(conn, season, "PLAYOFFS")
    if complete and playoff:
        top = {r["team_id"] for r in ranked[:fmt["playoff_spots"]]}
        out["playoffs"] = "top %d = PLAYOFFS teams" % fmt["playoff_spots"] if top == playoff else (
            f"differs: top only {sorted(top - playoff)}, PLAYOFFS only {sorted(playoff - top)}")
    playout = _phase_teams(conn, season, "PLAYOUT")
    if complete and playout:
        bottom = {r["team_id"] for r in ranked[len(ranked) - fmt["relegation_spots"]:]} if fmt["relegation_spots"] else set()
        out["playout"] = "bottom %d = PLAYOUT teams" % len(bottom) if bottom == playout else (
            f"differs: bottom only {sorted(bottom - playout)}, PLAYOUT only {sorted(playout - bottom)}")
    return out


def standings_check(conn: sqlite3.Connection, season: int, ranked: list[dict]) -> dict:
    official = {
        r["team_id"]: dict(r) for r in conn.execute(
            "SELECT team_id, team_name, ranking, games, points FROM hockey.standings "
            "WHERE season = ? AND phase = 'season'", (season,),
        )
    }
    if not official:
        return {"available": False}
    derived_ids = {r["team_id"] for r in ranked}
    stray = {t: o for t, o in official.items() if t not in derived_ids}
    matched, aliases, mismatches = {}, [], []
    for r in ranked:
        o = official.get(r["team_id"])
        if o is None or (o["games"], o["points"]) != (r["games_played"], r["points"]):
            same = [t for t, s in stray.items() if (s["games"], s["points"]) == (r["games_played"], r["points"])]
            if len(same) == 1:
                aliases.append((r["team_id"], same[0]))
                o = stray.pop(same[0])
        if o is None or (o["games"], o["points"]) != (r["games_played"], r["points"]):
            mismatches.append((r["team_id"], r["games_played"], r["points"], o and (o["games"], o["points"])))
            continue
        matched[r["team_id"]] = o["ranking"]
    # Order: liiga.fi's ranking restricted to the matched teams.
    official_order = sorted(matched, key=lambda t: matched[t])
    derived_order = [r["team_id"] for r in ranked if r["team_id"] in matched]
    points = {r["team_id"]: r["points"] for r in ranked}
    swaps = [
        (i + 1, d, o, points[d] == points[o])
        for i, (d, o) in enumerate(zip(derived_order, official_order)) if d != o
    ]
    return {
        "available": True, "teams": len(ranked), "points_match": len(matched), "aliases": aliases,
        "mismatches": mismatches, "stray_rows": sorted(stray), "order_differs": swaps,
    }


def late_flags(conn: sqlite3.Connection) -> dict[int, dict]:
    out = {}
    for r in conn.execute(f"""
        SELECT season, COUNT(*) AS n, SUM(playoff_clinched) AS clinched, SUM(playoff_eliminated) AS eliminated,
               SUM(relegation_safe) AS safe, SUM(relegation_doomed) AS doomed
        FROM features_motivation WHERE games_remaining IS NOT NULL AND games_remaining <= {LAST_ROUNDS}
        GROUP BY season ORDER BY season
    """):
        out[r["season"]] = dict(r)
    return out


def main() -> None:
    conn = db.connect()
    try:
        flags = late_flags(conn)
        for fmt in conn.execute("SELECT * FROM season_format ORDER BY season").fetchall():
            season = fmt["season"]
            ranked = final_table(conn, season)
            complete = fmt["runkosarja_games"] == fmt["runkosarja_ended"]
            zone = (f"bottom {fmt['relegation_spots']} ({fmt['relegation']})"
                    if fmt["relegation_spots"] else f"none ({fmt['relegation']})")
            print(f"{season}: {fmt['n_teams']} teams, {fmt['games_per_team']} games, playoff spots "
                  f"{fmt['playoff_spots']} (played: {fmt['playoff_teams'] or 0} teams), relegation zone {zone}"
                  + ("" if complete else f"; regular season in progress {fmt['runkosarja_ended']}/{fmt['runkosarja_games']}"))
            lines = lines_check(conn, fmt, ranked)
            for name, text in lines.items():
                if text:
                    print(f"  lines: {text}")
            st = standings_check(conn, season, ranked)
            if not st["available"]:
                print("  final table: no liiga.fi standings in hockey.db")
            else:
                print(f"  final table: points and games match liiga.fi for {st['points_match']}/{st['teams']} teams"
                      + (f" ({', '.join(f'{a} under {b}' for a, b in st['aliases'])})" if st["aliases"] else "")
                      + (f"; mismatches {st['mismatches']}" if st["mismatches"] else "")
                      + (f"; liiga.fi rows left over: {st['stray_rows']}" if st["stray_rows"] else ""))
                if st["order_differs"]:
                    level = all(s[3] for s in st["order_differs"])
                    print(f"  order differs at {len(st['order_differs'])} places "
                          f"({'all between teams level on points' if level else 'NOT all level on points'}): "
                          + ", ".join(f"{p}: {d} / liiga.fi {o}" for p, d, o, _ in st["order_differs"]))
                else:
                    print("  order: identical to liiga.fi")
            f = flags.get(season)
            if f:
                rel = (f", relegation safe {f['safe']}, doomed {f['doomed']}" if f["safe"] is not None else "")
                print(f"  last {LAST_ROUNDS} games per team: {f['n']} team-games, playoff clinched "
                      f"{f['clinched']}, eliminated {f['eliminated']}{rel}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
