"""Family 2: shot attempts (Corsi only, D4) and team xG, rolling per game.

Column definitions are on features_shots in db.py. History is read only
through history.history_before with game_team_shots joined, so a game's
windows hold only games from earlier dates.

Rates are per game, not per 60 minutes: even-strength minutes need penalty
timing on corrected teams, which exists only after F3. Games without
shot_events are skipped in the Corsi windows; games without game xG
(everything before 2021) are skipped in the xG windows. Nothing here uses
liiga.fi's corsi columns, team period stats or on-ice counts.
"""

import sqlite3

from hockey_edge.features.history import history_before, target_games

WINDOWS = (10, 25, 40)
# This season and the one before it: the 40-game window reaches back into
# last season for the first part of a season.
LOOKBACK_SEASONS = 2

_SELECT = (
    "h.start_utc AS start_utc, h.season AS season, "
    "p.corsi_for AS cf, p.corsi_against AS ca, p.ev_corsi_for AS ev_cf, p.ev_corsi_against AS ev_ca, "
    "p.xg_for AS xgf, p.xg_against AS xga"
)
_WHERE = "h.feeds_ratings = 1 AND p.season >= :first_season AND p.team_id = :team"


def _per_game(games: list[sqlite3.Row], key: str) -> float | None:
    return sum(g[key] for g in games) / len(games) if games else None


def _share(games: list[sqlite3.Row], for_key: str, against_key: str) -> float | None:
    total = sum(g[for_key] + g[against_key] for g in games)
    return sum(g[for_key] for g in games) / total if total else None


def team_row(conn: sqlite3.Connection, g: sqlite3.Row, team_id: str) -> list:
    """features_shots values after team_role and team_id, in column order."""
    games = sorted(
        history_before(
            conn, g["season"], g["game_id"], _SELECT, _WHERE,
            {"first_season": g["season"] - LOOKBACK_SEASONS + 1, "team": team_id},
            per_game="game_team_shots",
        ),
        key=lambda r: r["start_utc"], reverse=True,
    )
    shots = [r for r in games if r["cf"] is not None]
    xg = [r for r in games if r["xgf"] is not None]
    row = [sum(r["season"] == g["season"] for r in shots)]
    for n in WINDOWS:
        w = shots[:n]
        row += [
            len(w), _per_game(w, "cf"), _per_game(w, "ca"), _share(w, "cf", "ca"),
            _per_game(w, "ev_cf"), _per_game(w, "ev_ca"), _share(w, "ev_cf", "ev_ca"),
        ]
    row.append(int(bool(xg)))
    for n in WINDOWS:
        w = xg[:n]
        row += [len(w), _per_game(w, "xgf"), _per_game(w, "xga")]
    return row


def build(conn: sqlite3.Connection) -> None:
    rows = []
    for g in target_games(conn):
        for role in ("home", "away"):
            team_id = g[f"{role}_team_id"]
            rows.append([g["game_id"], g["season"], role, team_id, *team_row(conn, g, team_id)])
    if rows:
        conn.executemany(f"INSERT INTO features_shots VALUES ({', '.join('?' * len(rows[0]))})", rows)
