"""Write fixtures/features/mini.sql: a slice of real rows for the feature tests.

    python scripts/make_feature_fixture.py

One pass over data/hockey.db and data/snapshots.db, both opened read-only,
writing the whole file. INSERT statements only: the tests create the tables
from the real DDL. Each slice keeps only the rows and the columns that the
feature build, its reports and scripts/verify_starters.py read (plus NOT
NULL columns, which must be filled); every other column is left out and
loads as NULL. No row id is written: SQLite numbers the rows on load, and
a nightly resync renumbers them in hockey.db anyway, so tests never pin
them. odds_snapshots and lineup_snapshots keep their ids: snapshots.db is
append-only, so those never change, and the ids break ties.

Rows (docs/FEATURE_STORE_PLAN.md "Fixtures"):

- games: seasons 2022, 2025 and 2027 in full (every build step reads them;
  the build accepts only whole seasons). 2022 has the tied regular-time
  playoff game 49294 and the 49298 tiebreak; 2025 has the PLAYOUT and
  QUALIFICATIONS series, K-Espoo's first season and foreign friendlies;
  2027 has Jokerit, PITSITURNAUS, unplayed games and the games with odds.
- odds_snapshots: every row of four 2027 games, raw_payload blanked.
- F2a: goalie period stats and goalkeeper events for the competitive games
  of 2025 and 2027; the parsed 2027 lineup snapshots that name a starter
  (the only ones read), raw_payload blanked and roster_json cut to each
  player's id and role.
- F2b: shot_events for the competitive games of 2027 and of 2025 from
  2025-01-22 to 2025-02-23, which runs into the 2025 shot_events gap (game
  393 on).
- F3a/F3b: penalty and goal events for the ended competitive games of 2027
  and of the same 2025 stretch (EVENT_GAMES), and for five games picked per
  case of the penalty-team correction (PENALTY_GAMES).
- F3c: skater period stats (keys and ice time) for the ended competitive
  games of 2027 and the 2025 games from 2025-02-01 of Pelicans, Jukurit and
  Jokerit (SKATER_GAMES: their regular-season run-in, then the ice-time gap
  of the PLAYOUT and QUALIFICATIONS series).
- game_rosters: the rows of every player a penalty names (penalised player
  or sufferer; all the penalty-team rule reads) and of every skater with a
  stats row; role and role_code (they give pos).
- F3d reads games only.

Season 2027 is live, so regenerating this changes the 2027 rows; the tests
that count 2027 games or snapshots are re-pinned when it is regenerated.
"""

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "fixtures" / "features" / "mini.sql"
SOURCES = {"hockey": ROOT / "data" / "hockey.db", "snapshots": ROOT / "data" / "snapshots.db"}

GAME_SEASONS = (2022, 2025, 2027)
# 2701309: both books, many polls. 2701319: rows captured after the start.
# 2701332: Pinnacle inactive on the closing poll. 2701368: Pinnacle only.
ODDS_GAMES = (2701309, 2701319, 2701332, 2701368)
COMPETITIVE = "('RUNKOSARJA', 'PLAYOFFS', 'PLAYOUT', 'QUALIFICATIONS')"


def games_where(condition: str) -> str:
    """(game_id, season) of the games matching an SQL condition on hockey.games."""
    return f"(game_id, season) IN (SELECT game_id, season FROM hockey.games WHERE {condition})"


# The 2025 stretch with shot_events, running into the gap.
SHOT_STRETCH = f"(season = 2027 OR (season = 2025 AND start_utc >= '2025-01-22' AND start_utc < '2025-02-24'))"
GOALIE_GAMES = games_where(f"season IN (2025, 2027) AND phase IN {COMPETITIVE}")
SHOT_GAMES = games_where(f"phase IN {COMPETITIVE} AND {SHOT_STRETCH}")
EVENT_GAMES = games_where(f"ended = 1 AND phase IN {COMPETITIVE} AND {SHOT_STRETCH}")
# F3a penalty direction, all in seasons already in the fixture:
# 2022:152 swapped, with a bench penalty; 2027:2701274 stored right, with a
# bench penalty; 2025:231 mixed (one stray row) with a bench penalty;
# 2025:399 a penalised player listed on both rosters; 2022:35 a single bench
# penalty and nothing else (resolves by sufferer).
PENALTY_GAMES = ((2022, 152), (2027, 2701274), (2025, 231), (2025, 399), (2022, 35))
PENALTY_SLICE = (
    "((season, game_id) IN (VALUES " + ", ".join(f"({s}, {g})" for s, g in PENALTY_GAMES) + ") "
    f"OR {EVENT_GAMES})"
)
# F3c. Pelicans, Jukurit, Jokerit: the 2025 ice-time gap games.
GAP_TEAMS = ("875886777:pelicans", "292293444:jukurit", "238306801:jokerit")
SKATER_GAMES = games_where(
    f"ended = 1 AND phase IN {COMPETITIVE} AND (season = 2027 OR (season = 2025 AND "
    f"start_utc >= '2025-02-01' AND (home_team_id IN {GAP_TEAMS} OR away_team_id IN {GAP_TEAMS})))"
)
ROSTER_ROWS = (
    f"({PENALTY_SLICE} AND EXISTS (SELECT 1 FROM hockey.game_penalty_events p "
    "WHERE p.season = game_rosters.season AND p.game_id = game_rosters.game_id "
    "AND game_rosters.player_id IN (p.player_id, p.sufferer_player_id))) "
    f"OR ({SKATER_GAMES} AND EXISTS (SELECT 1 FROM hockey.game_player_period_stats p "
    "WHERE p.season = game_rosters.season AND p.game_id = game_rosters.game_id "
    "AND p.team_id = game_rosters.team_id AND p.player_id = game_rosters.player_id))"
)

# Each player's id and role: all lineups.py and availability_report read.
COMPACT_ROSTER = (
    "(SELECT json_group_array(json_object('id', json_extract(value, '$.id'), "
    "'role', json_extract(value, '$.role'))) FROM json_each(roster_json))"
)
BATCH_ROWS = 500

# (schema, table, columns, WHERE, ORDER BY). A column is a name, or
# (name, SQL expression) for a value the slice rewrites.
SLICES = (
    ("hockey", "games", (
        "game_id", "season", "phase", "start_utc", "home_team_id", "home_team_name", "home_goals",
        "away_team_id", "away_team_name", "away_goals", "home_expected_goals", "away_expected_goals",
        "game_time_seconds", "started", "ended", "finished_type",
    ), f"season IN {GAME_SEASONS}", "season, game_id"),
    ("snapshots", "odds_snapshots", (
        "id", "league", "book", "market", "fixture_ref", "captured_at", "home_odds", "draw_odds",
        "away_odds", "parsed", ("raw_payload", "''"), "season", "game_id", "start_utc",
    ), f"season = 2027 AND game_id IN {ODDS_GAMES}", "id"),
    ("hockey", "game_goalie_period_stats", (
        "game_id", "season", "team_id", "player_id", "period", "shots_on_goal", "saves", "goals_allowed",
        "time_on_ice_seconds",
    ), GOALIE_GAMES, "season, game_id, team_id, player_id, period"),
    ("hockey", "game_goalkeeper_events", (
        "game_id", "season", "team_id", "event_id", "player_id", "begin_time",
    ), GOALIE_GAMES, "season, game_id, team_id, event_id"),
    ("snapshots", "lineup_snapshots", (
        "id", "league", "season", "game_id", "team_role", "team_name", "captured_at", "source",
        "confirmed_player_count", "confirmed_goalie_count", ("roster_json", COMPACT_ROSTER),
        "starter_player_id", "parsed", ("raw_payload", "''"),
    ), "season = 2027 AND parsed = 1 AND starter_player_id IS NOT NULL", "id"),
    ("hockey", "shot_events", (
        "game_id", "season", "shooting_team_id", "event_type", "strength_type",
    ), SHOT_GAMES, "season, game_id, period, game_time_seconds, id"),
    ("hockey", "game_penalty_events", (
        "game_id", "season", "team_id", "event_id", "player_id", "sufferer_player_id", "period",
        "game_time_seconds", "penalty_begin_time", "penalty_end_time", "fault_type", "penalty_minutes",
    ), PENALTY_SLICE, "season, game_id, event_id"),
    ("hockey", "game_goal_events", (
        "game_id", "season", "team_id", "event_id", "period", "game_time_seconds", "goal_types",
    ), PENALTY_SLICE, "season, game_id, event_id"),
    ("hockey", "game_player_period_stats", (
        "game_id", "season", "team_id", "player_id", "period", "time_on_ice_seconds",
    ), SKATER_GAMES, "season, game_id, team_id, player_id, period"),
    ("hockey", "game_rosters", (
        "game_id", "season", "team_id", "player_id", "role", "role_code",
    ), ROSTER_ROWS, "season, game_id, team_id, player_id"),
)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", uri=True)  # uri=True lets ATTACH take ?mode=ro
    for schema, path in SOURCES.items():
        conn.execute("ATTACH DATABASE ? AS " + schema, (path.resolve().as_uri() + "?mode=ro",))
    return conn


def dump(conn: sqlite3.Connection, schema, table, columns, where, order_by) -> tuple[list[str], int]:
    """INSERT statements for one slice, and its row count."""
    names = [c if isinstance(c, str) else c[0] for c in columns]
    values = " || ', ' || ".join(f"quote({c if isinstance(c, str) else c[1]})" for c in columns)
    rows = [
        f"({row[0]})"
        for row in conn.execute(f"SELECT {values} FROM {schema}.{table} WHERE {where} ORDER BY {order_by}")
    ]
    # One row per line, in batches: a single statement is capped at 1 MB.
    header = f"INSERT INTO {schema}.{table} ({', '.join(names)}) VALUES"
    return [
        header + "\n" + ",\n".join(rows[i:i + BATCH_ROWS]) + ";"
        for i in range(0, len(rows), BATCH_ROWS)
    ], len(rows)


def main() -> None:
    lines = ["-- Generated by scripts/make_feature_fixture.py from real rows. Do not edit."]
    conn = connect()
    try:
        for schema, table, *spec in SLICES:
            statements, n = dump(conn, schema, table, *spec)
            lines.extend(statements)
            print(f"{schema}.{table}: {n} rows")
    finally:
        conn.close()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
