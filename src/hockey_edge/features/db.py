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
-- ratings_exclusion_reason is non-NULL only for the games listed in
-- spine.RATINGS_EXCLUSIONS, which have feeds_ratings = 0.
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
    ratings_exclusion_reason TEXT,
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

-- Derived per-game table (starters.py), read by features only through
-- history.history_before. One row per goalie per game in game_spine with
-- goalie stats, summed over all periods (OT included, shootout attempts
-- are not shots). whole_game = the only goalie of his team who faced a
-- shot (goalie ice time is missing for 2026 and part of 2025).
-- opponent_xg = the opponent's game xG, NULL unless both teams have one
-- (none before 2021). is_actual_starter is starters.derive_actual_starter.
CREATE TABLE game_goalies (
    game_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    team_id TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    shots_against INTEGER NOT NULL,
    goals_against INTEGER NOT NULL,
    time_on_ice_seconds INTEGER NOT NULL,
    is_actual_starter INTEGER NOT NULL,
    whole_game INTEGER NOT NULL,
    opponent_xg REAL,
    PRIMARY KEY (game_id, season, team_id, player_id)
);
CREATE INDEX idx_game_goalies_player ON game_goalies (player_id, season);
CREATE INDEX idx_game_goalies_team ON game_goalies (team_id, is_actual_starter, season);

-- One row per team per competitive game. actual_* is the post-game starter
-- (NULL for unplayed games, or when actual_reason is not 'ok');
-- snapshot_* is the latest lineup snapshot with a starter captured before
-- the start. resolved = snapshot, else actual, else NULL with source
-- 'unknown'. resolved_source is an audit column and evaluation slice, never
-- a model input.
CREATE TABLE game_starters (
    game_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    team_role TEXT NOT NULL CHECK (team_role IN ('home', 'away')),
    team_id TEXT NOT NULL,
    actual_player_id INTEGER,
    actual_reason TEXT NOT NULL,
    snapshot_player_id INTEGER,
    snapshot_captured_at TEXT,
    resolved_player_id INTEGER,
    resolved_source TEXT NOT NULL
        CHECK (resolved_source IN ('snapshot_inferred', 'actual_postgame', 'unknown')),
    PRIMARY KEY (game_id, season, team_role)
);

-- Family 1, one row per team per competitive game. Goalie history is the
-- goalie's earlier games that feed ratings, any team, this season and the
-- three before it.
-- league_sv: saves / shots over earlier rated games, this season and the
-- two before it. sv_short / sv_long: the goalie's raw save% over his most
-- recent games until shots_short / shots_long reach the window (NULL with no
-- shots). starter_sv: (saves_long + k * league_sv) / (shots_long + k).
-- expected_sv: the team's expected-goalie fallback, the start-weighted mean
-- of starter_sv over the goalies who started its last N games with a known
-- starter, this season or last (league_sv when there are none); computed
-- for every row. expected_starts_n is the number of those games.
-- goalie_sv = starter_sv, or expected_sv when starter_source is 'unknown'.
-- gsax_per_game: mean of (opponent_xg - goals_against) over the goalie's
-- last whole games with opponent xG; NULL when gsax_n = 0.
-- starter_source is an audit column, never a model input.
CREATE TABLE features_goalie (
    game_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    team_role TEXT NOT NULL CHECK (team_role IN ('home', 'away')),
    team_id TEXT NOT NULL,
    starter_player_id INTEGER,
    starter_source TEXT NOT NULL,
    league_sv REAL,
    shots_short INTEGER,
    sv_short REAL,
    shots_long INTEGER,
    sv_long REAL,
    starter_sv REAL,
    expected_starts_n INTEGER NOT NULL,
    expected_sv REAL,
    goalie_sv REAL,
    gsax_n INTEGER,
    gsax_per_game REAL,
    PRIMARY KEY (game_id, season, team_role)
);

-- Derived per-game table (team_shots.py), read by features only through
-- history.history_before. One row per team per ended game in game_spine.
-- corsi_* count every shot_events row of the team / its opponent, all four
-- result types (D4); ev_corsi_* only strength_type 'EvenStrengthShot'. All
-- four are NULL when the game has no shot_events at all (missing, not 0).
-- xg_for / xg_against are the game-level xG, NULL unless both teams have
-- one (none before 2021).
CREATE TABLE game_team_shots (
    game_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    team_id TEXT NOT NULL,
    opponent_id TEXT NOT NULL,
    corsi_for INTEGER,
    corsi_against INTEGER,
    ev_corsi_for INTEGER,
    ev_corsi_against INTEGER,
    xg_for REAL,
    xg_against REAL,
    PRIMARY KEY (game_id, season, team_id)
);
CREATE INDEX idx_game_team_shots_team ON game_team_shots (team_id, season);

-- Family 2, one row per team per competitive game. History is the team's
-- earlier games that feed ratings, this season and last. Windows _10, _25,
-- _40 take the team's most recent N such games that have shot_events
-- (games without are skipped, not counted as 0); corsi_n_N is how many
-- there were (< N early on). Rates are per game: corsi_for_N = attempts
-- for per game, corsi_share_N = for / (for + against) summed over the
-- window; ev_ = even strength only. corsi_n_season counts the window games
-- from this season. xg_*_N are per game over the most recent N games with
-- game xG (from 2021), xg_n_N their count; xg_available = 1 when there is
-- at least one. Everything is NULL when its window is empty.
CREATE TABLE features_shots (
    game_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    team_role TEXT NOT NULL CHECK (team_role IN ('home', 'away')),
    team_id TEXT NOT NULL,
    corsi_n_season INTEGER NOT NULL,
    corsi_n_10 INTEGER NOT NULL,
    corsi_for_10 REAL,
    corsi_against_10 REAL,
    corsi_share_10 REAL,
    ev_corsi_for_10 REAL,
    ev_corsi_against_10 REAL,
    ev_corsi_share_10 REAL,
    corsi_n_25 INTEGER NOT NULL,
    corsi_for_25 REAL,
    corsi_against_25 REAL,
    corsi_share_25 REAL,
    ev_corsi_for_25 REAL,
    ev_corsi_against_25 REAL,
    ev_corsi_share_25 REAL,
    corsi_n_40 INTEGER NOT NULL,
    corsi_for_40 REAL,
    corsi_against_40 REAL,
    corsi_share_40 REAL,
    ev_corsi_for_40 REAL,
    ev_corsi_against_40 REAL,
    ev_corsi_share_40 REAL,
    xg_available INTEGER NOT NULL,
    xg_n_10 INTEGER NOT NULL,
    xg_for_10 REAL,
    xg_against_10 REAL,
    xg_n_25 INTEGER NOT NULL,
    xg_for_25 REAL,
    xg_against_25 REAL,
    xg_n_40 INTEGER NOT NULL,
    xg_for_40 REAL,
    xg_against_40 REAL,
    PRIMARY KEY (game_id, season, team_role)
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
    "game_goalies",
    "game_starters",
    "features_goalie",
    "game_team_shots",
    "features_shots",
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
