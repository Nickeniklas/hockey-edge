"""Who dressed: skater ice time per game, and the lineup resolver.

game_skater_toi (definition on its DDL in db.py) is the historical lineup:
the players with a game_player_period_stats row, with their ice time.
game_rosters is the squad list, not the lineup, so it is used only for the
player's position in that game (role, else its role_code letter). Goalies are not in
game_player_period_stats; F2a covers them.

resolve_lineup mirrors starters.resolve_starter: the pre-game snapshot when
one exists, else the actual lineup for an ended game (training only), else
unknown, and availability.py falls back to the team's last known lineup.
Table names are read here, so availability.py stays SQL-free.
"""

import json
import sqlite3

from hockey_edge.features import pregame

SNAPSHOT = "snapshot"
ACTUAL = "actual"
UNKNOWN = "unknown"

GOALIE_ROLE = "GOALIE"


# Days on which liiga.fi's skater ice time is broken for every game, named
# rather than found by a threshold (Niklas, 2026-11-09). 2016-01-05: four
# team-games total 0 and the other six 2-17% of a full game.
GAP_DATES = ("2016-01-05",)

# game_rosters.role_code letter, after any ordinal prefix ("13. H").
_ROLE_CODE_POS = {"H": "F", "KH": "F", "P": "D"}


def position(role: str | None, role_code: str | None = None) -> str | None:
    """'D' for a defenceman, 'F' for another skater, None when neither
    says. role first: its vocabulary is open-ended (SEVENTH_DEFENSEMAN,
    THIRTEENTH_STRIKER, plain DEFENSEMAN/STRIKER in early seasons), so it
    maps by suffix. When role is NULL, game_rosters.role_code by its letter
    (H or KH -> F, P -> D; "13. H" is H), as decided 2026-11-09 for the
    2015-2019 rows with role NULL. Only game_rosters' role_code: a
    snapshot's roleCode is never passed. availability_report lists
    whatever falls through."""
    if role is not None:
        if role.endswith("DEFENSEMAN"):
            return "D"
        if role.endswith(("CENTER", "WING", "STRIKER")):
            return "F"
        return None
    if role_code is None:
        return None
    return _ROLE_CODE_POS.get(role_code.split(".")[-1].strip())


def _build_game_skater_toi(conn: sqlite3.Connection) -> None:
    rows = conn.execute("""
        SELECT p.game_id, p.season, p.team_id, p.player_id, r.role, r.role_code,
               SUM(COALESCE(p.time_on_ice_seconds, 0)) AS toi_s
        FROM hockey.game_player_period_stats p
        JOIN game_spine s ON s.season = p.season AND s.game_id = p.game_id
        LEFT JOIN hockey.game_rosters r ON r.season = p.season AND r.game_id = p.game_id
             AND r.team_id = p.team_id AND r.player_id = p.player_id
        WHERE s.is_liiga_competitive = 1 AND s.ended = 1
        GROUP BY p.season, p.game_id, p.team_id, p.player_id
    """).fetchall()
    conn.executemany(
        "INSERT INTO game_skater_toi VALUES (?, ?, ?, ?, ?, ?)",
        [
            (r["game_id"], r["season"], r["team_id"], r["player_id"], position(r["role"], r["role_code"]), r["toi_s"])
            for r in rows
        ],
    )
    # Ice-time gap: a team-game whose skaters total 0 has no ice time at
    # all (2016, 2021:51700 HPK, ten 2025 PLAYOUT/QUALIFICATIONS games), and
    # every team-game on a GAP_DATES day.
    conn.execute(f"""
        UPDATE game_skater_toi SET toi_s = NULL
        WHERE (season, game_id, team_id) IN (
            SELECT season, game_id, team_id FROM game_skater_toi
            GROUP BY season, game_id, team_id HAVING SUM(toi_s) = 0)
        OR (season, game_id) IN (
            SELECT season, game_id FROM game_spine
            WHERE start_date IN ({', '.join('?' * len(GAP_DATES))}))
    """, GAP_DATES)


def build(conn: sqlite3.Connection) -> None:
    _build_game_skater_toi(conn)


def _game(conn: sqlite3.Connection, season: int, game_id: int) -> sqlite3.Row:
    g = conn.execute("SELECT * FROM game_spine WHERE season = ? AND game_id = ?", (season, game_id)).fetchone()
    if g is None:
        raise KeyError(f"no game_spine row for season={season} game_id={game_id}")
    return g


def team_role(g: sqlite3.Row, team_id: str) -> str:
    if team_id == g["home_team_id"]:
        return "home"
    if team_id == g["away_team_id"]:
        return "away"
    raise KeyError(f"{team_id} does not play season={g['season']} game_id={g['game_id']}")


def snapshot_lineup(conn: sqlite3.Connection, g: sqlite3.Row, team_id: str) -> dict[int, str | None] | None:
    """{player_id: pos} of the confirmed skaters in the pre-game snapshot
    (pregame.latest_pregame_snapshot, as for the starter), or None. The
    snapshot's side maps to a team through game_spine's home/away team."""
    snapshot = pregame.latest_pregame_snapshot(conn, g, team_role(g, team_id))
    if snapshot is None:
        return None
    return {
        p["id"]: position(p["role"])
        for p in json.loads(snapshot["roster_json"]) if p["role"] != GOALIE_ROLE
    }


def actual_lineup(conn: sqlite3.Connection, season: int, game_id: int, team_id: str) -> dict[int, str | None] | None:
    """{player_id: pos} of the team's skaters with a stats row, or None."""
    rows = conn.execute(
        "SELECT player_id, pos FROM game_skater_toi WHERE season = ? AND game_id = ? AND team_id = ?",
        (season, game_id, team_id),
    ).fetchall()
    return {r["player_id"]: r["pos"] for r in rows} or None


def resolve_lineup(
    conn: sqlite3.Connection, season: int, game_id: int, team_id: str
) -> tuple[dict[int, str | None] | None, str]:
    """(skaters {player_id: pos}, source) for one team in one game.

    The pre-game snapshot when one exists; else the players with a stats
    row for an ended game (historical training only; an unplayed game has
    none); else (None, 'unknown'), and availability.py uses the team's last
    known lineup. Goalies are left out.
    """
    g = _game(conn, season, game_id)
    lineup = snapshot_lineup(conn, g, team_id)
    if lineup is not None:
        return lineup, SNAPSHOT
    if g["ended"]:
        lineup = actual_lineup(conn, season, game_id, team_id)
        if lineup is not None:
            return lineup, ACTUAL
    return None, UNKNOWN
