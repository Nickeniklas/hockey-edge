# SCHEMA_DRAFT — Liiga SQLite schema (draft, from real fixtures)

Derived from the real (trimmed) fixture payloads in `fixtures/liiga/` and the
endpoint catalog in `src/hockey_edge/ingest/liiga/endpoints.py`, not from
assumptions about what the API "should" return. Where a fixture forced a
deviation from `docs/DATA_PIPELINE.md`'s guideline shapes, it's called out
below with the reason. Implemented as DDL in `src/hockey_edge/ingest/db.py`.
Lives in `data/hockey.db` — separate from `data/snapshots.db` (snapshot job
owns that file; this schema is backfill/sync only, per the build-order split
between Layer 1 and Layer 2).

## Design principles

1. **Raw layer is strictly append-only.** `raw_responses` rows and the JSON
   files under `data/raw/liiga/` are never overwritten or deleted, per the
   global append-only-raw-data rule.
2. **Curated tables are derived and rebuildable.** Everything below
   `raw_responses` (games, events, rosters, stats, standings) is parsed from
   the cached raw JSON and can be safely deleted-and-reinserted per entity
   when re-parsing. This is what "corrections live in derived layers" means
   in practice: if a parser bug is fixed, drop and rebuild the affected rows
   from the untouched raw cache — never patch a raw file or hand-edit a
   curated row.
3. **Per-game season phase is a flagged column**, not inferred at query
   time — `games.phase`, sourced directly from the `serie` field games
   already carry. **Confirmed values differ from the naive assumption**: a
   season=2024 smoke-test backfill across all five `tournament` query values
   found `serie` is `RUNKOSARJA` (regular), `PLAYOFFS`, or **`PRACTICE`**
   (not `VALMISTAVAT_OTTELUT` as the tournament *query param* value would
   suggest — `tournament=valmistavat_ottelut` games come back with
   `serie="PRACTICE"`). `standings`, confusingly, keys its preseason phase
   dict as lowercase `valmistavat_ottelut` — the two endpoints use different
   vocabulary for the same phase. `tournament=playout` and
   `tournament=qualifications` returned zero games for season=2024, so their
   `serie` values are still unconfirmed; don't assume they'll say `PLAYOUT`/
   `QUALIFICATIONS` until a season that actually has games in those phases
   is backfilled. Liiga's playoff format changed in 2024-25 and has varied
   across seasons before that — never assume a fixed bracket shape, just
   carry what the API says per game.
4. **Recent-seasons-only endpoints degrade gracefully.** `game_stats` and
   `shotmap` 500 ("Remote server error") for old game_ids — confirmed on
   season=2010, working on season=2024. `sync_state` records the failure
   per `(endpoint, game_id)` as a terminal "unavailable" status, not a
   retryable error, so backfill doesn't loop on it forever. The
   corresponding curated tables (`game_team_period_stats`,
   `game_player_period_stats`, `game_goalie_period_stats`,
   `game_puck_control`, `shot_events`) simply have no rows for those
   game_ids — no sentinel/placeholder rows.
5. **IDs are carried as the API gives them.** `teamId` is the API's
   composite string (e.g. `"624554857:lukko"`) — used verbatim as the team
   key throughout rather than split into numeric id + slug, since some
   payloads (goal events, shot events, standings) mix numeric-only team ids
   with the composite form and splitting would need a lookup that doesn't
   always resolve. `players` is keyed by the API's numeric `playerId`
   directly (stable across `games_by_season`, `game_detail`, and
   `game_stats` in every fixture checked).

## sync_state

Tracks fetch status per `(league, endpoint, entity_id)` so re-running
backfill only fetches what's missing or stale. `entity_id` is season-scoped
for season-level endpoints (`games_by_season`, `standings`) and game-scoped
for per-game endpoints (`game_detail`, `game_stats`, `shotmap`).

```sql
CREATE TABLE sync_state (
    id INTEGER PRIMARY KEY,
    league TEXT NOT NULL DEFAULT 'liiga',
    endpoint TEXT NOT NULL, -- Endpoint.name from endpoints.py
    entity_id TEXT NOT NULL, -- season, or game_id, as string
    season INTEGER, -- redundant but indexed for backfill queries
    status TEXT NOT NULL CHECK (status IN ('success', 'failed_retryable', 'failed_permanent')),
    http_status INTEGER,
    content_hash TEXT, -- sha256 of response body; unchanged = skip reparse
    fetched_at TEXT NOT NULL, -- UTC ISO8601
    error TEXT,
    UNIQUE (league, endpoint, entity_id)
);
CREATE INDEX idx_sync_state_season ON sync_state (league, endpoint, season);
```

`failed_permanent` is used for the recent-seasons-only 500s (point 4 above)
so a resumable re-run doesn't keep retrying game_stats/shotmap on old games.
`failed_retryable` is for transient errors (timeouts, rate-limit 429s) —
those get retried on the next sync run.

## raw_responses

Metadata only; response bodies live on disk. Deviates slightly from
`docs/DATA_PIPELINE.md`'s literal `<hash>.json` naming: filenames are
`<entity_id>__<hash8>.json` (season or game_id, plus an 8-char content-hash
suffix) rather than hash-only, because entity-scoped filenames make
`data/raw/liiga/<endpoint>/` debuggable by hand during development, while the
hash suffix still gives append-only behavior — if a re-fetch's content
differs from what's cached, it's written as a new file rather than
overwriting, and `content_hash` in this table is what `sync_state` compares
against to decide whether reparse is needed.

```sql
CREATE TABLE raw_responses (
    id INTEGER PRIMARY KEY,
    league TEXT NOT NULL DEFAULT 'liiga',
    endpoint TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    season INTEGER,
    url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    file_path TEXT NOT NULL -- relative to repo root
);
CREATE INDEX idx_raw_responses_lookup ON raw_responses (league, endpoint, entity_id);
```

## games

Primary source: `games_by_season` (one call per season, embeds goal events —
cheaper than fetching `game_detail` per game just for the box score). Fields
below are what's actually present across the season=1976/2010/2024 fixtures;
several fields (`end`, `iceRink`) are present on some sampled games and
absent on others within the *same* endpoint — fixtures are trimmed real
responses, so absence in a fixture may mean "was null and got trimmed," not
"field never exists." All optional columns are nullable; nothing here
assumes a field is universally present.

```sql
CREATE TABLE games (
    game_id INTEGER PRIMARY KEY, -- API's top-level "id"
    season INTEGER NOT NULL,
    phase TEXT NOT NULL, -- from "serie": confirmed RUNKOSARJA/PLAYOFFS/PRACTICE so far; PLAYOUT/QUALIFICATIONS unconfirmed (see design principle 3)
    start_utc TEXT NOT NULL,
    end_utc TEXT,
    home_team_id TEXT NOT NULL,
    home_team_name TEXT NOT NULL,
    home_goals INTEGER,
    away_team_id TEXT NOT NULL,
    away_team_name TEXT NOT NULL,
    away_goals INTEGER,
    home_expected_goals REAL, -- absent pre-~2015 seasons; NULL, don't backfill-fail
    away_expected_goals REAL,
    game_time_seconds INTEGER,
    started INTEGER NOT NULL, -- 0/1
    ended INTEGER NOT NULL,
    finished_type TEXT, -- e.g. ENDED_DURING_REGULAR_GAME_TIME / ..._EXTENDED_GAME_TIME
    spectators INTEGER,
    game_week INTEGER,
    play_off_pair INTEGER,
    play_off_phase INTEGER,
    play_off_req_wins INTEGER,
    rink_name TEXT,
    rink_city TEXT,
    source_raw_response_id INTEGER REFERENCES raw_responses(id)
);
CREATE INDEX idx_games_season_phase ON games (season, phase);
CREATE INDEX idx_games_teams ON games (home_team_id, away_team_id);
```

**Rebuild caveat found during the smoke test:** design principle 2 above says
curated tables are "deleted-and-reinserted per entity" — for every table
*except* `games` itself, that's exactly what the parser does. `games` is the
one exception: it's upserted in place (`INSERT ... ON CONFLICT (game_id) DO
UPDATE`), never deleted, because seven other tables (`game_rosters`,
`game_team_period_stats`, `game_player_period_stats`,
`game_goalie_period_stats`, `game_puck_control`, `shot_events`, plus the
penalty/goalkeeper event tables) hold a `REFERENCES games(game_id)` written
by *other* endpoints' parsers. A blanket `DELETE FROM games WHERE season = ?`
before reinserting (the first version of this parser) throws a foreign-key
constraint error the moment any of those other tables already has rows for
that season — confirmed by re-running the season=2024 smoke test a second
time. `game_goal_events` is still safely delete-and-reinsert-scoped-to-its-
game_ids, since `games_by_season` is its only source.

## game_goal_events

Source: `games_by_season` (`homeTeam.goalEvents` / `awayTeam.goalEvents`) —
absent entirely (not just empty) on old seasons (confirmed absent on the
season=2010 fixture; present on season=2024). `assistantPlayerIds` is stored
as a JSON text array rather than a join table — Liiga goals have 0-2
assists and the array is never queried independently of its parent goal, so
a normalized assists table would be pure overhead for this access pattern.

```sql
CREATE TABLE game_goal_events (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    team_id TEXT NOT NULL, -- home or away team_id for this goal
    event_id INTEGER NOT NULL, -- API's per-game eventId
    scorer_player_id INTEGER,
    scorer_first_name TEXT,
    scorer_last_name TEXT,
    period INTEGER,
    game_time_seconds INTEGER,
    log_time_utc TEXT,
    goal_types TEXT, -- JSON array, e.g. ["TV"], ["YV"] (PP), ["AV"] (SH)
    assistant_player_ids TEXT, -- JSON array of ints, possibly empty
    home_score_after INTEGER,
    away_score_after INTEGER,
    winning_goal INTEGER, -- 0/1
    UNIQUE (game_id, team_id, event_id)
);
CREATE INDEX idx_goal_events_game ON game_goal_events (game_id);
```

## game_penalty_events / game_goalkeeper_events

Source: `game_detail` (per-game fetch; not present on `games_by_season`).
These require the per-game call, unlike goals.

```sql
CREATE TABLE game_penalty_events (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    team_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    player_id INTEGER, -- 0 for bench/team penalties (seen in fixture)
    sufferer_player_id INTEGER,
    period INTEGER,
    game_time_seconds INTEGER,
    penalty_begin_time INTEGER,
    penalty_end_time INTEGER,
    fault_name TEXT, -- Finnish, e.g. "Kampitus"
    fault_type TEXT, -- short code, e.g. "KAM"
    penalty_minutes INTEGER,
    UNIQUE (game_id, team_id, event_id)
);

CREATE TABLE game_goalkeeper_events (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    team_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    period INTEGER,
    game_time_seconds INTEGER,
    begin_time INTEGER, -- goalie's own-shift begin/end (net in/out), not the penalty clock
    end_time INTEGER,
    empty_net INTEGER, -- 0/1
    UNIQUE (game_id, team_id, event_id)
);
```

## game_rosters (lineups) + players

Source: `game_detail` (`homeTeamPlayers`/`awayTeamPlayers`) — this is the
confirmed dressed-roster data (`roleCode='MV'` = goalie), same payload the
liiga.fi `/kokoonpanot` page renders client-side.

**Leakage note (does not block this backfill, but scopes it):** the
endpoint catalog flags that whether `game_detail` returns partial/no lineup
data *before* puck drop for a future game is still unconfirmed — untested
per hard rule 1 (no info from after puck drop may leak into features). This
backfill only ever calls `game_detail` for already-completed historical
games, so there's no leakage risk in what this session builds. The open
question only matters for the live snapshot job (`src/hockey_edge/snapshot/`,
explicitly out of scope here) — do not reuse `game_rosters` rows produced by
*this* ingest path as a stand-in for a pre-game lineup snapshot.

```sql
CREATE TABLE game_rosters (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    team_id TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    role TEXT, -- e.g. "CENTER", "LEFT_DEFENSEMAN"
    role_code TEXT, -- e.g. "KH", "VP", "MV" (goalie)
    jersey INTEGER,
    captain INTEGER, -- 0/1
    alternate_captain INTEGER,
    rookie INTEGER,
    injured INTEGER,
    suspended INTEGER,
    removed INTEGER, -- left team mid-season; still a valid historical roster row
    UNIQUE (game_id, team_id, player_id)
);
CREATE INDEX idx_game_rosters_game ON game_rosters (game_id);
CREATE INDEX idx_game_rosters_player ON game_rosters (player_id);

CREATE TABLE players (
    player_id INTEGER PRIMARY KEY, -- API's numeric playerId, stable across endpoints
    first_name TEXT,
    last_name TEXT,
    handedness TEXT,
    height INTEGER,
    weight INTEGER,
    country_of_birth TEXT,
    nationality TEXT,
    date_of_birth TEXT,
    last_seen_team_id TEXT,
    updated_at TEXT NOT NULL -- last upsert time, UTC
);
```

`players` is a dimension table upserted opportunistically from whatever
endpoint last saw the player (`game_detail` rosters during this backfill);
name normalization across liiga.fi / Veikkaus / community sources is a known
pain per `docs/DATA_PIPELINE.md` — this table is the Liiga-side anchor for
that future normalization work, not a solution to it. Full player bios
(`player_info`) and league-wide roster listings (`player_list`) are cataloged
endpoints but **not parsed into tables this pass** — deferred, see "Deferred"
below.

## game_team_period_stats / game_player_period_stats / game_goalie_period_stats / game_puck_control

Source: `game_stats` — **recent-seasons-only** (confirmed working for
season=2024, returns `{"stats": "Remote server error"}` for season=2010).
Richest source for the team shot-quality feature family. Goalies get a
separate `goaliePeriodStats` array in the same payload with a different stat
set (saves, goals allowed) than skaters — kept as a separate table rather
than jamming both shapes into one, since forcing a shared schema would mean
every skater row carries NULL goalie columns and vice versa.

```sql
CREATE TABLE game_team_period_stats (
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

CREATE TABLE game_player_period_stats (
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
    expected_goals_player REAL, -- NULL in every 2024 sample seen; API field exists but unpopulated so far
    expected_goals_against REAL,
    UNIQUE (game_id, team_id, player_id, period)
);

CREATE TABLE game_goalie_period_stats (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    team_id TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    jersey_id INTEGER,
    period INTEGER NOT NULL,
    shots_on_goal INTEGER,
    saves INTEGER,
    goals_allowed INTEGER,
    save_percentage TEXT, -- API sends as string, e.g. "" when 0 shots; store as-is, cast at query time
    time_on_ice_seconds INTEGER,
    UNIQUE (game_id, team_id, player_id, period)
);

CREATE TABLE game_puck_control (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    period INTEGER NOT NULL,
    home_control_seconds REAL,
    away_control_seconds REAL,
    contested_control_seconds REAL,
    UNIQUE (game_id, period)
);
```

## shot_events

Source: `shotmap` — **recent-seasons-only**, same failure mode as
`game_stats` (500s on season=2010, works on season=2024). No natural unique
key exists in the payload (no `eventId` field, unlike goals/penalties), so
this table has no uniqueness constraint; re-parsing a game deletes and
reinserts all its rows rather than upserting row-by-row.

```sql
CREATE TABLE shot_events (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(game_id),
    period INTEGER,
    game_time_seconds INTEGER,
    shooting_team_id TEXT,
    shooter_player_id INTEGER,
    blocker_player_id INTEGER,
    shot_x INTEGER,
    shot_y INTEGER,
    event_type TEXT, -- e.g. GOALIE_BLOCKED, MISSED, GOAL
    strength_type TEXT, -- API's "type", e.g. EvenStrengthShot
    own_team_players_on_ice INTEGER,
    other_team_players_on_ice INTEGER
);
CREATE INDEX idx_shot_events_game ON shot_events (game_id);
```

## standings

Source: `standings` — one call per season, a dict keyed by phase. The
`sortByPointsPerGameSeason` key is a bare boolean flag (not a table of rows)
and isn't stored. `playoffsLines` (seen as `[6, 10]`) looks like bracket
seed-line markers, not team rows — also not stored this pass; revisit if a
future feature needs it.

```sql
CREATE TABLE standings (
    id INTEGER PRIMARY KEY,
    season INTEGER NOT NULL,
    phase TEXT NOT NULL, -- 'season' (regular), 'playoffs', 'playout', 'qualifications', 'valmistavat_ottelut'
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
```

## Endpoint → table map

| Endpoint (catalog name) | Feeds | Backfill granularity | Recent-seasons-only? |
|---|---|---|---|
| `games_by_season` | `games`, `game_goal_events` | 1 call/season | No — verified 1976–2024 |
| `game_detail` | `game_rosters`, `players`, `game_penalty_events`, `game_goalkeeper_events` | 1 call/game | No — verified 2010, 2024 |
| `game_stats` | `game_team_period_stats`, `game_player_period_stats`, `game_goalie_period_stats`, `game_puck_control` | 1 call/game | **Yes** — 500s pre-2024 in testing |
| `shotmap` | `shot_events` | 1 call/game | **Yes** — 500s pre-2024 in testing |
| `standings` | `standings` | 1 call/season | No — verified 2000, 2024 |
| `schedule_by_season` | *(not parsed — lighter duplicate of `games_by_season` without goal events; kept cataloged as a fallback if `games_by_season` ever breaks)* | — | — |
| `games_by_date`, `games_by_week`, `gameweeks`, `tournament` | *(live/navigation helpers, not backfill sources — `games_by_date` explicitly ignores `season` per catalog notes)* | — | — |
| `player_info`, `player_list`, `team_info`, `teams_stats`, `milestones` | *(deferred — see below)* | — | — |

## Deferred (not built this pass)

- **`player_info` / `player_list`**: real player-bio and roster-list tables.
  `game_rosters` + the minimal `players` dimension above already give
  per-game dressed rosters, which is what the backfill smoke test needed.
  Full bios add career-history detail (`historical` per-season splits,
  `teamList`/`teams` stints) not required by any current feature family in
  `docs/DATA_PIPELINE.md`.
- **`team_info` / `teams_stats`**: franchise-level season history and
  aggregate team stats. `teams_stats` is still unconfirmed (500/502 on every
  param combination tried — needs a devtools capture per the catalog's open
  item) so there's nothing real to build against yet; `team_info` fixture
  is real but low-priority (not in the planned feature families).
- **`milestones`**: live career-milestone tracker, explicitly noted in the
  catalog as low priority / not in the planned feature families.

## Corpus-wide caveat: fixtures are trimmed

`fixtures/liiga/README.md` describes these as trimmed real responses. In at
least one endpoint (`games_by_season`), the field set differs between the
1976/2010/2024 samples in ways too inconsistent to be pure schema evolution
(e.g. `iceRink` present in 1976 and 2024 samples, absent in the 2010 sample;
`end` present only in the 2024 sample) — plausibly fields that were `null`
in the untrimmed response and got stripped during trimming, rather than
fields that never existed for that season. **Every column in this schema is
nullable unless it's part of a game's core identity** (`game_id`, `season`,
team ids/names, `start_utc`) — the parser must not assume a field's absence
in one fetch means it's absent for that endpoint/season in general.

## Smoke test results (season=2024, 2026-07-13)

Ran `PYTHONPATH=src python -m hockey_edge.ingest.liiga.backfill --season 2024
--max-games 20`: fetched `games_by_season` across all 5 tournament values +
`standings` in full for the season (561 games total — 450 `RUNKOSARJA` + 45
`PLAYOFFS` + 66 `PRACTICE`; `playout`/`qualifications` had zero games this
season), then sampled `game_detail`/`game_stats`/`shotmap` for the season's
first 20 (all `RUNKOSARJA`) games rather than all 561 — see "`--max-games`
sampling" below for why. Verified idempotent (a second run with identical
args made zero HTTP requests, finished in ~1.5s, row counts unchanged) and
`--force` (refetches, writes new `raw_responses` rows, curated row counts
still unchanged since content was identical).

Row counts:

| Table | Rows |
|---|---|
| `sync_state` | 66 |
| `raw_responses` | 66 |
| `games` | 561 |
| `game_goal_events` | 3,034 |
| `game_penalty_events` | 174 |
| `game_goalkeeper_events` | 116 |
| `game_rosters` | 976 |
| `players` | 378 |
| `game_team_period_stats` | 136 |
| `game_player_period_stats` | 2,584 |
| `game_goalie_period_stats` | 272 |
| `game_puck_control` | 20 |
| `shot_events` | 1,760 |
| `standings` | 58 |

**`--max-games` sampling, and why:** a full season=2024 per-game backfill is
561 games x 3 endpoints x the 1.5s polite-scraping delay = well over 40
minutes of real traffic against liiga.fi's undocumented API. That's more
load than a *smoke test* should generate before the schema itself has been
reviewed, so `backfill.py` grew a `--max-games N` flag: `games_by_season` +
`standings` always run in full (6 requests total — cheap, and needed to
validate phase-flagging across the whole season), but per-game endpoints are
capped to the season's first N games. This run used N=20. The full
per-game backfill is deferred to the reviewed real run, same as the
full 10-season backfill.

**Two real bugs found and fixed by this smoke test** (both are why a smoke
test on real data, not just fixtures, was worth doing before the full
backfill):

1. **FK violation on re-parsing a season** — see the "Rebuild caveat" note
   under the `games` table above. First version deleted-then-reinserted
   `games` by season, which broke as soon as any other endpoint's rows
   referenced those game_ids. Fixed by upserting `games` in place.
2. **Windows/NTFS silently hides raw cache files with a colon in the
   name.** `raw_cache.py` built cache filenames from `entity_id` directly,
   and season-level `games_by_season` entity_ids look like
   `"2024:runkosarja"` (season + tournament, to disambiguate the 5 phase
   fetches sharing one season). On Windows, a bare colon in a path is NTFS's
   alternate-data-stream separator (`file:stream`) — the write **did not
   error**, it silently succeeded into a hidden stream on a 0-byte `2024`
   file invisible to `ls`, Explorer, and git. Reads happened to keep working
   (Windows resolves the same ADS path back), which is exactly why this is
   dangerous: idempotency checks and row counts looked correct even though
   the "file" didn't exist in any normal sense. Fixed by also sanitizing
   `:` (not just `/`) in `raw_cache.py`'s filename construction. Caught by
   checking `ls data/raw/liiga/games_by_season/` against the log's claimed
   file paths, not by any error — worth remembering if raw-cache filenames
   ever grow another delimiter.
