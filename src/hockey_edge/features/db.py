"""DDL and connection for data/features.db.

The two source databases are ATTACHed read-only as `hockey` and `snapshots`,
so a bug in the feature build cannot write to them. Every table here is
derived and is rebuilt from scratch on each build (see build.py).
"""

import sqlite3
from pathlib import Path

from hockey_edge.ingest.db import DB_PATH as HOCKEY_DB_PATH
from hockey_edge.snapshot.storage import DB_PATH as SNAPSHOTS_DB_PATH

FEATURES_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "features.db"

SCHEMA = """
-- One row per game in hockey.games, every phase, played or not. Goals, end
-- type and regulation_result are NULL until the game has ended. start_date
-- is the UTC date of start_utc; history.py cuts on it.
CREATE TABLE game_spine (
    game_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    start_utc TEXT NOT NULL,
    start_date TEXT NOT NULL,
    phase TEXT NOT NULL,
    home_team_id TEXT NOT NULL,
    home_team_name TEXT NOT NULL,
    away_team_id TEXT NOT NULL,
    away_team_name TEXT NOT NULL,
    ended INTEGER NOT NULL,
    home_goals INTEGER,
    away_goals INTEGER,
    end_type TEXT CHECK (end_type IN ('REG', 'OT', 'SO')),
    regulation_result TEXT CHECK (regulation_result IN ('H', 'D', 'A')),
    end_type_anomaly INTEGER NOT NULL,
    is_liiga_competitive INTEGER NOT NULL,
    has_non_liiga_team INTEGER NOT NULL,
    feeds_ratings INTEGER NOT NULL,
    in_eval_sample INTEGER NOT NULL,
    PRIMARY KEY (game_id, season)
);
CREATE INDEX idx_game_spine_date ON game_spine (start_date);
CREATE INDEX idx_game_spine_season_date ON game_spine (season, start_date);

-- Curated in season_format.py and checked against hockey.games on every
-- build. runkosarja_* and playoff_teams are counted from the games.
CREATE TABLE season_format (
    season INTEGER PRIMARY KEY,
    n_teams INTEGER NOT NULL,
    games_per_team INTEGER NOT NULL,
    has_playoffs INTEGER NOT NULL,
    relegation TEXT,
    runkosarja_games INTEGER NOT NULL,
    runkosarja_ended INTEGER NOT NULL,
    playoff_teams INTEGER
);

-- Regular-season games per team, counted. games_played is as of the build,
-- not point-in-time: it describes the season, it is not a feature.
CREATE TABLE team_season (
    season INTEGER NOT NULL,
    team_id TEXT NOT NULL,
    team_name TEXT NOT NULL,
    games_scheduled INTEGER NOT NULL,
    games_played INTEGER NOT NULL,
    is_first_liiga_season INTEGER NOT NULL,
    PRIMARY KEY (season, team_id)
);

-- Family 5. Rates over earlier games that feed ratings, this season and the
-- two before it. Goal difference is regulation (0 for an OT/SO game).
-- team_ha_raw = (home team's goal diff per home game - per away game) / 2;
-- team_ha_offset = (team_ha_raw - league_home_goal_diff) shrunk by
-- n / (n + k), 0 when the team has no home or no away games yet.
CREATE TABLE features_home (
    game_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    league_n INTEGER NOT NULL,
    league_home_win_rate REAL,
    league_draw_rate REAL,
    league_away_win_rate REAL,
    league_home_goal_diff REAL,
    team_home_n INTEGER NOT NULL,
    team_away_n INTEGER NOT NULL,
    team_ha_raw REAL,
    team_ha_offset REAL NOT NULL,
    PRIMARY KEY (game_id, season)
);

-- Family 4, Liiga subset. rest_days = days since the team's previous
-- competitive game this season (1 = back-to-back), NULL for its first.
-- games_last_6d counts competitive games in the 6 days before game day.
CREATE TABLE features_schedule (
    game_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    home_rest_days INTEGER,
    away_rest_days INTEGER,
    home_games_last_6d INTEGER NOT NULL,
    away_games_last_6d INTEGER NOT NULL,
    PRIMARY KEY (game_id, season)
);

-- NOT a feature table. Evaluation benchmark only: the last parsed 1X2 row
-- captured before start_utc, Pinnacle if it has one, else bet365.
CREATE TABLE benchmark_odds (
    game_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    book TEXT NOT NULL,
    is_fallback INTEGER NOT NULL,
    odds_snapshot_id INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    minutes_before_start REAL NOT NULL,
    home_odds REAL NOT NULL,
    draw_odds REAL NOT NULL,
    away_odds REAL NOT NULL,
    overround REAL NOT NULL,
    p_home REAL NOT NULL,
    p_draw REAL NOT NULL,
    p_away REAL NOT NULL,
    vig_method TEXT NOT NULL,
    PRIMARY KEY (game_id, season)
);

CREATE TABLE build_meta (
    built_at TEXT NOT NULL,
    code_version TEXT NOT NULL,
    hockey_max_fetched_at TEXT,
    snapshots_max_captured_at TEXT,
    row_counts TEXT NOT NULL
);
"""

TABLES = (
    "game_spine",
    "season_format",
    "team_season",
    "features_home",
    "features_schedule",
    "benchmark_odds",
)


def connect(
    features_path: Path = FEATURES_DB_PATH,
    hockey_path: Path = HOCKEY_DB_PATH,
    snapshots_path: Path = SNAPSHOTS_DB_PATH,
) -> sqlite3.Connection:
    """Open features_path with both sources attached read-only."""
    for source in (hockey_path, snapshots_path):
        if not Path(source).exists():
            raise FileNotFoundError(f"source database not found: {source}")
    conn = sqlite3.connect(Path(features_path).resolve().as_uri(), uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS hockey", (Path(hockey_path).resolve().as_uri() + "?mode=ro",))
    conn.execute("ATTACH DATABASE ? AS snapshots", (Path(snapshots_path).resolve().as_uri() + "?mode=ro",))
    return conn
