"""The only door to past games (hard rule 1, no leakage).

Feature modules read games through the two functions here and nowhere else;
tests/features/test_point_in_time.py enforces that.

History for a game is every ended game on an **earlier UTC date**. That is
stricter than "started earlier": a game that started 90 minutes before this
one is still being played at puck drop, and its stored result would leak.
No Liiga game has started before 09:00 or after 21:00 UTC, so a game from an
earlier date has always finished. `games.end_utc` cannot be used for this:
it is null for 2025 and earlier than `start_utc` for some games.
"""

import sqlite3


def target_games(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Games that get feature rows: every competitive game, played or not."""
    return conn.execute(
        "SELECT * FROM game_spine WHERE is_liiga_competitive = 1 "
        "ORDER BY start_utc, season, game_id"
    ).fetchall()


def history_before(
    conn: sqlite3.Connection,
    season: int,
    game_id: int,
    select: str = "h.*",
    where: str = "",
    params: dict | None = None,
) -> list[sqlite3.Row]:
    """Rows of game_spine (alias `h`) for ended games before this game.

    `select` and `where` are SQL fragments over `h` with named parameters
    from `params`; they narrow or aggregate the history, they cannot widen
    it. Rows come back in no particular order.
    """
    target = conn.execute(
        "SELECT start_date FROM game_spine WHERE season = ? AND game_id = ?",
        (season, game_id),
    ).fetchone()
    if target is None:
        raise KeyError(f"no game_spine row for season={season} game_id={game_id}")
    sql = f"SELECT {select} FROM game_spine h WHERE h.ended = 1 AND h.start_date < :_cutoff"
    if where:
        sql += f" AND ({where})"
    return conn.execute(sql, {**(params or {}), "_cutoff": target["start_date"]}).fetchall()
