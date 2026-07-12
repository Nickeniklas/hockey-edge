"""DDL for the Layer 1 (historical ingest) SQLite database.

Schema is documented with rationale in docs/SCHEMA_DRAFT.md — that doc is the
source of truth for *why* each table looks the way it does; this module is
just the executable DDL. Lives in data/hockey.db, separate from
data/snapshots.db (the Layer 2 snapshot job owns that file).

Raw JSON response bodies are cached as files under data/raw/liiga/ (gitignored,
see raw_cache.py); this database stores metadata (raw_responses, sync_state)
plus the curated tables parsed from those files. The raw layer is strictly
append-only; curated tables are derived and may be deleted-and-reinserted per
entity when re-parsing — see docs/SCHEMA_DRAFT.md's "Design principles".
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "hockey.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sync_state (
    id INTEGER PRIMARY KEY,
    league TEXT NOT NULL DEFAULT 'liiga',
    endpoint TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    season INTEGER,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed_retryable', 'failed_permanent')),
    http_status INTEGER,
    content_hash TEXT,
    fetched_at TEXT NOT NULL,
    error TEXT,
    UNIQUE (league, endpoint, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_sync_state_season ON sync_state (league, endpoint, season);

CREATE TABLE IF NOT EXISTS raw_responses (
    id INTEGER PRIMARY KEY,
    league TEXT NOT NULL DEFAULT 'liiga',
    endpoint TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    season INTEGER,
    url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    file_path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_responses_lookup ON raw_responses (league, endpoint, entity_id);

CREATE TABLE IF NOT EXISTS games (
    game_id INTEGER PRIMARY KEY,
    season INTEGER NOT NULL,
    phase TEXT NOT NULL,
    start_utc TEXT NOT NULL,
    end_utc TEXT,
    home_team_id TEXT NOT NULL,
    home_team_name TEXT NOT NULL,
    home_goals INTEGER,
    away_team_id TEXT NOT NULL,
    away_team_name TEXT NOT NULL,
    away_goals INTEGER,
    home_expected_goals REAL,
    away_expected_goals REAL,
    game_time_seconds INTEGER,
    started INTEGER NOT NULL,
    ended INTEGER NOT NULL,
    finished_type TEXT,
    spectators INTEGER,
    game_week INTEGER,
    play_off_pair INTEGER,
    play_off_phase INTEGER,
    play_off_req_wins INTEGER,
    rink_name TEXT,
    rink_city TEXT,
    source_raw_response_id INTEGER REFERENCES raw_responses(id)
);
CREATE INDEX IF NOT EXISTS idx_games_season_phase ON games (season, phase);
CREATE INDEX IF NOT EXISTS idx_games_teams ON games (home_team_id, away_team_id);

CREATE TABLE IF NOT EXISTS game_goal_events (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    team_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    scorer_player_id INTEGER,
    scorer_first_name TEXT,
    scorer_last_name TEXT,
    period INTEGER,
    game_time_seconds INTEGER,
    log_time_utc TEXT,
    goal_types TEXT,
    assistant_player_ids TEXT,
    home_score_after INTEGER,
    away_score_after INTEGER,
    winning_goal INTEGER,
    UNIQUE (game_id, team_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_goal_events_game ON game_goal_events (game_id);

CREATE TABLE IF NOT EXISTS game_penalty_events (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    team_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    player_id INTEGER,
    sufferer_player_id INTEGER,
    period INTEGER,
    game_time_seconds INTEGER,
    penalty_begin_time INTEGER,
    penalty_end_time INTEGER,
    fault_name TEXT,
    fault_type TEXT,
    penalty_minutes INTEGER,
    UNIQUE (game_id, team_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_penalty_events_game ON game_penalty_events (game_id);

CREATE TABLE IF NOT EXISTS game_goalkeeper_events (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    team_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    period INTEGER,
    game_time_seconds INTEGER,
    begin_time INTEGER,
    end_time INTEGER,
    empty_net INTEGER,
    UNIQUE (game_id, team_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_goalkeeper_events_game ON game_goalkeeper_events (game_id);

CREATE TABLE IF NOT EXISTS game_rosters (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    team_id TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    role TEXT,
    role_code TEXT,
    jersey INTEGER,
    captain INTEGER,
    alternate_captain INTEGER,
    rookie INTEGER,
    injured INTEGER,
    suspended INTEGER,
    removed INTEGER,
    UNIQUE (game_id, team_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_game_rosters_game ON game_rosters (game_id);
CREATE INDEX IF NOT EXISTS idx_game_rosters_player ON game_rosters (player_id);

CREATE TABLE IF NOT EXISTS players (
    player_id INTEGER PRIMARY KEY,
    first_name TEXT,
    last_name TEXT,
    handedness TEXT,
    height INTEGER,
    weight INTEGER,
    country_of_birth TEXT,
    nationality TEXT,
    date_of_birth TEXT,
    last_seen_team_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_team_period_stats (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    team_id TEXT NOT NULL,
    period INTEGER NOT NULL,
    goals INTEGER,
    shots INTEGER,
    powerplay_instances INTEGER,
    powerplay_goals INTEGER,
    shorthanded_instances INTEGER,
    shorthanded_goals_against INTEGER,
    penalty_minutes INTEGER,
    face_off_wins INTEGER,
    total_distance_travelled REAL,
    UNIQUE (game_id, team_id, period)
);
CREATE INDEX IF NOT EXISTS idx_team_period_stats_game ON game_team_period_stats (game_id);

CREATE TABLE IF NOT EXISTS game_player_period_stats (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    team_id TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    jersey_id INTEGER,
    period INTEGER NOT NULL,
    goals INTEGER,
    assists INTEGER,
    points INTEGER,
    plusminus INTEGER,
    shots INTEGER,
    penalty_minutes INTEGER,
    powerplay_goals INTEGER,
    shorthanded_goals INTEGER,
    blocked_shots INTEGER,
    faceoffs_total INTEGER,
    faceoffs_won INTEGER,
    corsi_for INTEGER,
    corsi_against INTEGER,
    time_on_ice_seconds INTEGER,
    distance REAL,
    expected_goals_player REAL,
    expected_goals_against REAL,
    UNIQUE (game_id, team_id, player_id, period)
);
CREATE INDEX IF NOT EXISTS idx_player_period_stats_game ON game_player_period_stats (game_id);

CREATE TABLE IF NOT EXISTS game_goalie_period_stats (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    team_id TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    jersey_id INTEGER,
    period INTEGER NOT NULL,
    shots_on_goal INTEGER,
    saves INTEGER,
    goals_allowed INTEGER,
    save_percentage TEXT,
    time_on_ice_seconds INTEGER,
    UNIQUE (game_id, team_id, player_id, period)
);
CREATE INDEX IF NOT EXISTS idx_goalie_period_stats_game ON game_goalie_period_stats (game_id);

CREATE TABLE IF NOT EXISTS game_puck_control (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    period INTEGER NOT NULL,
    home_control_seconds REAL,
    away_control_seconds REAL,
    contested_control_seconds REAL,
    UNIQUE (game_id, period)
);

CREATE TABLE IF NOT EXISTS shot_events (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    period INTEGER,
    game_time_seconds INTEGER,
    shooting_team_id TEXT,
    shooter_player_id INTEGER,
    blocker_player_id INTEGER,
    shot_x INTEGER,
    shot_y INTEGER,
    event_type TEXT,
    strength_type TEXT,
    own_team_players_on_ice INTEGER,
    other_team_players_on_ice INTEGER
);
CREATE INDEX IF NOT EXISTS idx_shot_events_game ON shot_events (game_id);

CREATE TABLE IF NOT EXISTS standings (
    id INTEGER PRIMARY KEY,
    season INTEGER NOT NULL,
    phase TEXT NOT NULL,
    team_id TEXT NOT NULL,
    team_name TEXT NOT NULL,
    ranking INTEGER,
    games INTEGER,
    wins INTEGER,
    losses INTEGER,
    ties INTEGER,
    overtime_wins INTEGER,
    overtime_losses INTEGER,
    points INTEGER,
    goals INTEGER,
    goals_against INTEGER,
    powerplay_instances INTEGER,
    powerplay_goals INTEGER,
    powerplay_percentage REAL,
    shorthanded_instances INTEGER,
    shorthanded_goals_against INTEGER,
    shorthanded_percentage REAL,
    penalty_minutes INTEGER,
    distance REAL,
    points_per_game REAL,
    win_percentage REAL,
    UNIQUE (season, phase, team_id)
);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn
