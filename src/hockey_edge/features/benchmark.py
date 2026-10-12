"""benchmark_odds: odds-implied 1X2 probabilities, for evaluation only.

Not a feature. No feature or model code may read this table or
`odds_snapshots`; tests/features/test_point_in_time.py enforces that.

Per game: the last parsed regulation-1X2 row captured strictly before
`start_utc`, from Pinnacle if it has one, otherwise bet365. Pinnacle wins
even when its last parsed row is older than bet365's (it can go inactive
near puck drop), so `minutes_before_start` is stored for filtering.
"""

import sqlite3
from datetime import datetime

PRIMARY_BOOK = "pinnacle"
FALLBACK_BOOK = "bet365"
MARKET = "1x2_regulation"
# Each implied probability divided by their sum.
VIG_METHOD = "proportional"


def remove_vig(home_odds: float, draw_odds: float, away_odds: float) -> tuple[float, float, float, float]:
    """(p_home, p_draw, p_away, overround) from decimal odds."""
    implied = [1 / home_odds, 1 / draw_odds, 1 / away_odds]
    overround = sum(implied)
    return implied[0] / overround, implied[1] / overround, implied[2] / overround, overround


def build(conn: sqlite3.Connection) -> None:
    # Timestamps are compared as datetimes: captured_at ('...+00:00', with
    # microseconds) and start_utc ('...Z') do not sort correctly as text.
    last: dict[tuple[int, int, str], tuple] = {}
    for o in conn.execute(
        """
        SELECT o.id, o.book, o.captured_at, o.home_odds, o.draw_odds, o.away_odds,
               s.game_id, s.season, s.start_utc
        FROM snapshots.odds_snapshots o
        JOIN game_spine s ON s.season = o.season AND s.game_id = o.game_id
        WHERE o.league = 'liiga' AND o.parsed = 1 AND o.market = ? AND o.book IN (?, ?)
          AND o.home_odds > 1 AND o.draw_odds > 1 AND o.away_odds > 1
        ORDER BY o.id
        """,
        (MARKET, PRIMARY_BOOK, FALLBACK_BOOK),
    ):
        captured = datetime.fromisoformat(o["captured_at"])
        start = datetime.fromisoformat(o["start_utc"])
        if captured >= start:
            continue
        key = (o["game_id"], o["season"], o["book"])
        if key not in last or captured >= last[key][0]:
            last[key] = (captured, start, o)

    rows = []
    for game_id, season in sorted({key[:2] for key in last}):
        book = PRIMARY_BOOK if (game_id, season, PRIMARY_BOOK) in last else FALLBACK_BOOK
        captured, start, o = last[(game_id, season, book)]
        p_home, p_draw, p_away, overround = remove_vig(o["home_odds"], o["draw_odds"], o["away_odds"])
        rows.append((
            game_id, season, book, int(book != PRIMARY_BOOK), o["id"], o["captured_at"],
            (start - captured).total_seconds() / 60, o["home_odds"], o["draw_odds"], o["away_odds"],
            overround, p_home, p_draw, p_away, VIG_METHOD,
        ))
    conn.executemany(
        "INSERT INTO benchmark_odds VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
