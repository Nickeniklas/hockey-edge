# STATUS_LOG — dated status entries moved out of CLAUDE.md on 2026-11-09, verbatim, newest first; history only, CLAUDE.md "Current status" is the live one.

## Current status (2026-11-09, CLAUDE.md before F3d)

Agrees with `docs/PROJECT_CONTEXT.md` "Where things stand". History:
`docs/STATUS_LOG.md`.

- **Step 1, Liiga ingest: done**, 2015–2027 in `data/hockey.db`. Historical
  recovery finished 2026-09-25 (`docs/RECOVERY_BACKLOG.md`). The scheduled
  task "hockey-edge nightly sync" runs `scripts/nightly_sync.py` daily
  (backfill of the season-level endpoints, forced, with `--only-ended`, then
  guarded `resync --days 7`) at 23:00 local.
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
- **Step 4, feature store: F0–F3c done, `FEATURE_VERSION` is `f3c`.** Plan
  `docs/FEATURE_STORE_PLAN.md`, audit `docs/FEATURE_AUDIT.md`. Rebuild
  44–74 s on 2026-11-09 (it varies with machine load). Table meanings are
  in the DDL comments in `features/db.py`.
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
  - F2/F3 feature tables are **one row per team per game**; F1's are
    home/away columns. Step 6 pivots them.
  - F3c follow-up (Niklas, 2026-11-09): every team-game on 2016-01-05 is
    an ice-time gap, named in `lineups.GAP_DATES` (no threshold); the
    nine other partial team-games stay in windows. When roster `role` is
    NULL, `pos` comes from `role_code`'s letter (H/KH → F, P → D); 623
    rows of 2015–2019 gained `pos`, 6 with both NULL stay unmapped.
  - Next: **F3d** (motivation; needs the 2025–26 relegation rule from
    Niklas). Before step 6: one full regeneration of
    `fixtures/features/mini.sql` with the tests re-pinned (it now has
    F3c slices appended by `scripts/make_feature_fixture.py --append-f3c`).
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
- **Tests: 278** (5–8 min; F3c added 22), `python -m unittest discover -s tests`.

## Current status (2026-11-09, CLAUDE.md before F3c)

Agrees with `docs/PROJECT_CONTEXT.md` "Where things stand". History:
`docs/STATUS_LOG.md`.

- **Step 1, Liiga ingest: done**, 2015–2027 in `data/hockey.db`. Historical
  recovery finished 2026-09-25 (`docs/RECOVERY_BACKLOG.md`). The scheduled
  task "hockey-edge nightly sync" runs `scripts/nightly_sync.py` daily
  (backfill of the season-level endpoints, forced, with `--only-ended`, then
  guarded `resync --days 7`) at 23:00 local.
- **Step 2, snapshot capture: live.** Task "hockey-edge snapshot job", every
  15 min 11:00–23:00 local, `WakeToRun` + `StartWhenAvailable`, runs
  `python -m hockey_edge.snapshot.job --once`. Lineups (`lineup_snapshots`)
  since 2026-09-02, odds (`odds_snapshots`, OddsPapi pinnacle + bet365) since
  2026-09-17. Starter inference (`line == 1`) is about 98% right. **No
  capture 2026-10-08 to 10-10 (PC shut down): those 13 games have no
  confirmed lineup and no usable closing line, permanently.**
- **Step 3, NHL ingest: deferred.**
- **Step 4, feature store: F0–F3b done, `FEATURE_VERSION` is `f3b`.** Plan
  `docs/FEATURE_STORE_PLAN.md`, audit `docs/FEATURE_AUDIT.md`. Rebuild about
  45–56 s (56 s on 2026-11-09). Table meanings are in the DDL comments in
  `features/db.py`.
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
  - F2/F3 feature tables are **one row per team per game**; F1's are
    home/away columns. Step 6 pivots them.
  - Next: **F3c** (roster availability; player ice-time pre-check first,
    then stop for review), then **F3d** (motivation; needs the 2025–26
    relegation rule from Niklas). Before step 6: one full regeneration of
    `fixtures/features/mini.sql` with the tests re-pinned.
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
- **Tests: 256**, `python -m unittest discover -s tests`.

## Status (as of 2026-11-09, F3b)

**Feature store phase F3b is done: special teams (Family 3), on
`penalty_events_resolved`.** Plan `docs/FEATURE_STORE_PLAN.md`. F3c
(roster availability) is next.

- **Rebuild:** `python -m hockey_edge.features.build`, 56 s on 2026-11-09.
  The new steps take about 9 s (windows 8.7 s, per-game table 0.4 s); the
  rest is the existing steps running slower that day (goalie 22 s vs about
  13 s on 11-07). `FEATURE_VERSION` is `f3b`.
- **New tables:** `game_team_special_teams` (12,172: one row per team per
  ended competitive game: PP and PK opportunities, PP goals for and
  against, penalties taken and drawn) and `features_special_teams`
  (12,938: one row per team per game, same shape as F2). Columns in
  `features/db.py`; code in `features/team_penalties.py` (per-game table)
  and `features/special_teams.py` (windows). `history_before`'s
  `per_game=` allowlist has `game_team_special_teams`.
- **Opportunities:** the coincident-adjusted count (FEATURE_AUDIT item 7)
  for every season, never liiga.fi's stored `powerplay_instances`. A
  counted penalty has a resolved team, 2 or 5 minutes and a non-zero
  duration; unresolved rows are left out. **Derived runs about 0.2 below
  stored in every season** (2024 2.91 vs 3.13 stored, 2027 2.85 vs 2.96),
  so compare derived with derived: 2025 3.13, 2026 2.90.
- **PP goals come from liiga.fi's own strength label (`YV`/`YV2`),
  checked three ways:** it matches the stored `powerplay_goals` in 100% of
  team-games where one is stored (2015–2024, 2027), and agrees with the
  man advantage derived from resolved penalty timing on 99.2–99.6% of
  goals per season. Before the bench fix that agreement was 97.7% at
  worst.
- **Real-goal rule:** a goal event with `VT0` or `RL0` in `goal_types` is
  not a goal (the score does not change; `RL0` is a missed penalty shot),
  and neither is a shootout attempt (`end_type` SO, period ≥ 5). With
  that rule the goals match the final score in every game except 2021:480
  (damaged at source).
- **Missing, not zero:** the seven games with no penalty events (the six
  RUNKOSARJA ones plus the 2022:49298 tiebreak) have NULL counts and are
  skipped in the windows.
- **Windows:** 10/25/40 games over the team's rating-feeding games this
  season and last. PP% and PK% are shrunk toward the league PP% (from
  earlier dates, same two seasons) with **k = 150 opportunities**, which
  pulls a 40-game window about halfway. Raw counts are stored, so k can be
  tuned in step 6. Penalties taken and drawn are per game, not shrunk.
- **League PP% 18.7–22.2%**; 2025 is the highest (22.2%), with no stored
  value to check it against. 2015 and 2021 run high on opportunities
  (3.72) but stored is high too (4.04, 3.96). 2027's correlation with
  stored is 0.80 (0.95–0.96 elsewhere): the opening-night games stored as
  0. **2016 and 2023 now fit as well as the rest (76–77% exact):** their
  worse fit in FEATURE_AUDIT item 7 was the bench-penalty team.
- **Report:** `python -m hockey_edge.features.special_teams_report`.
- Tests: 256. `tests/features/test_special_teams.py` pins two games by
  hand (coincident penalties, a missed penalty shot), plus truncation,
  poison and a gap game skipped rather than counted as zero.

## Status (as of 2026-11-07, F3a)

**Feature store phase F3a is done: `penalty_events_resolved` (D1), the
penalised team corrected in `features.db`.** Plan
`docs/FEATURE_STORE_PLAN.md`. F3b (special teams) is next. No ingest,
parser, `hockey.db` DDL, backfill, resync or nightly-sync change:
`hockey.db` keeps what liiga.fi sent.

- **Bench-penalty fix, 2026-11-09 (`FEATURE_VERSION` `f3a.1`):** bench
  penalties now take the roster team of `sufferer_player_id`, the player who
  served them, not the game's direction. Found in the F3b check: liiga.fi
  stores most bench penalties under the other team in 2016, 2023, 2025 and
  much of 2026, whatever the game's player penalties do (about 650 rows were
  on the wrong team). Evidence: a two-minute penalty ended early at the
  second of a regulation goal was taken by the team that did not score; the
  sufferer rule is right for all 335 such bench penalties, the old rule for
  224. **The per-season bench "wrong team" counts in the first F3a report
  came from the old rule.**
- **Rebuild:** `python -m hockey_edge.features.build`, 26–28 s on
  2026-11-07 (the penalty step is 0.4 s; the 45 s quoted for F2b looks like
  machine state, not code).
- **New table:** `penalty_events_resolved` (46,687: one row per penalty
  event of a competitive game). Columns in `features/db.py`; code in
  `features/penalties.py`. `history_before`'s `per_game=` allowlist has it.
- **Rule:** a player penalty takes the penalised player's roster team
  (`roster`); a bench penalty (`player_id` 0) its sufferer's roster team
  (`sufferer_roster`). A bench penalty without a sufferer on exactly one
  roster, or a player liiga.fi lists on both rosters of the game (3
  penalties: 2016:7661, 2016:7665, 2025:399), takes the game's direction
  (`game_direction`): the stored team when most of the game's
  roster-resolved player penalties are stored right, the other team when
  most are swapped. `unresolved` (team NULL): a player in neither roster, or
  no direction. `stored_team_id` and `was_swapped` are kept for audit. A
  stored or resolved team outside the game stops the build. By source:
  44,638 roster, 1,962 sufferer roster, 85 game direction, 2 unresolved.
- **Goal-shortened check** (`penalty_report.goal_shortened`, and a test on
  the fixture): every two-minute penalty ended early by a regulation goal
  resolves to the team that did not score. Full data: 6,947 / 6,947 player,
  326 / 326 sufferer, 9 / 9 game direction. Overtime goals are left out:
  they end the game, and with it every penalty, whoever scores.
- **Report:** `python -m hockey_edge.features.penalty_report`. Swapped share
  per season reproduces FEATURE_AUDIT item 7 (2018 100%, 2023 0%, 2024
  100%, ...) **except 2016 and 2025, where item 7's counts double-count
  players on both rosters**: its roster join counts such a penalty once kept
  and once swapped (built 3,221 / 97 vs audit 3,223 / 99 in 2016; 3,464 / 4
  vs 3,465 / 5 in 2025).
- **2 unresolved rows** (5 before the fix): both by one player in neither
  roster (2016:7943, 2016:7968). The three bench penalties of games with no
  player penalty (2016:37752 PLAYOFFS, two; 2022:35, one) now resolve by
  sufferer.
- **9 games with mixed direction**, all RUNKOSARJA, one stray row each
  (2016:7856; 2025:87, 173, 231, 324; 2026:62, 66, 303, 324). Bench
  penalties there follow the majority. Item 7's method finds 12 today, the
  three both-rosters games included.
- Tests: 245 (244 at F3a). `tests/features/test_penalties.py` runs on penalty events and
  rosters for five games appended to the fixture: 2022:152 (swapped),
  2027:2701274 (right) and 2025:231 (mixed), each with a bench penalty;
  2025:399 (both rosters); 2022:35 (bench only). 2018 game 1 and 2023 game 1
  are not in it: no bench penalty, and the build takes whole seasons only.
  **Since the fix the fixture also holds penalty and goal events for the
  ended competitive games of 2027 and of 2025 from 2025-01-22 to
  2025-02-23**, with roster rows only for players a penalty names (the
  generator's `ROSTER_SLICE`); 3.0 MB. These slices replaced the earlier
  appended ones at the end of the file, limited to games the fixture has as
  ended; nothing above them changed.

## Status (as of 2026-11-07, F2b)

**Feature store phase F2 is done: F2b adds shot attempts and team xG
(Family 2).** Plan `docs/FEATURE_STORE_PLAN.md`. F3 is next.

- **Rebuild:** `python -m hockey_edge.features.build`, about 45 s now (was
  about 23 s at F2a): the shot steps add about 12 s. `FEATURE_VERSION` is
  `f2b`.
- **New tables:** `game_team_shots` (13,890: one row per team per ended
  game, Corsi for/against, even-strength Corsi, game xG) and
  `features_shots` (12,938: one row per team per game, same shape as
  `features_goalie`). Column meanings are in `features/db.py`.
- **Code:** `features/team_shots.py` (builds `game_team_shots`) and
  `features/shot_quality.py` (the windows). `history_before`'s `per_game=`
  allowlist now has `game_team_shots` too.
- **Corsi rules (D4):** every `shot_events` row counts, all four result
  types; no Fenwick. Even strength = `EvenStrengthShot`. `shot_events` has
  the bare team number, so teams are matched on the part of `games`' team
  id before the `:`. The build stops if any attempt matches neither team of
  its game (none today). No liiga.fi corsi columns or team period stats.
- **Gap games are missing, not zero:** a game with no `shot_events` row
  has NULL Corsi and is skipped in the windows (79 in 2025, one or two in
  2016, 2017, 2021, 2022). Game xG is used only where both teams have
  xG > 0, so from 2021; `xg_available` says whether a team's history has
  any.
- **Windows:** 10/25/40 games, per game (not per 60), all situations and
  even strength, over the team's rating-feeding games this season and last.
  Share is for ÷ (for + against) summed over the window. **No shrinkage,
  by design:** each window stores how many games it held (`corsi_n_*`,
  `xg_n_*`, `corsi_n_season`), and shrinking toward the league mean is a
  step 6 option.
- **League Corsi is 89–96 per game in every season, with no break at 2023**,
  and mean shares sit at 0.500 ± 0.002, except 2027.
- **2027's mean share dips to 0.493 (25-game) and 0.494 (40-game).** Not a
  counting error: for and against mirror exactly, and over 2027 games alone
  the 17-team mean is 0.5000. Early in the season those windows reach back
  into 2026 for 16 teams but not for Jokerit (promoted, no 2026 games), so
  the shares no longer net to 0.5. It fades as 2027 fills the windows.
- Tests: 232. `tests/features/test_shots.py` has truncation and poison
  tests on three games plus the rules above. The fixture is now 2.7 MB: it
  holds `shot_events` (five columns kept) for 2027 and for 2025 from
  2025-01-22 to 2025-02-23, into the 2025 gap.

## Status (as of 2026-11-07)

**Feature store phase F2a is built: starters and goalie quality (Family 1).**
Plan `docs/FEATURE_STORE_PLAN.md` (D4: Corsi only, decided after the F2
pre-check). F2b (shot quality) is next.

- **Rebuild:** `python -m hockey_edge.features.build`, about 23 s at F2a (was
  about 6 s before F2a; about 45 s since F2b). The build's own connection uses a 256 MB page
  cache (`build.CACHE_KIB`); nothing else does. `FEATURE_VERSION` is `f2a`.
- **New tables:** `game_goalies` (26,875: one row per goalie per game,
  shots, goals, actual starter, whole game, opponent xG), `game_starters`
  (12,938: actual, snapshot and resolved starter per team per competitive
  game), `features_goalie` (12,938: one row per team per game, not
  home/away columns). Column meanings are in `features/db.py`.
- **Code:** `features/starters.py` (`derive_actual_starter`,
  `resolve_starter`; `scripts/verify_starters.py` now imports it, output
  byte-identical), `features/goalie.py`, and
  `python -m hockey_edge.features.goalie_report` for the skew report and
  starter source per season.
- **Resolver:** pre-game snapshot, else the actual starter (ended games,
  training only), else `unknown`, which uses the team's expected-goalie
  fallback (start-weighted mix over its last 10 games with a known
  starter, this season or last). The fallback is stored for every row.
  `starter_source` is an audit column, never a model input.
- **Goalie quality:** save% over 500- and 1,500-shot windows (this season
  and the three before it), shrunk toward the league rate with k = 1,000
  shots; GSAx-style mean over the last 20 whole games with opponent xG.
- **`history_before` takes `per_game=`** for derived per-game tables in
  `features.db` (only `game_goalies` for now); anything else raises.
- **Two accepted departures:** (1) the early-change check ignored the
  starter's own opening goalkeeper event, which 2015–2024 log at
  `begin_time` 0, so 786 team-games (468 in 2024) were wrongly
  `flagged_early_change`; now only a different goalie in the first 300 s
  counts. (2) `whole_game` is the only goalie of his team who faced a shot,
  not ice time, since goalie ice time is 0 for all of 2026.
- **Skew, 2027:** 1 mismatch in 250 team-games with both a snapshot and a
  clean actual starter (2701312 TPS away). Fallback use: 10–23 team-games
  a season, except 2016 (113: 51 games have no goalie stats) and 2025
  (189: 89 games have zero goalie shots). See `docs/DATA_PIPELINE.md`.
- Tests: 221, `python -m unittest discover -s tests`. The fixture now
  holds goalie stats and goalkeeper events for 2025/2027 and every 2027
  lineup snapshot (payloads blanked).

## Status (as of 2026-10-16)

**Pipeline safety is done** (`docs/PIPELINE_SAFETY_PLAN.md`): a weekly
verified backup (S1) and healthchecks.io alerts (S2), both live and
confirmed working. No build work; F2 is next.

- **Backup:** `scripts/backup_data.py`, weekly task "hockey-edge backup",
  Sunday 11:05, `StartWhenAvailable` (catches up after a day the PC was
  off; confirmed 2026-10-19) and `WakeToRun`. Zips `hockey.db` and
  `snapshots.db` (SQLite online backup, never a file copy), `data/raw/` and
  `data/recovery/`; about 196 MB, verified before it reaches the folder,
  keeps 4. Folder `D:\Users\Nikla_000\DATA\hockey-edge-backups` (second
  HDD, synced to Google Drive). Restore steps in `docs/BACKUP.md`.
- **Alerts:** `src/hockey_edge/ops/heartbeat.py`, four healthchecks.io
  checks in `Europe/Helsinki`: `snapshot-job` (every tick, 45 min grace),
  `missed-windows` (fail ping listing newly missed windows), `nightly-sync`
  (daily 23:00, 2 h grace), `backup` (Sunday, 1 day grace). Email on
  failure or silence, green email on recovery. **Ping URLs live in `.env`
  and are secrets**; the code never logs them. A ping can never stop a job.
- `snapshot-job` alerts on any day the PC is off during 11:00–23:00, game
  day or not. Accepted noise; pause the check in the web UI when away on
  purpose.
- Alerts shorten a gap from days to hours. Only an always-on machine
  prevents one (open decision: moving scheduling to the i5 Linux box).
- All three tasks (snapshot job, nightly sync, backup) run `pythonw.exe`.
- Tests: 203, `python -m unittest discover -s tests`.

## Status (as of 2026-10-13)

**Build step 5 is done: the Elo baseline and the walk-forward harness.**
Plan `docs/ELO_HARNESS_PLAN.md` (E0-E3), results `docs/ELO_RESULTS.md`.
Next is the design chat, then F2 for step 6.

- **Headline:** Elo beats base rates by 0.0316 +/- 0.0039 log loss on pooled
  2018-2026 (4,075 regular-season games, walk-forward). It beats a naive
  goal-difference rating by only 0.0028 +/- 0.0014. Pinnacle's closing line
  beats Elo by 0.0493 +/- 0.0204 on the 49 games of 2027 that have one;
  about 816 games would be needed to pin a 0.01 gap.
- **Commands:**
  `python -m hockey_edge.features.build`, then
  `python -m hockey_edge.validation.run --models elo,base_rate,gd_rating`
  (about 8 s), then `python -m hockey_edge.validation.report --run latest`.
  `scripts/elo_smoke.py` is a quick sanity pass with default parameters.
- **Code:** `src/hockey_edge/models/` (`elo.py`, `link.py`, `baselines.py`,
  `versions.py`) and `src/hockey_edge/validation/` (`folds.py`,
  `harness.py`, `metrics.py`, `market.py`, `report.py`, `run.py`).
- **`data/backtest.db` is not the prediction log.** It is gitignored,
  rebuildable derived output; runs are append-only by `run_id`. The
  immutable log is step 7.
- **`elo-1` is tagged** in `models/versions.py`: K 10, HFA 0, r 0.5, margin
  multiplier on, `s_ot` 0.15, tuned on 2016-2026. Step 7 logs under it.
- **The home edge is fixed at 0, not tuned.** The ordered-logit link's
  cutpoints carry it, so the tuning loss cannot identify HFA. Don't put it
  back in the grid.
- **Elo works one UTC date at a time**: predict every game on a date, then
  apply that date's results. Same cut as `features/history.py`, and a test
  checks the two agree game by game. The pass stops if a team appears twice
  on one date.
- **2022:49298 no longer feeds ratings** (sudden-death tiebreak; reason in
  `game_spine.ratings_exclusion_reason`). `FEATURE_VERSION` is `f1.1`.
- **`docs/ELO_RESULTS.md` is generated below its marker line**; the summary
  above it is hand-written and kept. Regenerating does not update the
  summary.
- **Only `validation/market.py` reads odds.** A test checks the models and
  the harness never reference `benchmark_odds` or `odds_snapshots`.
- **Open:** the cold-start rule shows no measurable gain over a flat entry;
  2025 was a weak season for every results-only rating; PLAYOFFS
  predictions are stored but not evaluated.
- **Snapshot gap:** no odds or confirmed lineups exist for the 13 games of
  2026-10-08 to 10-10 (the job did not run). Odds rows are arriving again
  as of 2026-10-13; the first closing windows since the gap are 2026-10-14.
- Tests: 173, `python -m unittest discover -s tests`. Model and harness
  tests are in `tests/models/` and `tests/validation/`, on the same fixture.
  `numpy` and `scipy` are now in `requirements.txt`.

## Status (as of 2026-10-12)

**Feature store phase F1 is built**: `src/hockey_edge/features/`, output
`data/features.db` (gitignored, derived). It is enough to start build step
5 (Elo + walk-forward harness). F2 and F3 are open.

- **Rebuild:** `python -m hockey_edge.features.build`. Full rebuild every
  time (about 6 s then; about 45 s since F2b), built in a temp file and swapped in. `hockey.db` and
  `snapshots.db` are attached read-only.
- **Tables:** `game_spine` (all 7,331 games, flags and the regulation 1X2
  target), `season_format`, `team_season`, `features_home`,
  `features_schedule`, `benchmark_odds`, `build_meta`. Column meanings are
  in the DDL comments in `features/db.py`.
- **History is cut by UTC date, not by start time.** `history_before`
  returns ended games from an earlier date. The plan said "started
  earlier", but a game that started 90 minutes before is still in play at
  puck drop, and `games.end_utc` is unusable (null for 2025, before the
  start in some games). In a busy season over 100 competitive games have
  another one starting up to three hours earlier.
- **Feature modules read games only through `features/history.py`**, and
  nothing model-facing reads odds. Both are enforced by tests.
- **`season_format.py` is hand-curated and checked on every build.** A new
  season, a new team or a wrong team count stops the build. The 2026
  relegation format is left NULL: that season has no PLAYOUT or
  QUALIFICATIONS games and the docs don't say why.
- **`benchmark_odds` has 57 games**, all Pinnacle. It takes Pinnacle's last
  parsed row before the start even when that row is old, so filter on
  `minutes_before_start`: 8 rows are more than an hour out, 5 of them a day
  or two (the snapshot job did not run 2026-10-08 to 10-10).
- Tests: 99. The feature tests are in `tests/features/` and run on
  `fixtures/features/mini.sql`, real rows written by
  `scripts/make_feature_fixture.py`.

## Status (as of 2026-09-25, second session)

**`resync.py` now has a value guard for `game_stats`, so the nightly sync
can no longer apply a stripped refetch.** Next is still the Liiga feature
store (step 4).

- **Value guard.** A `game_stats` reparse that passes the row counts is
  still refused (`value_guarded`, rows restored exactly) if a per-game total
  above zero falls below 50%, or if a goal sum moves further from the final
  score. The totals checked are player/goalie time on ice, corsi-for,
  faceoffs, and team faceoff wins and power-play/shorthanded instances.
  Backtest: it refuses 3,767 of the 3,953 stripped reparses (the other 186
  had nothing stored to lose) and none of 44 real live 2027 refetches,
  where corrections dropped time on ice by up to 5.3% and corsi by up to
  12%. Details: `docs/RESYNC.md` 4c.
- **2025 and 2026 `game_team_period_stats` were already stripped when
  fetched (2026-08-22).** Every ended game has 0 power-play/shorthanded
  instances, and team-period goals fall short of the final score in 893 of
  1,192. Player/goalie period stats are fine. No clean copy exists. The
  feature store takes goals from the final score and goal events, and power
  plays from penalty events, for those seasons and the seven 2027
  opening-night games. See `docs/DATA_PIPELINE.md` data gaps.
- **Season 2027 period stats are healthy:** all 52 ended regular-season
  games have real time on ice and corsi.
- **Nightly sync now runs at 23:00** (was 23:30), together with the
  snapshot job's last tick. They share no database.
- Tests: 67, `python -m unittest discover -s tests`.

## Status (as of 2026-09-25)

**The historical recovery is finished.** The `game_stats` pass ran, damaged
data without the guard noticing, and was repaired from the pre-run backup.
Net result: 2015–2024 period stats are exactly as before, and 2023/2024
gained puck control. Full record: `docs/RECOVERY_BACKLOG.md` section 2
results. Next in the build order is the Liiga feature store (step 4).

- **liiga.fi now serves 2015–2024 `game_stats` with the period stats
  stripped.** The row counts are the same, but player time on ice and corsi
  are 0, faceoffs are null, power-play/shorthanded lists are empty, and some
  period goals are missing. `puckStats` gained periods. The run
  (`resync.py --targets`, 5,512 requests, 0 fetch failures) reparsed 3,953
  games. **The no-shrink guard compares row counts only, so it let every one
  of them through.** It refused the other 1,553, which also had rows missing.
- **Repaired with `scripts/repair_game_stats.py` (approved, no HTTP).** It
  restored the three period-stats tables for the reparsed games exactly from
  `data/backups/hockey_pre_gamestats.db` (verified table-for-table, ids
  included). It then added only puck-control periods that carry a value and
  whose game's stored rows appear unchanged in the new response. That was
  1,934 rows, all in 2023/2024, and no existing row changed. The state before
  the repair is kept in `data/backups/hockey_post_gamestats_run.db`.
- **Puck control has values only from 2023 on.** 2015–2022 responses carry
  all-null placeholder rows, before and after, so nothing was recoverable
  there (the old "~11,000 recoverable rows" was row counts, not data). Games
  with 3 valued periods: 2023 488/562, 2024 479/561, on par with 2025's
  528/614.
- **Puck-control seconds are cumulative in 2023–2026 and per-period in
  2027** (see `docs/DATA_PIPELINE.md`).
- **`recovery_report.py --diff` now checks value sums (section 8)**, not just
  row counts. Section 7 counts only puck-control rows that carry a value.
- **Open: the guard's blind spot applies to the nightly `resync --days 7`**
  too. It refetches `game_stats` for live games through the same row-count
  guard. 2025–2027 are unaffected today, but nothing would catch a stripped
  refetch. A value-level check for `game_stats` in `resync.py` is the fix;
  not built (it changes the reviewed guard, so it needs a decision).
  **[Built later on 2026-09-25 as the value guard. "2025–2027 are
  unaffected" was wrong for 2025/2026 team stats; see the second-session
  Status entry.]**
- Tests: 57 at the time (now 67).

## Status (as of 2026-09-24)

**The historical `game_detail` recovery is done**, including an approved
grow-from-zero salvage. Results are in `docs/RECOVERY_BACKLOG.md`, and the
data gaps the feature store must respect are in `docs/DATA_PIPELINE.md`.
The build order has changed: NHL ingest is deferred behind the recovery
and the Liiga feature store (plan: design chat, 2026-09-24).

- **Zero-penalty RUNKOSARJA games in 2015–2024 went 665 → 6.** The fetch
  was `resync.py --targets` (new; exact `season,game_id` lists from
  `scripts/recovery_targets.py`): 1,135 requests, 1,086 reparsed, 49
  refused by the guard, no failures. `game_goal_events` is unchanged
  everywhere, and no guarded table shrank anywhere
  (`scripts/recovery_report.py --diff`).
- **The guard is all-or-nothing per game.** 32 refused games had penalties
  in their refetch but lost 1–5 `game_rosters` rows, so the guard reverted
  everything, penalties included. `resync.py --salvage-from-raw` (approved
  exception, no HTTP) re-applied only the tables that had been at zero:
  304 penalty events and 37 goalkeeper events, with rosters untouched.
  Every salvaged penalty's player is in that game's roster.
- **7 competitive games still have zero penalties** at liiga.fi itself (6
  RUNKOSARJA plus 2022:49298 PLAYOFFS). 2021:480 is damaged at source (a
  0–1 final with no goal events). **The feature store treats zero-penalty
  competitive games as missing data.**
- **2015 is not partially damaged (checked 2026-09-24).** A guarded
  refetch of all 793 never-zero 2015/2016 games returned identical penalty
  counts. The 10.5-vs-8.0 per-game gap comes from liiga.fi recording some
  games with extra misconduct and untyped penalty entries. **Count minors,
  not raw penalty events, for power-play features.**
- **Still deferred: the `game_stats` puck-control pass.** Its filter selects
  5,512 of 5,515 games, so it's effectively the full ≈2.3 h sweep.
  **[Run 2026-09-25; see the 2026-09-25 Status entry.]**
- Tests: 48, `python -m unittest discover -s tests`.

## Status (as of 2026-09-18)

Odds capture has now run live through one evening and one morning. **Phase 4
(a full game night end to end) is still open**: 2026-09-18 is the first, with
3 games (18:30/19:30 local, closing windows 18:05/19:05).

- **Both books have returned 200 on every poll since the 20 s book spacing
  went in** (22:30, and 11:00 the next day). The 429 was the burst, not
  bet365.
- **The retry gap works live**: the 22:45 and 23:00 ticks held off a
  still-unposted game without spending a request.
- **Books post independently, and Pinnacle can be late**: Pinnacle listed no
  KooKoo–SaiPa on either 2026-09-17 poll while bet365 did, then posted it at
  11:00 the next day. Hence the any-book satisfaction change
  (`capture_windows.satisfied_by`); that window ended up `pinnacle,bet365`.
- **Pinnacle can mark its whole book inactive on a fixture**
  (`bookmakerIsActive: false`, prices still present): 5 of 8 fixtures at the
  11:00 poll. Those rows are stored unparsed with the reason logged and the
  raw payload kept — deliberate, since an inactive book's prices aren't live.
  Expect ~10 WARNING lines per poll when it happens; bet365 still satisfies
  the window. Not yet known whether it's time-of-day, pre-lineup, or random —
  tonight's closing windows are the thing to check.
- **All 17 teams mapped** (HPK 3837 appeared on the 22:30 board).
- **Two request counts — don't conflate them.** `api_usage` (the job's own
  ceiling counter) read 6 at noon on 2026-09-18. The OddsPapi account had
  ~11 for September: 3 planning recon + 2 Phase 0 + 6 job. The 50 between
  the job's 200 ceiling and the 250 tier is what absorbs manual calls.
- **Next**: check tonight's `odds capture:` log lines (satisfied, not
  MISSED) and how often Pinnacle comes back inactive near puck drop. Then
  the build order resumes at step 3 (NHL ingest) or step 4 (feature store);
  `docs/RECOVERY_BACKLOG.md` is still unrun.

## Status (as of 2026-09-17)

**Liiga odds capture is live.** Plan and per-phase record: `docs/ODDS_PLAN.md`.
Phases 0–3 done; Phase 4 (watch a full game night) is the only open item.

- **`/odds-by-tournaments` does carry prices in-season** — the 2026-08-23
  "metadata only" finding was the off-season board being too early, not the
  endpoint. `docs/SNAPSHOT_FINDINGS.md` now carries a superseded banner; its
  Shape A/B budget analysis is moot.
- **Books: `pinnacle` (primary) + `bet365`.** Phase 0 spent exactly 2 requests
  comparing candidates: bet365 clean on every board fixture (overround
  1.059–1.079), **betsson rejected** (2 fixtures, one with no odds, one
  suspended). Boards for all three are in `fixtures/oddspapi/`. A window is
  satisfied when **any** book priced the game and it resolved to a liiga.fi
  game, and `capture_windows.satisfied_by` records which (`pinnacle,bet365`,
  or `bet365` alone). Changed 2026-09-18 from pinnacle-only: books post
  fixtures independently, and Pinnacle had no KooKoo–SaiPa on the
  2026-09-17 polls while bet365 did (it posted it only the next morning) —
  pinnacle-only would record a game we had good odds for as missed whenever
  Pinnacle posts late or never. A bet365-only capture logs a WARNING, since it lacks the
  benchmark line. A game on no board stays pending.
- **`capture_windows` now has a `kind` column** ('odds'/'lineups') — a shared
  status meant a successful odds poll would have silently stopped lineup
  capture. Migration ran on `data/snapshots.db` (backup:
  `data/snapshots.pre-kind-migration.db`); all 117 pre-existing rows are
  lineup rows, unchanged. `get_due_windows` requires `kind=`.
- **`odds_snapshots` gained `season`/`game_id`/participant ids/`start_utc`.**
  Games are matched by liiga.fi home+away `teamId` (read from
  `discovered_fixtures.raw_payload`) plus a start within 24h, and only when
  exactly one game matches — never guessed. The map is curated in
  `snapshot/odds/oddspapi_teams.json`: **all 17 teams**, each entry backed
  by a real board fixture. The last three (HIFK 3839, Kärpät 3835, HPK 3837)
  were deliberately left unmapped until each appeared on a board the job
  polled — OddsPapi lists 2–3 ids per Finnish club name, so a guess could map
  a junior/women's side. All three showed up the same evening and matched
  exactly one real game each. **Any new team (promotion) follows the same
  rule**: wait for its first board, never guess from the participants list.
- **First live capture (2026-09-17 21:30 local): 6 fixtures, 12 rows, all
  parsed**, 4 resolved (the 2 unresolved were HIFK/Kärpät, mapped right after
  from those very payloads; their already-written rows keep `game_id` NULL —
  the table is append-only).
- **Tests exist now: `tests/`, 31 of them, stdlib unittest, no new dependency.**
  Run `python -m unittest discover -s tests`. They run against the saved real
  board responses.
- **Open**: a full game night watched end to end; the
  `docs/RECOVERY_BACKLOG.md` 1,164-game recovery (untouched, unrelated).

## Status (as of 2026-09-02)

Step 2 is **deployed and capturing lineups**. The odds question is the one
big thing still open, unchanged and still the user's call.

- **The pre-puck-drop lineup question is ANSWERED: liiga.fi publishes a
  confirmed lineup, but only close to game time.** A T-30 probe across the
  full 2026-09-01 opening slate (7 games, `scripts/lineup_probe.py --date
  2026-09-01 --label T-30`) found `line` non-null for **exactly 22 players
  per team-side (20 skaters + 2 goalies), on all 14 team-sides, zero
  exceptions** — versus `line=null` for every player on the same games at
  T-9 days. This retires the long-standing "does game_detail ever narrow?"
  blocker and means veikkaus.fi is not needed as a lineup fallback.
- **Starting goalie: no literal field exists, but `line == 1` identifies it,
  and that is now measured, not assumed.** Every field on every goalie
  object was dumped — nothing says "starter". What is real: `line` (the same
  depth-chart field used for forward lines/D-pairs) is populated for goalies
  and splits exactly one at `line=1` and one at `line=2` per team-side.
  Scored against real results once season 2027 was ingested: **`line==1` went
  14/14 (100%); the competing "first goalie in array order" candidate went
  7/14 (50%)** — array order carries no signal, exactly as its 7/14
  structural split against `line` predicted. An earlier manual Flashscore
  check that appeared to validate array order 7/7 was drawn from the subset
  where the two coincide. Full analysis + caveats (n=14, one night) in
  `docs/SNAPSHOT_FINDINGS.md`.
- **`snapshot/lineups.py` is implemented** (was stubbed) and wired into
  `job.py`'s `capture_lineups`: one `game_detail`(+`game_preview`) fetch per
  due `(season, game_id)`, one append-only `lineup_snapshots` row per
  team-side, full raw payload retained regardless of parse outcome.
  `lineup_snapshots`' schema was replaced (the old stub had never had a row
  written to it, so no migration was needed) and is now keyed
  `(league, season, game_id, team_role)` per capture. Every starter is
  stored with `starter_source='goalie_line_value'` and
  `starter_confidence='inferred_structural'` so downstream feature code can
  see the basis rather than treating it as fact.
- **`scripts/verify_starters.py` (new, read-only both DBs) turns that
  inference into something monitored.** GOTCHA it surfaced:
  `game_goalkeeper_events` **cannot** identify a starter — zero rows anywhere
  have `begin_time=0`, and 36% of season-2026 games have no rows in it at
  all; it only logs mid-game subs/empty-net pulls. Ground truth is derived
  from `game_goalie_period_stats` (period-1 `shots_on_goal > 0`) instead,
  with early-substitution cases flagged rather than silently resolved.
- **Season 2027 is now ingested** — 595 fixtures (544 RUNKOSARJA / 41
  PRACTICE / 10 PITSITURNAUS), 58 ended at ingest time, zero failures across
  every endpoint. The 7 completed regular-season games have full per-game
  data (rosters, events, period stats, shots, xG). This closes the
  "hockey.db has zero rows for season 2027" gap from 2026-08-23.
- **`scripts/nightly_sync.py` (new) must run daily while the season is on.**
  Nothing else keeps hockey.db current: the snapshot job writes only to
  snapshots.db, and backfill.py was a one-time-per-historical-season pass.
  It runs backfill (season-level endpoints forced, `--only-ended`) then
  `resync --days 7`. **Not scheduled** — that's a manual step. See
  `docs/RESYNC.md`'s nightly-sync section.
- **Snapshot job is registered as a Windows scheduled task (2026-09-02)**,
  every 15 min, **11:00–23:00 local** — not 14:00, see Gotchas.
  `docs/snapshot_job_task_scheduler.xml` now has absolute paths, no
  execution time limit, `IgnoreNew` for overlap, and the working PowerShell 7
  registration command.
- **Not done / still open**: the odds provider decision (unchanged — still
  `NullOddsProvider`, still the user's call); `lineup_snapshots` has **zero
  rows** so far, because the 2026-09-01 T-30 capture was a manual probe run
  (writes to `fixtures/`, never to a DB) and the job hasn't yet had a due
  window for a real slate — whether to inject that probe data has
  provenance implications and was deliberately left to the user;
  `docs/RECOVERY_BACKLOG.md`'s 1,164-game recovery still not run.

## Status (as of 2026-08-25)

Session mid-flight, not a finished milestone — Phase 3 (real odds parsing,
lineup capture) and the bulk of Phase 4 (actually running the recovery
below) are explicitly deferred, not done. What did ship:

- **Snapshot job (step 2) is substantially built, but still captures nothing
  real.** `src/hockey_edge/snapshot/` now has live fixture discovery
  (`fixtures.py`, polls `games_by_date`), a per-`(season, game_id, window)`
  capture-window state table with sleep-safe due/missed logic (verified: a
  simulated machine-asleep wake-up correctly fires overdue windows instead
  of skipping them, and correctly waits for a not-yet-due closing window),
  an `api_usage` table with a monthly ceiling, and `job.py --dry-run`/
  `--once` modes. **The odds provider wired in is `NullOddsProvider` — zero
  HTTP requests, by design, not `OddsPapiProvider`.** Reason: a live
  `/odds-by-tournaments` call (2026-08-23, request 4/8 that session) showed
  it returns fixture metadata only, no `bookmakerOdds`/prices, contradicting
  OddsPapi's own docs — so the entire per-fixture-vs-per-tournament budget
  math this design rested on is unresolved (see
  `docs/SNAPSHOT_FINDINGS.md`'s Shape A/B analysis: same-tier-safe if the
  docs are eventually right, ~225-300 req/month over the 250 tier if not).
  Which provider/cadence is primary is explicitly the user's decision, not
  made this session. Windows Task Scheduler XML written and is what's
  actually being tested (`docs/snapshot_job_task_scheduler.xml`); launchd/
  cron are scaffolding only.
- **liiga.fi's `game_detail` responses can change in EITHER direction on
  refetch — not just retroactive enrichment.** The 2026-08-23 CRITICAL
  FINDING below only tested `game_stats` (which does only ever gain data).
  **[Wrong for `game_stats` too: 2026-09-25 found historical responses
  with period stats stripped. See the 2026-09-25 Status entry.]**
  `game_detail` can retroactively *lose* real event data
  (`goalKeeperEvents`/`goalKeeperChanges`) on a refetch of the same
  completed game; `shotmap` only ever corrects a stat in place. See
  Gotchas for the corrected framing.
- **The no-shrink guard (`src/hockey_edge/ingest/liiga/resync.py`) is the
  mechanism that makes refetching safe again.** It reparses a refetched
  response into curated tables only if doing so would not leave any
  `(game_id, season)`-scoped table with fewer rows than it already has —
  compared per-table, not per-team (an event legitimately moving between
  the home/away arrays must not trip it). A would-shrink reparse is skipped,
  logged at WARNING with before/after counts, existing curated rows are
  left untouched, and the raw response is still saved to disk regardless
  (nothing is ever lost, even when a reparse is refused). Verified
  end-to-end against a scratch copy of `hockey.db`, never against the real
  file. `backfill.py --force` still has no such guard — it does a blind
  delete-and-reinsert, unchanged this session except for a new `--endpoints`
  flag (4b: scopes which endpoints `--force` actually applies to, so a
  targeted recovery doesn't triple its own traffic refetching endpoints
  that don't need it).
- **Found a real, bounded liiga.fi-side incident: an ~34-hour bad-response
  window, 2026-07-19T19:55 UTC through 2026-07-21T05:53 UTC, during the
  original 10-season backfill run.** liiga.fi intermittently served
  `game_detail` responses missing real penalty/goalkeeper-event data during
  that window — 1,164 games across all 10 seasons 2015–2024 (an earlier
  version of this line said 8 and omitted 2018 and 2022, which have 35 and 72) have zero `game_penalty_events` despite a `success` sync_state row,
  in two distinct symptom shapes (missing the whole `game` JSON key vs. a
  `game` key present with empty event arrays) — both confirmed independently
  recoverable via a live refetch. Never recurred outside that window
  (2025/2026's own small zero-penalty counts were fetched 2026-08-22, a
  confirmed-clean run, and are more likely genuine than broken). Full
  per-season/per-symptom-class breakdown, the exact recovery commands (via
  the guarded `resync.py` path, not the unguarded `backfill.py` one — see
  Gotchas), and cost estimates are in **`docs/RECOVERY_BACKLOG.md`**.
  **[Run 2026-09-24; see the 2026-09-24 Status entry.]** One open question logged there
  with no proposed mechanism: 45 `PLAYOFFS` games fetched in the exact same
  78-minute sub-window that broke 450 `RUNKOSARJA` games came through 100%
  clean — phase seems to matter, timing alone doesn't explain it.
- **`data/hockey.db` has zero rows for season=2027 (the live 2026-27
  season)** — confirmed 2026-08-23, contradicting an earlier (design-chat)
  belief that it was already backfilled. **[Fixed 2026-09-01: season 2027 is
  now ingested, 595 fixtures. See the 2026-09-02 Status entry.]** The live API has it (544
  `RUNKOSARJA` + 52 preseason fixtures, confirmed by direct fetch, matching
  the 17-team/64-game structural change) — it was just never ingested. Not
  a blocker for the snapshot job (which reads live, not `hockey.db`, by
  design) but a real gap if anyone assumes `hockey.db` is current for this
  season. Backfilling it is out of scope for this session, deliberately.
- **The pre-puck-drop lineup question is still open.** `game_detail`/
  `game_preview` return a full extended squad (37/33 players, 4 goalies
  each) with no `line` assignment and no way to identify a confirmed
  starter, tested from T-9 days down to T-25 hours — worse, this doesn't
  even resolve after the game ends for a historical sample, so identifying
  the actual starter for training data needs a different source
  (`game_goalkeeper_events`/ice-time, inherently post-hoc). `scripts/
  lineup_probe.py` (new, read-only, zero DB writes) is built for exactly
  this test and works. **Two scheduled one-shot checks are pending for
  2026-08-25 at T-90min and T-30min before season=2027 game_id=2701831's
  puck drop** (17:02 and 18:03 local) — results not yet known as of this
  writeup; if the session that scheduled them isn't alive when they fire,
  they silently don't happen (session-scoped cron, not persisted to disk).
  Check `fixtures/liiga/lineup_probe/` and `docs/SNAPSHOT_FINDINGS.md` for
  whether they landed.
  **[Resolved 2026-09-01 — see the 2026-09-02 Status entry at the top: the
  question was settled by a T-30 probe of the full 2026-09-01 slate instead.
  liiga.fi does publish a confirmed lineup, ~30 min out. Nothing to chase
  here.]**

## Status (as of 2026-08-23)
- **Backfill extended to seasons 2025 and 2026 (the 2024-25 and 2025-26
  seasons) — `data/hockey.db` now covers 12 seasons, 2015–2026, 6,736 games
  total.** Confirmed liiga.fi's season-numbering convention first
  (`scripts/season_probe.py`, read-only, modelled on
  `scripts/oddspapi_probe.py`): it's **ending-year** — `season=2024` covers
  Sep 2023–Apr 2024. Also confirmed **season=2027 is the upcoming 2026-27
  season** (17 teams, 544 scheduled `RUNKOSARJA` games, matching the
  league's stated expansion — zero games `started` as of the check, so
  correctly left un-backfilled; it belongs to the future snapshot job, not
  historical ingest). Both new seasons ran clean: `python -m
  hockey_edge.ingest.liiga.backfill --season <2026|2025>`, no `--max-games`,
  zero `failed_permanent`/`failed_retryable` rows in either season (first
  time `game_stats` has had a 100%-clean run) — season 2026: 605 games
  (480/60/65 RUNKOSARJA/PLAYOFFS/PRACTICE); season 2025: 614 games
  (480/55/5/5/69 RUNKOSARJA/PLAYOFFS/PLAYOUT/QUALIFICATIONS/PRACTICE).
  Row counts, sync_state, goal-event cross-checks, xG coverage, and NTFS
  spot-checks all verified clean for both — no per-season writeup file yet
  (unlike `docs/BACKFILL_RESULTS.md` for the original 10 seasons); this
  session's chat transcript has the full numbers if that's ever needed.
- **`PLAYOUT`/`QUALIFICATIONS` `serie` values confirmed** (`docs/SCHEMA_DRAFT.md`
  design principle 3): `"PLAYOUT"` and `"QUALIFICATIONS"` exactly, found in
  season 2025 (Pelicans-Jukurit playout, Pelicans-Jokerit qualification, 5
  games each). Also found `tournament=playoffs` returns a superset that
  already includes both — not a 1:1 mapping to `serie`; harmless in
  practice (upsert/`INSERT OR IGNORE` semantics absorb the duplication) but
  worth knowing before assuming tournament-param == phase.
- **Two data-completeness findings from this pass, both documented but not
  acted on** — see Gotchas below and `docs/SCHEMA_DRAFT.md`'s `shot_events`
  section + its 2026-08-23 data-completeness audit table: (1) liiga.fi
  retroactively enriches `game_stats` responses after original fetch —
  confirmed on a season=2024 game, likely affects `game_puck_control` for
  all of 2015-2024 **[2026-09-25: only 2023/2024 had puck-control values to
  gain, and the same refetch stripped the period stats]**; (2) season 2025 has a 79-game `shot_events` gap
  (coordinates only, nothing else affected) clustered in the season's final
  month, unlike any other season's pattern.

## Status (as of 2026-08-22)
- **Docs reconciled 2026-08-22 (no code change).** `docs/SCHEMA_DRAFT.md`
  had been left describing the pre-fix world — bare `game_id PRIMARY KEY`,
  single-column FKs on all nine per-game tables, and the wrong
  "recent-seasons-only" claim about `game_stats`/`shotmap`. Its DDL now
  matches `ingest/db.py`, its corrected assumptions are marked inline, and
  **it is trustworthy again** — the design principles renumbered (the
  composite-key rule is now principle 5; "IDs as the API gives them" moved
  to 6). `README.md` and `docs/DATA_PIPELINE.md`'s storage-shape contract
  also updated for the composite key. The smoke-test section at the bottom
  of `SCHEMA_DRAFT.md` is preserved as dated history, not current state.

## Status (as of 2026-07-21)
- **Step 1 (Liiga ingest): full 10-season backfill (2015–2024) complete and
  verified.** `python -m hockey_edge.ingest.liiga.backfill --season <N>` run
  for every season 2024 down to 2015 — 5,517 games total, all per-game
  endpoints (`game_detail`/`game_stats`/`shotmap`), no `--max-games`
  sampling. Full per-season row counts, sanity checks, goal-event cross-
  checks, and NTFS spot-checks are in `docs/BACKFILL_RESULTS.md` — read it
  before trusting `data/hockey.db` for feature-store work, especially the
  top summary's notes on the two distinct `game_stats` gap mechanisms
  (foreign-opponent friendlies vs. an unresolved Blues-specific gap in
  2015/2016) and the confirmed xG-availability cutoff (absent through 2019,
  partial by 2021, ~100% by 2023).
- **Schema changed mid-backfill: `games`' primary key is now composite
  `(game_id, season)`, not bare `game_id`.** Discovered during the season
  2023 backfill that liiga.fi's `game_id` is **not globally unique across
  seasons** — `RUNKOSARJA`/`PRACTICE` game_ids are small per-season counters
  that get reused (449/450 season-2023 `RUNKOSARJA` game_ids collided with
  season 2024's), silently overwriting `games` rows and causing the
  per-game raw-cache (`sync_state`/`raw_responses`, keyed on bare
  `entity_id=str(game_id)`) to serve one season's cached response under
  another season's game_id. Fixed: `games` PK and every dependent table's
  FK/UNIQUE constraint (`game_rosters`, `game_goal_events`,
  `game_penalty_events`, `game_goalkeeper_events`,
  `game_team_period_stats`, `game_player_period_stats`,
  `game_goalie_period_stats`, `game_puck_control`, `shot_events`) now
  include `season`; `raw_cache` entity_id for `game_detail`/`game_stats`/
  `shotmap` is `f"{season}:{game_id}"`. `PLAYOFFS` game_ids did not collide
  in any season checked (large, apparently-global range) — only
  `RUNKOSARJA`/`PRACTICE` did. Full root-cause writeup, remediation steps,
  and the regression check are in `docs/BACKFILL_RESULTS.md`'s "Fix
  implemented and verified" section — **any future schema/parser work on
  the per-game tables must preserve the composite key**, not silently
  revert to bare `game_id`.
- **Correction to an earlier assumption**: `game_stats`/`shotmap` are
  **not** "recent-seasons-only" as previously believed from a season=2010
  fixture 500ing — both endpoints work fine all the way back to season
  2015 (the oldest season backfilled so far). The real `failed_permanent`
  gaps found (160/5,517 games, 2.9%) are unrelated to season age — see
  `docs/BACKFILL_RESULTS.md`'s top summary.
- **Not yet done**: seasons older than 2015 (untested — `game_stats`/
  `shotmap` behavior further back is unknown), `PLAYOUT`/`QUALIFICATIONS`
  phase confirmation (zero games in either across all 10 seasons checked),
  and the Blues `game_stats` gap root cause (would need a liiga.fi devtools
  capture).
- **Next in build order**: step 2's open blocker (whether `game_detail`
  exposes lineups *before* puck drop) is now answerable — the season is
  live again from October, so a real pre-game fetch can settle it, and
  step 2 is the one that loses data permanently if it keeps slipping.
  Step 4 (feature store) is otherwise unblocked on data: `data/hockey.db`
  holds all 10 seasons.

## Status (historical — as of 2026-07-13)
- **Step 1 (Liiga ingest): schema drafted and ingest machinery built, smoke-tested
  on season 2024.** `docs/SCHEMA_DRAFT.md` documents 14 curated tables (games,
  goal/penalty/goalkeeper events, rosters, players, team/player/goalie period
  stats, puck control, shot events, standings) plus `sync_state`/`raw_responses`,
  derived from the real fixtures — not from assumptions — with rationale for every
  deviation from `docs/DATA_PIPELINE.md`'s guideline shapes. DDL lives in
  `src/hockey_edge/ingest/db.py` (`data/hockey.db`, separate from
  `data/snapshots.db`). `src/hockey_edge/ingest/raw_cache.py` does rate-limited
  (1.5s, normal Chrome UA), resumable fetch-and-cache: raw JSON to
  `data/raw/liiga/<endpoint>/` (gitignored), metadata to `raw_responses`,
  status to `sync_state` — a `status='success'` row skips the network entirely on
  re-run. `src/hockey_edge/ingest/liiga/parsers.py` parses cached JSON into
  curated rows; `backfill.py` orchestrates one season end to end (`python -m
  hockey_edge.ingest.liiga.backfill --season 2024`). Curated tables are
  deleted-and-reinserted per entity on reparse **except `games`**, which is
  upserted in place — see `docs/SCHEMA_DRAFT.md`'s "Rebuild caveat" for why a
  blanket delete broke on the FK from every other per-game table.
- **Smoke test (season=2024, 2026-07-13)**: `games_by_season` (all 5 tournament
  phases) + `standings` fetched in full — 561 games (450 regular + 45 playoffs +
  66 preseason `serie="PRACTICE"`; `playout`/`qualifications` had none this
  season). Per-game endpoints (`game_detail`/`game_stats`/`shotmap`) sampled to
  the season's first 20 games via a new `--max-games` flag, not all 561 — a full
  per-game pass is ~561 × 3 × the 1.5s delay (40+ min), too much real traffic to
  spend before schema review. Verified idempotent (re-run: 0 HTTP requests, ~1.5s)
  and `--force` refetch. Full row counts and two real bugs found+fixed while
  smoke-testing (an FK violation on season reparse; a Windows/NTFS bug where a
  colon in a cache filename silently wrote to a hidden alternate-data-stream
  instead of erroring) are written up in `docs/SCHEMA_DRAFT.md`'s "Smoke test
  results" section — the second bug in particular is worth reading before
  trusting `data/raw/` file listings blindly on Windows.
- **Not yet done**: the real ~10-season backfill (blocked on your schema review,
  per your instruction) and full per-game endpoints for season 2024 beyond the
  20-game sample. `player_info`/`player_list`/`team_info`/`teams_stats`/
  `milestones` are cataloged but have no parser/table yet — deferred, see
  `docs/SCHEMA_DRAFT.md`'s "Deferred" section for why each was skipped.
- **Step 1 (Liiga ingest): endpoint discovery done without devtools.** Fetched
  liiga.fi's HTML + Vite JS bundle directly, grepped it for API call sites, then
  curled candidates (normal UA, rate-limited, no auth) to confirm. All data lives
  under `https://liiga.fi/api/v2`. `src/hockey_edge/ingest/liiga/endpoints.py` now
  has 17 catalog entries, 16 confirmed with real responses + `verified_seasons`,
  covering games (verified back to season=1976, the league's first season, through
  2024), schedule, standings, per-game lineups/rosters, per-game/per-period corsi
  stats, shot coordinates, player bios/rosters/game logs, and team season history.
  One entry (`teams_stats`) is a confirmed *path* from the bundle but every param
  combination tried 500/502'd — flagged in its `notes` field as needing a real
  devtools capture (open the team-stats tab on liiga.fi/en/stats, change a filter,
  grab the request).
- `fixtures/liiga/` now has one real (trimmed) sample response per verified
  endpoint/season — see the directory for the full list.
- **Known gotchas found this pass** (see `notes` on each `Endpoint` for detail):
  `games_by_date` and `team_info` silently ignore the `season` query param;
  `game_stats`/`shotmap` occasionally 500 per-game (thought at the time to be a
  recent-seasons-only cutoff — **corrected by the 2026-07-21 10-season
  backfill: both endpoints work fine back through season 2015; the real
  failures are per-game, not season-wide — see CLAUDE.md's current Status
  section and `docs/BACKFILL_RESULTS.md`**); whether `game_detail`/
  `game_preview` expose lineups *before* puck drop is untested (no games were
  scheduled during the 2026-07 off-season check) — verify against a live pre-game
  fetch before wiring the snapshot job, per the no-leakage rule.
- **OddsPapi billing verified (2026-07-12, confirmed against the dashboard
  counter)**: per HTTP request — one call to `/v4/odds-by-tournaments` bills as a
  single request regardless of how many fixtures/bookmakers it returns (total
  usage across all manual tests + `scripts/oddspapi_probe.py`: 9/250 for the
  month). Liiga's tournamentId is **134** — not `34596` (Auroraliiga, the
  women's league) or `48851` (Hokiliiga, Estonia), both false positives on a
  naive name match. Full writeup in `docs/DATA_PIPELINE.md`'s OddsPapi section,
  including the HTTP 404/`FIXTURE_NOT_FOUND` gotcha for an empty tournament board.
- **Step 2 (snapshot capture job): skeleton built, not deployed.**
  `src/hockey_edge/snapshot/` has the odds provider interface (`odds/base.py`), a
  working `OddsPapiProvider`, a stubbed `VeikkausProvider` fallback, append-only
  SQLite storage (`storage.py` → `data/snapshots.db`), and an orchestrator
  (`job.py`, run manually via `python -m hockey_edge.snapshot.job`) that logs to
  `logs/snapshot_job.log` and alerts at CRITICAL on failure without crashing or
  writing partial data. Lineup capture (`lineups.py`) is stubbed, blocked on the
  same pre-puck-drop `game_detail` question noted above. Odds parsing into
  structured home/draw/away columns is also stubbed (every row currently has
  `parsed=False`) — every call this session hit the empty-board 404 case, so the
  real market/outcome JSON shape is still unconfirmed; implement parsing once a
  live fixture payload can be inspected. Scheduling/deployment placement is still
  open (see Gotchas).
