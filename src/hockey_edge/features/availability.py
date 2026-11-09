"""Family 6: roster availability, the share of recent ice time missing.

Column definitions are on features_availability in db.py. Who dresses comes
from lineups.resolve_lineup; whose ice time counts comes only from the
team's earlier games this season, read through history.history_before with
game_skater_toi joined. The window is leak-free whichever way the lineup
was resolved.

The weight is skater ice time (every season has it; the F3c pre-check).
Ice-time gap games (toi_s NULL) are skipped as history, not counted as 0,
but still give a lineup: the fallback can be one, and a gap game is
featured like any other when it is the game being predicted.
"""

import sqlite3
from collections import defaultdict

from hockey_edge.features import lineups
from hockey_edge.features.history import history_before, target_games

WINDOWS = (5, 10)
FALLBACK_WINDOW = 10

# One row per earlier game of the team this season that has a lineup.
# toi_s is NULL on every row of a gap team-game, so DISTINCT gives one row
# per game.
_GAMES_SELECT = (
    "DISTINCT h.game_id AS game_id, h.start_utc AS start_utc, h.feeds_ratings AS rated, "
    "p.toi_s IS NOT NULL AS has_toi"
)
_TEAM = "h.season = :season AND p.team_id = :team"
_PLAYERS_SELECT = "h.game_id AS game_id, p.player_id AS player_id, p.pos AS pos, p.toi_s AS toi_s"


def team_history(conn: sqlite3.Connection, g: sqlite3.Row, team_id: str) -> dict:
    """The team's window games this season, newest first, as [{player_id:
    (pos, toi_s)}] (up to the longest window), and its last known lineup
    ({player_id: pos}, None when it has no earlier game this season)."""
    params = {"season": g["season"], "team": team_id}
    games = sorted(
        history_before(conn, g["season"], g["game_id"], _GAMES_SELECT, _TEAM, params, per_game="game_skater_toi"),
        key=lambda r: r["start_utc"], reverse=True,
    )
    window_ids = [r["game_id"] for r in games if r["rated"] and r["has_toi"]][:max(WINDOWS)]
    fallback_id = games[0]["game_id"] if games else None
    wanted = list(dict.fromkeys(window_ids + ([fallback_id] if fallback_id is not None else [])))
    players: dict[int, dict] = defaultdict(dict)
    if wanted:
        ids = {f"g{i}": game_id for i, game_id in enumerate(wanted)}
        for r in history_before(
            conn, g["season"], g["game_id"], _PLAYERS_SELECT,
            f"{_TEAM} AND h.game_id IN ({', '.join(':' + k for k in ids)})", {**params, **ids},
            per_game="game_skater_toi",
        ):
            players[r["game_id"]][r["player_id"]] = (r["pos"], r["toi_s"])
    return {
        "window": [players[game_id] for game_id in window_ids],
        "fallback": (
            {pid: pos for pid, (pos, _) in players[fallback_id].items()} if fallback_id is not None else None
        ),
    }


def missing_shares(window: list[dict], lineup: dict | set | None) -> tuple[float | None, float | None, float | None]:
    """(all, forwards, defence) share of the window's ice time held by
    window skaters absent from the lineup. None with an empty window or no
    lineup, and per position when that position has no ice time."""
    if not window or lineup is None:
        return None, None, None
    toi: dict[int, int] = defaultdict(int)
    pos: dict[int, str | None] = {}
    for game in window:  # newest first
        for pid, (p, t) in game.items():
            toi[pid] += t
            if pos.get(pid) is None:
                pos[pid] = p

    def share(players) -> float | None:
        total = sum(toi[pid] for pid in players)
        if not total:
            return None
        return sum(toi[pid] for pid in players if pid not in lineup) / total

    return (
        share(toi),
        share([pid for pid in toi if pos[pid] == "F"]),
        share([pid for pid in toi if pos[pid] == "D"]),
    )


def team_values(history: dict, lineup: dict | None) -> list:
    """The window columns of features_availability, in column order, for
    one lineup."""
    row = []
    for n in WINDOWS:
        window = history["window"][:n]
        row += [len(window), *missing_shares(window, lineup)]
    return row


def build(conn: sqlite3.Connection) -> None:
    rows = []
    for g in target_games(conn):
        for role in ("home", "away"):
            team_id = g[f"{role}_team_id"]
            lineup, source = lineups.resolve_lineup(conn, g["season"], g["game_id"], team_id)
            history = team_history(conn, g, team_id)
            if source == lineups.UNKNOWN:
                lineup = history["fallback"]
            fallback = missing_shares(history["window"][:FALLBACK_WINDOW], history["fallback"])[0]
            rows.append([
                g["game_id"], g["season"], role, team_id, source, *team_values(history, lineup), fallback,
            ])
    if rows:
        conn.executemany(
            f"INSERT INTO features_availability VALUES ({', '.join('?' * len(rows[0]))})", rows
        )
