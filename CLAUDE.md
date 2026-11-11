# CLAUDE.md — hockey-edge operating brief

## What we're building
**hockey-edge**: personal-use-first hockey prediction & edge tool for **Liiga + NHL**: data pipelines
→ feature store → calibrated win probabilities → compared against bookmaker odds →
immutable prediction log. Liiga is the differentiator (nobody models it; softer odds).
Full plan: `docs/PLAN.md`. Data contract: `docs/DATA_PIPELINE.md`. Model contract:
`docs/MODEL.md`. Read them before structural work.

`docs/PROJECT_CONTEXT.md`: owned by the design chat. Emitted whole and replaced
wholesale at session close. Not edited by Human or by Claude Code sessions.

`docs/STATUS_LOG.md`: the dated Status entries this file used to carry, newest
first. History only; the "Current status" section below is the live one and is
**replaced each session, not appended to**.

## Decided stack — do not re-litigate without being asked
Python · SQLite · liiga.fi JSON API at `https://liiga.fi/api/v2` (endpoints
discovered 2026-07-12 via JS-bundle grep + direct curl, not devtools — catalog
and `verified_seasons` in `src/hockey_edge/ingest/liiga/endpoints.py`, the one
module that defines endpoints) · NHL official API · MoneyPuck/NatStatTrick for
NHL xG bootstrap · lineups and starting goalies from liiga.fi `game_detail`
(+`game_preview`), which carries the confirmed lineup about 30 min before puck
drop; liiga.fi has no odds · odds: NHL via The Odds API free tier (1 credit =
all NHL games per market+region; not built yet), Liiga via OddsPapi free tier
(billed per HTTP request, confirmed against the dashboard counter; Liiga
tournamentId=134; fallback = scrape Veikkaus, the book actually bettable in
Finland, stubbed in `snapshot/odds/veikkaus.py`, not built) · odds capture
behind a swappable provider interface (`src/hockey_edge/snapshot/odds/`) · Elo
baseline + LightGBM blend · local compute only.

**Liiga odds: OddsPapi, polling `pinnacle` (primary, the benchmark line) +
`bet365` (fallback). Settled 2026-09-17, live since — do not re-open.**
`/odds-by-tournaments` carries prices in-season (an early board shows fixtures
without prices). The `bookmaker` param is single-valued, so a poll costs **one
request per book**. `snapshot/job.py` runs `OddsPapiProvider` over
`ODDS_BOOKS = ("pinnacle", "bet365")`; `NullOddsProvider` is kept only as a
zero-request stub. See `docs/ODDS_PLAN.md`.

Tests: `tests/`, **stdlib `unittest`, no pytest** — run
`python -m unittest discover -s tests` (292 tests, 5–8 min). New tests go in
`tests/test_*.py` or the matching subpackage (`tests/features/`,
`tests/models/`, `tests/validation/`). They assert against **real saved data**
(`fixtures/oddspapi/`, `fixtures/liiga/`, `fixtures/features/mini.sql`), never
invented payloads, and make zero HTTP requests. A test needing a snapshot DB
builds a temp one via `storage.get_connection(tmp_path)`; nothing touches
`data/*.db`. `mini.sql` is regenerated from the live databases by
`scripts/make_feature_fixture.py`, and a nightly resync can renumber row ids in
it, so tests never pin ids. Every point-in-time feature gets a truncation test
and a poison test.

Tooling: plain venv + `requirements.txt` (no uv/poetry; `numpy`, `scipy` are in
it) · `pyproject.toml` (setuptools, `pip install -e .`) makes
`src/hockey_edge` an editable-installed package, so `python -m
hockey_edge.<module>` works from an activated venv; **no `PYTHONPATH=src`
prefix anywhere** (any doc still showing it outside a historical log is stale)
· commands run from the repo root · raw JSON cached as files under `data/raw/`
(gitignored), SQLite stores metadata only · one real sample response per
verified endpoint/season in `fixtures/liiga/<name>/<season>.json` (git-tracked,
unlike `data/raw/`).

Databases (all under `data/`, gitignored): `hockey.db` (curated liiga.fi
data), `snapshots.db` (pre-game lineups, odds, capture windows),
`features.db` (derived, full rebuild every time), `backtest.db` (validation
runs; **not the prediction log**, which is step 7).

## Hard rules
1. **No leakage.** Features use only information timestamped before puck drop.
   Snapshot rows are append-only with `captured_at` (UTC).
2. **Prediction log is immutable.** Never edit or delete rows; corrections go
   in as new rows under a new `model_version`.
3. **Walk-forward validation only.** Never shuffle games across time.
4. **Raw data is append-only**; corrections live in derived layers
   (`features.db`), never in `hockey.db` or `data/raw/`.
5. **Resumable sync from day one** — `sync_state` table, idempotent re-runs, raw
   responses cached so parsing can be redone without refetching.
6. **Polite scraping**: rate limits (1.5 s), cached responses, normal UA.
   liiga.fi API is undocumented and can change — endpoint definitions live only
   in `endpoints.py`.
7. **Log loss + calibration are the metrics.** Accuracy is never the target.
8. No bookmaker affiliate integrations, ever.
9. No autonomous git commits or pushes (global rule; applies here too).
10. **`(game_id, season)` together is the key** for `games` and every per-game
    table; `game_id` alone is reused across seasons.
11. **Feature modules read games only through `features/history.py`**, and
    nothing model-facing reads odds: only `validation/market.py` (and
    `features/benchmark.py`, which builds `benchmark_odds`) touch them. Tests
    enforce both.

## Build order
1. Liiga ingest — **done** for 2015–2027; live season kept current by
   `scripts/nightly_sync.py`
2. Snapshot capture job (lineups/goalies/odds) — **live**: lineups since
   2026-09-02, odds since 2026-09-17
3. NHL ingest — **deferred** behind the Liiga feature store and Elo
4. Feature store — **done 2026-11-09** (F0–F3d, `docs/FEATURE_STORE_PLAN.md`)
5. Elo baseline + walk-forward harness — **done 2026-10-13**, tagged `elo-1`
6. LightGBM + blend
7. Prediction log + local dashboard

## Current status (2026-11-11)

Agrees with `docs/PROJECT_CONTEXT.md` "Where things stand", except that its
"known hole in the `game_stats` value guard" is now closed (below). History:
`docs/STATUS_LOG.md`.

- **Step 1, Liiga ingest: done**, 2015–2027 in `data/hockey.db`. Historical
  recovery finished 2026-09-25 (`docs/RECOVERY_BACKLOG.md`). The scheduled
  task "hockey-edge nightly sync" runs `scripts/nightly_sync.py` daily
  (backfill of the season-level endpoints, forced, with `--only-ended`, then
  guarded `resync --days 7`) at 23:00 local.
- **Per-team `game_stats` value guard: done 2026-11-11.** `resync.py` now
  also refuses a reparse (`value_guarded`, rows restored exactly) when any
  team's total falls below 50% of that team's old value, on the stored
  `team_id`; a team with old total 0 is not checked. The per-game and goal
  checks are unchanged. Backtest, replaying every stored `game_stats`
  response pair (zero HTTP): of 106 live 2027 corrections the per-game guard
  accepted (49 of them by 2026-09-25, the era of the original 44), the
  per-team check refuses **none**; the largest per-team drop was power-play
  instances 4 → 3 (TPS, 2027:2701339). On the 5,347 historical 2015–2024
  pairs it refuses exactly the 5,307 the per-game guard already refuses.
  History against itself (6,724 games): zero refusals. Backup taken first
  (`hockey-edge-data-2026-11-11.zip`, verified). Details `docs/RESYNC.md` 4c.
- **Step 2, snapshot capture: live.** Task "hockey-edge snapshot job", every
  15 min 11:00–23:00 local, `WakeToRun` + `StartWhenAvailable`, runs
  `python -m hockey_edge.snapshot.job --once`. Lineups (`lineup_snapshots`)
  since 2026-09-02, odds (`odds_snapshots`, OddsPapi pinnacle + bet365) since
  2026-09-17. Starter inference (`line == 1`) is about 98% right. **No
  capture 2026-10-08 to 10-10 (PC shut down): those 13 games have no
  confirmed lineup and no usable closing line, permanently.** **On
  2026-10-17 the mid and closing windows were missed for all 7 games
  (lineups and odds alike): no confirmed lineup and no closing line for
  them either, permanently.**
- **Step 3, NHL ingest: deferred.**
- **Step 4, feature store: done 2026-11-09 (F0–F3d), `FEATURE_VERSION` is
  `f3d`.** Plan `docs/FEATURE_STORE_PLAN.md`, audit `docs/FEATURE_AUDIT.md`.
  Rebuild about 45 s (up to 74 s under machine load). Table meanings are in
  the DDL comments in `features/db.py`.
  - F1: `game_spine` (regulation 1X2 target, phase flags), `season_format`,
    `team_season`, `features_home`, `features_schedule`, `benchmark_odds`,
    `build_meta`.
  - F2a: `game_goalies`, `game_starters`, `features_goalie` (save% over
    500/1,500 shots shrunk with k = 1,000; GSAx-style mean over 20 games).
  - F2b: `game_team_shots`, `features_shots` (Corsi and team xG, 10/25/40-game
    windows, no shrinkage by design; window game counts stored).
  - F3a: `penalty_events_resolved` (penalised team from rosters, bench fix
    `f3a.1`).
  - F3b: `game_team_special_teams`, `features_special_teams` (PP%/PK% shrunk
    with k = 150 opportunities; raw counts stored).
  - F3c: `game_skater_toi` (who dressed, with ice time and F/D; 228,790
    rows), `features_availability` (share of the last 5/10 games' ice
    time missing from the resolved lineup, this season only; lineup from
    `lineups.resolve_lineup`: snapshot, else actual, else the last known
    lineup). Report `python -m hockey_edge.features.availability_report`.
    In 2027 the confirmed lineup equals who played in all 254 team-games
    with both.
    2016-01-05 is a named ice-time gap day (`lineups.GAP_DATES`); a NULL
    roster `role` takes `pos` from `role_code`'s letter (H/KH → F, P → D).
  - F3d: `features_motivation` (RUNKOSARJA only, NULL otherwise): table
    before the game (3/2/1/0 points, then goal difference), games
    remaining on the nominal schedule, signed gaps to the playoff and
    relegation lines, conservative clinched / eliminated / safe / doomed
    flags. Lines in `season_format.playoff_spots` / `relegation_spots`
    (hand-curated): 10 spots 2015–2024 (2020's planned 10 although
    cancelled), 12 from 2025; relegation zone 2025 bottom 2 (playout),
    2026 none, 2027 bottom 3 direct (2027 format confirmed from MTV
    Uutiset's season preview, 2026-08-31). Report
    `python -m hockey_edge.features.motivation_report`.
  - F2/F3 feature tables are **one row per team per game**; F1's are
    home/away columns. Step 6 pivots them.
  - Before step 6: one full regeneration of `fixtures/features/mini.sql`
    with the tests re-pinned (it has F3c slices appended by
    `scripts/make_feature_fixture.py --append-f3c`).
- **Step 5, Elo + harness: done 2026-10-13.** Plan `docs/ELO_HARNESS_PLAN.md`,
  results `docs/ELO_RESULTS.md`. Elo beats base rates by 0.0316 ± 0.0039 log
  loss on pooled 2018–2026 (4,075 games), a goal-difference rating by only
  0.0028 ± 0.0014; Pinnacle's closing line beats Elo by 0.049 ± 0.020 on 49
  games. `elo-1` in `models/versions.py` (K 10, HFA 0, r 0.5, margin
  multiplier on, `s_ot` 0.15).
- **Steps 6–7: not started.** Step 6 is planned in the design chat first.
- **Pipeline safety: done 2026-10-16** (`docs/PIPELINE_SAFETY_PLAN.md`).
  Task "hockey-edge backup" runs `scripts/backup_data.py` weekly on Sunday
  at 11:05 local, `StartWhenAvailable` + `WakeToRun`; keeps 4 verified zips in
  `D:\Users\Nikla_000\DATA\hockey-edge-backups`. Restore: `docs/BACKUP.md`.
  healthchecks.io alerts via `src/hockey_edge/ops/heartbeat.py`: checks
  `snapshot-job`, `missed-windows`, `nightly-sync`, `backup`.
- **Tests: 299** (5–8 min; the per-team guard added 7), `python -m unittest discover -s tests`.

Commands (repo root, venv active):

```
python -m unittest discover -s tests
python -m hockey_edge.features.build
python -m hockey_edge.features.goalie_report
python -m hockey_edge.features.penalty_report
python -m hockey_edge.features.special_teams_report
python -m hockey_edge.features.availability_report
python -m hockey_edge.features.motivation_report
python -m hockey_edge.validation.run --models elo,base_rate,gd_rating
python -m hockey_edge.validation.report --run latest
python -m hockey_edge.snapshot.job --dry-run
python scripts/nightly_sync.py --season 2027 --days 7
python scripts/verify_starters.py
python scripts/backup_data.py
```

`scripts/elo_smoke.py` is a quick Elo sanity pass with default parameters.
The snapshot job, nightly sync and backup are normally left to their
scheduled tasks; check one with
`Get-ScheduledTaskInfo -TaskName "hockey-edge snapshot job"`.

## Gotchas

### liiga.fi data and ingest
- **Seasons are numbered by ending year**: `season=2024` is Sep 2023–Apr 2024;
  2027 is the live 2026-27 season (17 teams, 544 RUNKOSARJA games).
- **`game_id` is not globally unique across seasons** — liiga.fi reuses small
  `RUNKOSARJA`/`PRACTICE` game_ids per season (449/450 season-2023 ids
  collided with 2024's before the fix). Key on `(game_id, season)`; raw-cache
  entity ids for `game_detail`/`game_stats`/`shotmap` are
  `f"{season}:{game_id}"`. Root cause in `docs/BACKFILL_RESULTS.md`.
- **liiga.fi degrades and rearranges its own historical data; the local copy
  is better than the source.** On refetch, `game_detail` can lose goalkeeper
  events and roster rows and move events between the home/away arrays;
  `shotmap` corrects stats in place; `game_stats` now serves 2015–2024 with
  period stats stripped (same row counts, time on ice and corsi 0, faceoffs
  null; real pair in `fixtures/liiga/game_stats/2024_game2_*.json`).
  2025/2026 `game_team_period_stats` were already stripped when first fetched.
  Never assume "content changed" means "content improved".
- **Refetch only through `resync.py`.** Its no-shrink guard refuses a reparse
  that would leave any per-game table with fewer rows (per table, not per
  team), and its value guard refuses a `game_stats` reparse whose totals
  collapse (`value_guarded`): any total below 50% of its old value, for the
  game **and for each team** (on the stored `team_id`; a team whose old
  total is 0 is not checked, so grow-from-zero passes). The per-team check
  closes the hole where one team stripped to 0 left about half the game
  total. The raw response is saved either way. **The
  row-count guard cannot see stripped values**, and the value guard covers
  `game_stats` only, so don't bulk-refetch `shotmap` or `game_detail`
  history. **`backfill.py --force` has no guard at all.**
- **Never refetch `game_stats` for 2015–2024 into curated tables.** The July
  2026 fetch is the good copy. After any bulk reparse, check value sums
  (`scripts/recovery_report.py --diff`, section 8), not just row counts, and
  take a backup first. Pre-recovery copies are in `data/backups/`.
- **A "no total may drop" rule is wrong**: real live corrections drop time on
  ice by up to 5.3%, corsi by up to 12%, player goals by 1 toward the final
  score. The value guard's thresholds were backtested against those.
- **The no-shrink guard is all-or-nothing per game.** One shrinking table
  reverts all of them; `resync.py --salvage-from-raw` (grow-from-zero, no
  HTTP) recovers a refused game's empty tables.
- **A live season needs `backfill.py --only-ended`**, or per-game endpoints
  get fetched for unplayed games, cached as `success`, and skipped forever.
- **`games_by_season`/`standings` must be force-refetched on every
  live-season run** (`--force --endpoints games_by_season,standings`, as
  `nightly_sync.py` does), or `games.ended` freezes. Per-game endpoints are
  deliberately not forced there.
- `games_by_date` and `team_info` ignore the `season` param; `games_by_date`
  502s intermittently. `tournament=playoffs` returns a superset that includes
  PLAYOUT and QUALIFICATIONS games.
- **`serie` and `roleCode` vocabularies are open-ended.** `serie` has
  RUNKOSARJA/PLAYOFFS/PRACTICE/PLAYOUT/QUALIFICATIONS and, since 2027,
  `PITSITURNAUS`; never constrain a column to the list. `games_by_season`
  says `"PRACTICE"` where `standings` says `valmistavat_ottelut`. `roleCode`
  is generic (`{H, P, MV}`) days out and detailed near game time.
- **Playoff formats vary by season** (changed 2024-25; 2022's first round
  was a two-game aggregate with a sudden-death tiebreak, 2022:49298). Flag
  phase per game; never assume a bracket shape.
- **On Windows, a raw `:` in a `data/raw/` filename writes to a hidden NTFS
  alternate data stream** without erroring. Sanitize `:` like `/` in any new
  cache path (fixed once in `raw_cache.py`).
- **A penalty `player_id` of 0 is not a player** (team/bench penalty, mostly
  *Joukkuerangaistus*). Exclude it from player-level joins.
- **`game_goalkeeper_events` cannot identify the starter.** 36% of 2026's
  ended games have no rows at all, and only some 2015–2024 games log the
  opening assignment at `begin_time` 0. The actual starter comes from
  `game_goalie_period_stats` via `features/starters.derive_actual_starter`.
- `player_info`/`player_list`/`team_info`/`teams_stats`/`milestones` are
  cataloged with no parser; `teams_stats` 500s on every param combination
  tried.
- Player-name normalization across sources (liiga.fi vs Veikkaus vs
  community) is a known pain.

### Feature store and models
Full gap lists: `docs/DATA_PIPELINE.md` "Known data gaps" and
`docs/FEATURE_AUDIT.md`.
- **History is cut by UTC date, not start time**: `history_before` returns
  ended games from an earlier date, so an earlier same-day game still in play
  can't leak. `games.end_utc` is unusable. Right for Liiga, wrong for NHL
  (games cross UTC midnight). `history_before(per_game=...)` only accepts the
  tables in `PER_GAME_TABLES`.
- **Elo works one UTC date at a time**: predict every game on a date, then
  apply that date's results; the pass stops if a team appears twice on one
  date. A test checks it agrees with `features/history.py`.
- **Season team lists come from `team_season`**, never from games seen so far
  (that leaked future games in step 5; the truncation test caught it).
  `season_format.py` is hand-curated and checked every build: a new season,
  new team, wrong team count, or more PLAYOFFS teams than the curated
  `playoff_spots` (any other count once a later season exists; the live
  season's playoff games are listed round by round) stops the build. 2026 had no relegation from Liiga
  (Niklas, 2026-11-09).
- **Standings are derived, not read from `hockey.standings`**: 3/2/1/0
  points match liiga.fi for every team 2015–2027, but liiga.fi breaks
  points ties its own way (not modelled; motivation ranks by goal
  difference), and in 2015–2016 it files Espoo Blues' record under
  K-Espoo's id next to a stray partial Blues row.
- **`games_remaining` uses the nominal schedule** (`games_per_team`), not
  the listed games: 2020 was cut short and 2021–2022 lost games mid-season,
  so counting listed games would leak those later cancellations.
- **The home edge is fixed at 0 in Elo, not tuned.** The ordered-logit
  link's cutpoints carry it, so tuning can't identify HFA. Don't put it back
  in the grid.
- **Regulation result comes from `games.finished_type`**; goals from the
  final score (`games.home_goals`/`away_goals`), never team-period stats.
- **Real goals**: a goal event with `VT0` or `RL0` in `goal_types` is not a
  goal (`RL0` is a missed penalty shot), nor is a shootout attempt
  (`end_type` SO, period ≥ 5). Then goal events match the final score in
  every game except 2021:480 (damaged at source: 0–1 final, no goal events).
- **`game_penalty_events.team_id` is the wrong team in whole seasons.** Use
  `penalty_events_resolved`: player penalties take the player's roster team,
  bench penalties the roster team of `sufferer_player_id`, game direction
  only as a fallback.
- **A competitive game with zero penalty events is missing data, not a clean
  game** (six left plus the 2022:49298 tiebreak): NULL counts, skipped in
  windows. Same for gap games in shots (no `shot_events` row → NULL Corsi).
- **Power-play opportunities are the coincident-adjusted count on resolved
  teams, for every season**, never liiga.fi's `powerplay_instances` or raw
  penalty counts. It runs about 0.2 per team-game below stored by design, so
  compare derived with derived. PP goals come from the `YV`/`YV2` label.
- **Shots: Corsi only, no Fenwick (D4)** — from 2023 liiga.fi labels attempts
  blocked that it earlier labelled missed. Every `shot_events` row counts;
  even strength = `EvenStrengthShot`. `shot_events.shooting_team_id` is a bare
  number; other tables use `number:slug`. Never use liiga.fi's own corsi.
- **xG**: game-level from 2021 (used only where both teams have xG > 0);
  player-level only from 2025; none per goalie.
- **Goalie ice time is 0 for all of 2026 and partial in 2025.** "Whole game"
  means the only goalie of his team who faced a shot. Never define anything by
  ice time without checking these seasons.
- **Skater ice time exists in every season** (97–99.5% of skater rows > 0).
  Gaps: a team-game whose skaters total 0 (2021:51700 HPK, the ten 2025
  PLAYOUT/QUALIFICATIONS games) or that is on a `lineups.GAP_DATES` day
  (all ten team-games of 2016-01-05: four at 0, six at 2–17% of a game)
  has `toi_s` NULL in `game_skater_toi` and is skipped by windows.
  2015:45873, 2021:480 and 51 games of 2016 have no stats rows at all.
- **`game_rosters` is the squad list, not the lineup.** Who played is the
  players with a `game_player_period_stats` row (`game_skater_toi`); the
  roster gives only the position: `role`, else `role_code`'s letter when
  `role` is NULL (H/KH → F, P → D). A snapshot's `roleCode` is never used.
- **`starter_source` and `lineup_source` are audit columns, never model
  inputs.** Both resolvers take the snapshot from
  `pregame.latest_pregame_snapshot`: pre-game snapshot, else the actual
  starter / lineup (ended games, training only), else a fallback (expected
  goalie; last known lineup), whose value is stored on every row.
- **`benchmark_odds` takes Pinnacle's last row before the start even when
  it's old**: filter on `minutes_before_start` ≤ 60.
- **`docs/ELO_RESULTS.md` is generated below its marker line**; the summary
  above it is hand-written and is not updated by regenerating.
- Puck control has values only from 2023 (cumulative seconds in 2023–2026,
  per-period in 2027). Not a planned feature input.
- Liiga small samples: regress early-season features hard to the league mean.
- **2022:49298 (tiebreak) and the 2025 Pelicans–Jokerit qualification series
  do not feed ratings.** Pelicans won that series 4-1; Jokerit's history before
  2027 is five competitive losses plus friendlies, barely better than no data.
- Liiga odds are three-way (regulation 1X2); NHL moneyline is two-way incl.
  OT. Store market type.

### Snapshot job and scheduling
- **Three Windows scheduled tasks**, all running the venv's `pythonw.exe` from
  the repo root: "hockey-edge snapshot job", "hockey-edge nightly sync",
  "hockey-edge backup". Under pythonw there is no console, so a failure
  before logging shows only as a non-zero `LastTaskResult`
  (`Get-ScheduledTaskInfo`). Logs: `logs/snapshot_job.log`,
  `logs/nightly_sync.log`, `logs/backup.log`.
- **The tasks run from the working checkout.** A half-finished edit to job
  code during 11:00–23:00 can break a live capture.
- **Snapshot window is 11:00–23:00 local**, because some fixtures start at
  12:00 or 14:00. The binding constraint is the closing (T-25 min) window:
  `storage.get_due_windows` only returns a window while `start_utc > now`, so
  a closing window due before the first tick is MISSED permanently. Check the
  real schedule before narrowing it.
- **`WakeToRun` only wakes a sleeping machine**, not a hibernated or shut-down
  one. The job's own due/missed logic (`get_due_windows`,
  `mark_missed_windows`) makes a late wake-up correct; never replace it with
  an "is now ≈ the window time" check.
- **Task triggers must be stored without a UTC offset or they shift with
  DST.** A `StartBoundary` like `2026-09-25T22:00:00+02:00` fires at a fixed
  UTC time, so the nightly sync ran at 22:00 instead of 23:00 from
  2026-10-25 until all three were fixed on 2026-11-09.
  `New-ScheduledTaskTrigger -At` writes an offset: set
  `$trigger.StartBoundary` to plain local time (`2026-11-15T11:05:00`)
  before registering, or on an existing task edit `Triggers[0].StartBoundary`
  and `Set-ScheduledTask -InputObject`. Check with
  `(Get-ScheduledTask -TaskName <name>).Triggers[0].StartBoundary`.
- **Task registration is fiddly.** `Register-ScheduledTask -Xml` from
  PowerShell 7 needs the XML declaration stripped
  (`$xml -replace '<\?xml[^>]*\?>', ''`); the file must stay UTF-8 for
  `schtasks /XML`. Editing the snapshot task needs an elevated PowerShell
  (its XML has a `<Principal>`); the nightly sync does not.
  `docs/snapshot_job_task_scheduler.xml` is the deployed definition;
  `docs/snapshot_job_launchd.plist` and `docs/snapshot_job_cron.txt` are
  scaffolding only.
- **`capture_windows` has a `kind` column ('odds'/'lineups')**; a window of
  one kind never satisfies the other. `get_due_windows` requires `kind=`.
- **Confirmed lineups appear only close to puck drop**: about T-30, exactly
  22 players per side have `line` set (20 skaters + 2 goalies); days out,
  `line` is null for everyone. The goalie at `line == 1` is the starter
  (stored as `starter_source='goalie_line_value'`,
  `starter_confidence='inferred_structural'`); array order carries no signal.
- **Alerts:** healthchecks.io ping URLs live in `.env` and are secrets; the
  code never logs them, and a ping can never stop a job. `snapshot-job`
  alerts on any day the PC is off during 11:00–23:00, game day or not
  (accepted noise; pause the check when away on purpose). Alerts shorten a
  gap; only an always-on machine prevents one.
- **Backups use SQLite's online backup, never a file copy** of a live DB.

### Odds (OddsPapi)
- **`bookmaker` is required and single-valued**, so cost scales with books.
  `GET /v4/odds` (per-fixture, all 213 bookmakers, 11.6 MB) is not for
  polling, and Veikkaus is not among its bookmakers.
- **Bursts get HTTP 429 separately from the monthly quota**, and a 429 still
  bills. `job.py` sleeps `ODDS_BOOK_DELAY_SECONDS` (20 s) between books; keep
  it, and never add a blind retry (another billed request).
- **Two request counts — don't conflate them.** `api_usage` is the job's own
  counter against `ODDS_MONTHLY_CEILING` (200); the account tier is 250. The
  50 between them absorbs manual calls. Say the exact request count before
  spending any beyond what was approved.
- **A tournament with no posted fixtures returns HTTP 404 `FIXTURE_NOT_FOUND`**,
  not 200 with `[]`. It means "no odds yet"; don't reintroduce a bare
  `raise_for_status()`.
- **Books post fixtures independently.** A window is satisfied when any book
  priced the game and it resolved to a liiga.fi game;
  `capture_windows.satisfied_by` records which. A bet365-only capture logs a
  WARNING (no benchmark line).
- **Pinnacle marks its whole book inactive on a fixture**
  (`bookmakerIsActive: false`, prices present), mostly far from puck drop;
  closing is reliably live. Those rows land `parsed=0` with the payload kept.
- **Market/outcome ids (ice hockey):** market `153` = 3-way regulation 1X2,
  outcomes `153`/`154`/`155` = home/draw/away; market `151` = 2-way moneyline
  incl. OT, `151`/`152` = home/away; price at
  `...outcomes.<oid>.players["0"].price` (decimal); `participant1Id` is home.
  bet365's outcome labels are opaque numbers; the parser rejects any row whose
  text label contradicts its slot.
- **Never string-match teams.** OddsPapi lists 2–3 participant ids per Finnish
  club name; the map is curated in `snapshot/odds/oddspapi_teams.json` (all 17
  teams, each backed by a real board fixture). **A new team's id is never
  guessed**: wait for its first board. `GET /v4/participants?sportId=<id>`
  resolves ids to names in one call.
- **Odds rows resolve to a liiga.fi game** by home+away `teamId` (from
  `discovered_fixtures.raw_payload`) plus a start within 24 h
  (`RESOLVE_MAX_START_DIFF`), only when exactly one game matches. Rows written
  unresolved keep `game_id` NULL (the table is append-only). `startTime` alone
  can't disambiguate: several games share one kickoff.

## Secrets
Odds API keys and healthchecks.io ping URLs via environment / untracked `.env`.
Never commit keys. If the repo goes public, gitignore any personal
betting/bankroll data (same pattern as market-advisor's gitignored
portfolio.md).
