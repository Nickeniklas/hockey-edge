"""Write fixtures/features/mini.sql: a slice of real rows for the feature tests.

    python scripts/make_feature_fixture.py

Reads data/hockey.db and data/snapshots.db read-only and writes INSERT
statements only (tests create the tables from the real DDL). The slice is
what phase F1 reads; later phases add their tables to SLICES.

- hockey.games: seasons 2022, 2025 and 2027 in full. 2022 has the tied
  regular-time playoff game 49294; 2025 has the PLAYOUT and QUALIFICATIONS
  series, K-Espoo's first season and foreign friendlies; 2027 has Jokerit,
  PITSITURNAUS, unplayed games and the games with odds.
- snapshots.odds_snapshots: every row for four 2027 games. raw_payload is
  blanked (about 18 KB per row); nothing in the feature store reads it.
- F2a: hockey.game_goalie_period_stats and hockey.game_goalkeeper_events for
  the competitive games of 2025 and 2027 (goalie windows, actual starters),
  and every snapshots.lineup_snapshots row of 2027 with raw_payload and
  roster_json blanked (the resolver reads only the starter columns).
- F2b: hockey.shot_events for the competitive games of 2027 and of 2025 from
  2025-01-22 to 2025-02-23, which runs into the start of the 2025 shot_events
  gap (game 393 on). Only the columns Corsi reads are kept; the rest are left
  out of the INSERT and load as NULL (about 6 MB otherwise).
- F3a/F3b: hockey.game_penalty_events and hockey.game_goal_events for five
  games picked per case of the penalty-team correction (PENALTY_GAMES) and
  for the ended competitive games of 2027 and of 2025 from 2025-01-22 to
  2025-02-23 (EVENT_GAMES, the same 2025 stretch as shot_events). Rosters
  for the same games, but only the rows of players a penalty names (the
  penalised player or the sufferer): that is all the penalty-team rule
  reads. Text and id columns nothing reads are left out.
- F3c: hockey.game_player_period_stats (keys and ice time only) for the
  ended competitive games of 2027 and the 2025 games from 2025-02-01 of
  Pelicans, Jukurit and Jokerit (SKATER_GAMES: their regular-season run-in,
  then the ice-time gap of the PLAYOUT and QUALIFICATIONS series), with
  the game_rosters rows of those skaters (role kept: it gives pos), and the
  2027 lineup snapshots' roster_json cut to each player's id, role and line.

`--append-f3c` appends only the F3c slices to the existing file, as INSERT
OR REPLACE where rows are already there (the roster rows of penalty-named
players without role, the 2027 snapshots without roster_json), cut to what
the file already holds (FIXTURE_FROZEN). The full regeneration before step
6 writes everything from SLICES; APPEND_F3C can go then.

Season 2027 is live, so regenerating this changes the file. The tests pin
individual games, not totals.
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "fixtures" / "features" / "mini.sql"

GAME_SEASONS = (2022, 2025, 2027)
# 2701309: both books, many polls. 2701319: rows captured after the start.
# 2701332: Pinnacle inactive on the closing poll. 2701368: Pinnacle only.
ODDS_GAMES = (2701309, 2701319, 2701332, 2701368)
GOALIE_SEASONS = (2025, 2027)
COMPETITIVE = "('RUNKOSARJA', 'PLAYOFFS', 'PLAYOUT', 'QUALIFICATIONS')"
COMPETITIVE_GAMES = (
    f"season IN {GOALIE_SEASONS} AND (game_id, season) IN "
    f"(SELECT game_id, season FROM games WHERE phase IN {COMPETITIVE})"
)
SHOT_GAMES = (
    "(game_id, season) IN (SELECT game_id, season FROM games "
    f"WHERE phase IN {COMPETITIVE} AND (season = 2027 OR "
    "(season = 2025 AND start_utc >= '2025-01-22' AND start_utc < '2025-02-24')))"
)
SHOT_COLUMNS_DROPPED = (
    "id", "period", "game_time_seconds", "shooter_player_id", "blocker_player_id", "shot_x",
    "shot_y", "own_team_players_on_ice", "other_team_players_on_ice",
)
# F3a penalty direction, all in seasons already in the fixture:
# 2022:152 swapped, with a bench penalty; 2027:2701274 stored right, with a
# bench penalty; 2025:231 mixed (one stray row) with a bench penalty;
# 2025:399 a penalised player listed on both rosters; 2022:35 a single bench
# penalty and nothing else, so unresolved.
PENALTY_GAMES = ((2022, 152), (2027, 2701274), (2025, 231), (2025, 399), (2022, 35))
PENALTY_WHERE = (
    "(season, game_id) IN (VALUES " + ", ".join(f"({s}, {g})" for s, g in PENALTY_GAMES) + ")"
)
EVENT_GAMES = (
    "(game_id, season) IN (SELECT game_id, season FROM games "
    f"WHERE ended = 1 AND phase IN {COMPETITIVE} AND (season = 2027 OR "
    "(season = 2025 AND start_utc >= '2025-01-22' AND start_utc < '2025-02-24')))"
)
PENALTY_SLICE = f"({PENALTY_WHERE} OR {EVENT_GAMES})"
ROSTER_SLICE = (
    f"{PENALTY_SLICE} AND EXISTS (SELECT 1 FROM game_penalty_events p "
    "WHERE p.season = game_rosters.season AND p.game_id = game_rosters.game_id "
    "AND game_rosters.player_id IN (p.player_id, p.sufferer_player_id))"
)
ROSTER_COLUMNS_DROPPED = (
    "id", "role", "role_code", "jersey", "captain", "alternate_captain", "rookie", "injured",
    "suspended", "removed",
)
GOAL_COLUMNS_DROPPED = (
    "id", "scorer_first_name", "scorer_last_name", "log_time_utc", "assistant_player_ids",
)
# F3c. Pelicans, Jukurit, Jokerit: the 2025 ice-time gap games.
GAP_TEAMS = ("875886777:pelicans", "292293444:jukurit", "238306801:jokerit")
SKATER_GAMES = (
    "(game_id, season) IN (SELECT game_id, season FROM games "
    f"WHERE ended = 1 AND phase IN {COMPETITIVE} AND (season = 2027 OR "
    "(season = 2025 AND start_utc >= '2025-02-01' AND "
    f"(home_team_id IN {GAP_TEAMS} OR away_team_id IN {GAP_TEAMS}))))"
)
SKATER_COLUMNS_DROPPED = (
    "id", "jersey_id", "goals", "assists", "points", "plusminus", "shots", "penalty_minutes",
    "powerplay_goals", "shorthanded_goals", "blocked_shots", "faceoffs_total", "faceoffs_won",
    "corsi_for", "corsi_against", "distance", "expected_goals_player", "expected_goals_against",
)
SKATER_ROSTER = (
    f"{SKATER_GAMES} AND EXISTS (SELECT 1 FROM game_player_period_stats p "
    "WHERE p.season = game_rosters.season AND p.game_id = game_rosters.game_id "
    "AND p.team_id = game_rosters.team_id AND p.player_id = game_rosters.player_id)"
)
# role gives a skater's pos (lineups.py); the rest still goes.
ROSTER_COLUMNS_DROPPED_F3C = tuple(c for c in ROSTER_COLUMNS_DROPPED if c != "role")


class Expr(str):
    """A column override that is an SQL expression, not a literal."""


# Each player's id, role and line: all lineups.py reads.
COMPACT_ROSTER = Expr(
    "(SELECT json_group_array(json_object('id', json_extract(value, '$.id'), "
    "'role', json_extract(value, '$.role'), 'line', json_extract(value, '$.line'))) "
    "FROM json_each(roster_json))"
)
# What the file held before F3c: games up to the 2026-11-01 slate and the
# snapshots captured until then (ids 1-772).
FIXTURE_FROZEN = "2026-11-02"
BATCH_ROWS = 500

# (schema, table, source db, column overrides, WHERE, ORDER BY). An override of
# None leaves the column out of the INSERT, so it loads as its default.
SLICES = (
    ("hockey", "games", "hockey.db", {"source_raw_response_id": "NULL"},
     f"season IN {GAME_SEASONS}", "season, game_id"),
    ("snapshots", "odds_snapshots", "snapshots.db", {"raw_payload": "''"},
     f"season = 2027 AND game_id IN {ODDS_GAMES}", "id"),
    ("hockey", "game_goalie_period_stats", "hockey.db", {"jersey_id": "NULL", "save_percentage": "NULL"},
     COMPETITIVE_GAMES, "id"),
    ("hockey", "game_goalkeeper_events", "hockey.db", {}, COMPETITIVE_GAMES, "id"),
    ("snapshots", "lineup_snapshots", "snapshots.db", {"raw_payload": "''", "roster_json": COMPACT_ROSTER},
     "season = 2027", "id"),
    ("hockey", "shot_events", "hockey.db", dict.fromkeys(SHOT_COLUMNS_DROPPED),
     SHOT_GAMES, "id"),
    ("hockey", "game_penalty_events", "hockey.db", dict.fromkeys(("id", "fault_name")),
     PENALTY_SLICE, "season, game_id, event_id"),
    ("hockey", "game_rosters", "hockey.db", dict.fromkeys(ROSTER_COLUMNS_DROPPED_F3C),
     f"({ROSTER_SLICE}) OR ({SKATER_ROSTER})", "season, game_id, team_id, player_id"),
    ("hockey", "game_goal_events", "hockey.db", dict.fromkeys(GOAL_COLUMNS_DROPPED),
     PENALTY_SLICE, "season, game_id, event_id"),
    ("hockey", "game_player_period_stats", "hockey.db", dict.fromkeys(SKATER_COLUMNS_DROPPED),
     SKATER_GAMES, "id"),
)

# `--append-f3c`: (verb, slice), cut to what the file already holds.
_FROZEN_GAMES = (
    f"(game_id, season) IN (SELECT game_id, season FROM games WHERE start_utc < '{FIXTURE_FROZEN}')"
)
APPEND_F3C = (
    ("INSERT", ("hockey", "game_player_period_stats", "hockey.db", dict.fromkeys(SKATER_COLUMNS_DROPPED),
                f"{SKATER_GAMES} AND {_FROZEN_GAMES}", "id")),
    ("INSERT OR REPLACE", ("hockey", "game_rosters", "hockey.db", dict.fromkeys(ROSTER_COLUMNS_DROPPED_F3C),
                           f"{SKATER_ROSTER} AND {_FROZEN_GAMES}", "season, game_id, team_id, player_id")),
    ("INSERT OR REPLACE", ("snapshots", "lineup_snapshots", "snapshots.db",
                           {"raw_payload": "''", "roster_json": COMPACT_ROSTER},
                           f"season = 2027 AND captured_at < '{FIXTURE_FROZEN}'", "id")),
)


def _value(column: str, overrides: dict) -> str:
    if column not in overrides:
        return f"quote({column})"
    if isinstance(overrides[column], Expr):
        return f"quote({overrides[column]})"
    return "'" + overrides[column].replace("'", "''") + "'"


def dump(schema, table, db_name, overrides, where, order_by, verb="INSERT") -> list[str]:
    """INSERT statements for one slice."""
    conn = sqlite3.connect((ROOT / "data" / db_name).as_uri() + "?mode=ro", uri=True)
    try:
        columns = [
            row[1] for row in conn.execute(f"PRAGMA table_info({table})")
            if not (row[1] in overrides and overrides[row[1]] is None)
        ]
        values = " || ', ' || ".join(_value(c, overrides) for c in columns)
        rows = [
            f"({row[0]})"
            for row in conn.execute(f"SELECT {values} FROM {table} WHERE {where} ORDER BY {order_by}")
        ]
    finally:
        conn.close()
    # One row per line, in batches: a single statement is capped at 1 MB.
    header = f"{verb} INTO {schema}.{table} ({', '.join(columns)}) VALUES"
    return [
        header + "\n" + ",\n".join(rows[i:i + BATCH_ROWS]) + ";"
        for i in range(0, len(rows), BATCH_ROWS)
    ]


def _statements(spec: tuple, verb: str = "INSERT") -> list[str]:
    statements = dump(*spec, verb=verb)
    print(f"{spec[0]}.{spec[1]} ({verb}): {sum(s.count(chr(10)) for s in statements)} rows")
    return statements


def main() -> None:
    if sys.argv[1:] == ["--append-f3c"]:
        lines = ["-- F3c slices, appended by scripts/make_feature_fixture.py --append-f3c."]
        for verb, spec in APPEND_F3C:
            lines.extend(_statements(spec, verb))
        with OUT_PATH.open("a", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        print(f"appended to {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.0f} KB)")
        return
    lines = ["-- Generated by scripts/make_feature_fixture.py from real rows. Do not edit."]
    for spec in SLICES:
        lines.extend(_statements(spec))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
