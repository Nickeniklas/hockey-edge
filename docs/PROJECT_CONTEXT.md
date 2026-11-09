# PROJECT_CONTEXT — hockey-edge (Liiga + NHL prediction & edge tool)

Paste-ready summary for Claude project memory. Crystallized 2026-07-06; rewritten
2026-09-03, 2026-09-20, 2026-09-25, 2026-10-12, 2026-10-14, 2026-11-06, 2026-11-07,
2026-11-09 after F3a/F3b; rewritten 2026-11-09 after F3c/F3d (build step 4 done).

Owned by the design chat. Emitted whole and replaced wholesale at session close. Not
edited by Human or by Claude Code sessions.

## Project

Niklas (IT student, Finland; Python/data/ML skills; Windows RTX 3060 Ti desktop + M1
Mac, plus an unused 5th-gen i5 desktop earmarked as a future Linux host) is building a
**personal-use-first hockey prediction tool for Liiga and NHL**. Core loop: ingest
historical data → capture pre-game info (confirmed lineups, starting goalies,
bookmaker odds) timestamped before puck drop → rolling features → calibrated win
probabilities → compare vs odds to surface edges → immutable, timestamped prediction
log.

It's an **edge finder** (model probability vs bookmaker odds), not a stats site and
not bare predictions. **Primary user is Niklas himself; the public/commercial angle is
effectively dropped.** Treat personal-use as the operating assumption and don't let
"someday public" quietly shape build choices. The only thing the public option still
buys is a reason to keep the prediction log clean, which the invariants already
require anyway.

## Decided stack (don't re-open)

Python, SQLite, local compute, `src/hockey_edge/` package layout, `pyproject.toml` +
`pip install -e .` into a `.venv`. No `PYTHONPATH=src` prefix anywhere. Commands run
from the repo root. `numpy` and `scipy` are dependencies (added 2026-10-12 for the Elo
link fit).

Liiga data from liiga.fi's undocumented JSON API at `https://liiga.fi/api/v2`; 16
endpoints confirmed, catalog and `verified_seasons` in
`src/hockey_edge/ingest/liiga/endpoints.py` (source of truth). NHL from the official
free NHL API; NHL xG bootstrapped from MoneyPuck/Natural Stat Trick.

**Liiga odds: OddsPapi, polling `pinnacle` (primary/benchmark) + `bet365` (fallback).
Settled 2026-09-17, proven live since — do not re-open.** NHL odds via The Odds API
remain a separate, later session.

Models: Elo-style baseline + LightGBM, blended. Targets: NHL binary moneyline; Liiga
three-way regulation 1X2. Metrics: log loss + calibration, never accuracy; benchmark is
odds-implied probabilities (vig removed) from the closing pre-game snapshot. The Liiga
Elo turns its rating gap into 1X2 probabilities with an **ordered-logit link** fitted
per fold (decided 2026-10-12).

Tests: `tests/`, stdlib `unittest`, no pytest — `python -m unittest discover -s tests`
(292 as of 2026-11-09, after F3d; a full run takes 5–8 min, up from ~3 min before
F3c). They assert against real saved data and make zero HTTP requests. The feature
tests run on `fixtures/features/mini.sql` (5.5 MB since F3c), regenerated from the
live databases by `scripts/make_feature_fixture.py`; a nightly resync can renumber row
ids in it with values unchanged, so tests never pin ids. The full suite is
memory-hungry: on 2026-11-09 a run hit `MemoryError` while the machine was loaded with
other apps, then passed cleanly on a rerun.

## Hard invariants

Pre-puck-drop information only (append-only snapshots with `captured_at` UTC);
**immutable prediction log** (corrections go in as new rows under a new
`model_version`); strict walk-forward validation; resumable sync (`sync_state`, raw
JSON cached as files under `data/raw/`, SQLite metadata only); capture-before-parse;
corrections live in derived layers, never in raw; polite scraping (1.5s, normal UA);
no autonomous git commits (an explicit request from Niklas is the exception); no
affiliate integrations.

Composite `(season, game_id)` is the primary key everywhere. `game_id` is a per-season
counter on liiga.fi's side and gets reused.

## Where things stand (2026-11-09)

Build order: 1 Liiga ingest → 2 snapshot capture → 3 NHL ingest (deferred) → 4 feature
store → 5 Elo + walk-forward harness → 6 LightGBM + blend → 7 prediction log + local
dashboard.

- **Step 1 (Liiga ingest): done** for 2015–2027. `data/hockey.db` is kept current by
  `scripts/nightly_sync.py` (daily 23:00: forced season-level endpoints with
  `--only-ended`, then guarded `resync --days 7`). Historical recovery done
  2026-09-25.
- **Step 2 (snapshot capture): live.** `data/snapshots.db`, Windows scheduled task
  every 15 min 11:00–23:00 with `WakeToRun`. Lineups since 2026-09-02, odds since
  2026-09-17. Closing windows land ~T-15. Starter inference (`line == 1`) is about
  98% right; misses look like late changes or wrong published lines.
  **Two permanent gaps, both PC-off days:** nothing captured 2026-10-08 to 10-10 (13
  games, before alerts existed), and on 2026-10-17 the mid and closing windows were
  missed for all 7 games (opening captured; alerts fired as designed). Those games
  have no confirmed lineup and no usable closing line. Any later shutdown on a game
  day is a gap of the same kind.
- **Step 4 (feature store): done 2026-11-09 (F0–F3d).** Plan:
  `docs/FEATURE_STORE_PLAN.md`. Audit: `docs/FEATURE_AUDIT.md`.
  `python -m hockey_edge.features.build` rebuilds `data/features.db` in ~45 s (up to
  74 s under machine load; full rebuild every time; the build's own connection uses a
  256 MB page cache). `FEATURE_VERSION` is `f3d`. F1: game spine with the regulation
  1X2 target and phase flags, `season_format`, `team_season`, home advantage, rest
  days, `benchmark_odds` (2022:49298 dropped from `feeds_ratings`). F2: starters,
  goalie quality, shot attempts and team xG. F3a: penalty team corrected; F3b: special
  teams; F3c: roster availability; F3d: motivation (summaries below).
- **Step 5 (Elo + walk-forward harness): done 2026-10-13.** Plan
  `docs/ELO_HARNESS_PLAN.md` (E0–E3), results `docs/ELO_RESULTS.md`. Summary below.
- **Pipeline safety: done 2026-10-16** (`docs/PIPELINE_SAFETY_PLAN.md`). Weekly
  verified backup and healthchecks.io alerts, both live and confirmed working.
  Summary below. Task triggers fixed for DST on 2026-11-09 (`b17329e`).
- **Steps 6–7: not started.** Step 6 is planned in the design chat first.

Repo: F2a `d42a76f`, F2b `9390aab`, F3a `a41770b`, F3a bench fix `dbc70cf`, F3b
`c17f3f7`, `CLAUDE.md` cleanup `7a5eb7b`, DST trigger fix `b17329e`, F3c `477b761`,
F3d `e5b9aa6`. The GitHub account is fine; the earlier suspension is long over.

## F2 in short

- **Tables:** `game_goalies` (one row per goalie per game), `game_starters` (actual,
  snapshot and resolved starter per team-game), `features_goalie`, `game_team_shots`
  (Corsi for/against, even-strength Corsi, game xG per team-game), `features_shots`.
  **F2/F3 feature tables are one row per team per game**, unlike F1's home/away
  columns; step 6 pivots them.
- **Starter resolver:** pre-game snapshot, else the actual starter (ended games,
  training only), else `unknown` → the team's expected-goalie fallback (start-weighted
  mix of its last 10 known starters, this season or last). The fallback is stored on
  every row so it can be scored. `starter_source` is audit only, never a model input.
  2027 skew: 1 mismatch in 250 team-games.
- **Goalie quality:** save% over 500- and 1,500-shot windows, shrunk toward the league
  rate (k = 1,000 shots); GSAx-style mean over the last 20 whole games with opponent
  xG (from 2021). "Whole game" = the only goalie of his team who faced a shot.
- **Accepted F2a fix:** the actual-starter early-change check counted the starter's own
  opening event (logged 2015–2024) as a change; 786 team-games now resolve. 2027
  output of `verify_starters.py` unchanged.
- **Shots (D4, Corsi only):** every `shot_events` row counts. Windows 10/25/40 games,
  per game, all situations and even strength, this season and last. Gap games are
  NULL and skipped. **No shrinkage by design**; window game counts are stored and
  shrinkage is a step 6 option. League Corsi is 89–96 per game every season, no break
  at 2023.
- 2027's mean Corsi share sits at ~0.493 in the 25/40-game windows because Jokerit's
  windows hold only 2027 games while the others reach into 2026. Fades as 2027 fills.
- `history_before(..., per_game=...)` serves derived per-game tables in `features.db`
  through the same date cut; only listed tables are allowed.

## F3a/F3b in short

- **`penalty_events_resolved`:** player penalties take the player's roster team;
  **bench penalties (`player_id = 0`) take the roster team of the player who served
  them (`sufferer_roster`)**; game direction only as a fallback (85 rows); a player
  on both rosters takes the game's direction. 2 rows unresolved (one 2016 player in
  neither roster). Proof: on penalties cut short by a non-overtime goal, every rule
  is right in every case (player 6,947/6,947, sufferer 326/326, fallback 9/9). The
  first F3a rule (bench = game direction) got 224 of 335 bench cases right and was
  replaced in `dbc70cf`.
- **Real goals** exclude `VT0`/`RL0` events and shootout attempts; then goal events
  match the final score in every game except 2021:480.
- **PP goals from liiga.fi's goal label (`YV`/`YV2`).** It matches stored
  `powerplay_goals` exactly wherever one exists and agrees with penalty timing on
  99.2–99.6% of goals per season.
- **PP opportunities: coincident-adjusted, derived for every season.** Runs ~0.2 per
  team-game below liiga.fi's stored count in every season (by design). League PP%
  18.7–22.2%; 2025/2026 have no stored value to check against.
- `game_team_special_teams` (per team-game counts) and `features_special_teams`
  (10/25/40-game windows, raw counts plus PP%/PK% shrunk with k = 150 opportunities).
  Seven games are NULL: the six zero-penalty games and the 2022:49298 tiebreak.

## F3c/F3d in short

- **Who played = players with a stats row, never `game_rosters`.** `game_rosters` is
  the full squad: about 6 (2015) to 9 (2026) more skaters per team-game than dressed,
  and nothing in it tells them apart. Counting from it would have added phantom
  absences that grow over the seasons. Found in the F3c pre-check.
- **`game_skater_toi`** (228,790 rows): one row per skater per team per ended
  competitive game with a stats row, ice time and F/D. Position from the roster's
  `role`, else from `role_code`'s letter (H/KH → F, P → D); the snapshot's `roleCode`
  is never used. Ice-time gap team-games (total 0, or on the named gap day
  2016-01-05) have NULL ice time and are skipped as history, but a gap game whose
  lineup is known (the 2025 playout games) still gets features as the game being
  predicted.
- **Lineup resolver** mirrors the starter resolver and shares its snapshot choice
  (`features/pregame.py`; `game_starters` came out row-for-row identical): pre-game
  snapshot, else actual (stats rows; training only), else the last known lineup this
  season. `lineup_source` is audit only. **2027: the confirmed lineup equals who played
  in all 254 team-games with both.** A missed lineup window costs a mean 0.029 in
  `missing_share_10` (max 0.166).
- **`features_availability`:** share of the team's ice time over its last 5/10 games
  (with ice time, **this season only**, NULL at a team's first game) held by skaters
  absent from the resolved lineup; also forward and defence splits, window game counts,
  and the last-known-lineup fallback on every row. Weight is ice time because the 10th
  to 90th percentile spread is about 10 to 21 minutes. Mean `missing_share_10` is
  0.12–0.14 per season. This-season-only was chosen so summer departures don't read as
  absences; roster turnover could be its own feature later.
- **`features_motivation`** (RUNKOSARJA only, NULL otherwise): table before the game
  from 3/2/1/0 points (matches liiga.fi for all 198 team-seasons), ordered by points
  then goal difference; `games_remaining` from the nominal schedule (counting listed
  games would leak 2020's and 2021–22's cancellations); signed point gaps to the
  playoff and relegation lines; conservative clinched / eliminated / safe / doomed
  flags. liiga.fi breaks ties on points its own way; that changes no gap value.
- **`season_format` lines (hand-curated, verified against the data):** playoff spots
  10 in 2015–2024 (2020 planned 10, cancelled), 12 from 2025. Relegation: none before
  2025; 2025 bottom 2 to the playout; **2026 none** (Liiga grew to 17 for 2026–27);
  **2027 bottom 3 directly, top 12 to the playoffs** (confirmed from MTV Uutiset's
  season preview, 2026-08-31). An earlier announced 2027 format with a 13–16
  relegation series was dropped before the season.
- Espoo Blues (`237467442:blues`, 2015–2016) and Kiekko-Espoo
  (`1368624751:k-espoo`, from 2025) never share an id in games or any per-game
  table, so Elo carries nothing over. Only liiga.fi's `standings` table files
  Blues' 2015–2016 record under the K-Espoo id; nothing model-facing reads it.

## Pipeline safety in short

- **Backup:** `scripts/backup_data.py`, weekly task "hockey-edge backup", Sunday 11:05,
  `StartWhenAvailable` (catches up after a day the PC was off; confirmed) and
  `WakeToRun`. Zips `hockey.db` and `snapshots.db` (SQLite online backup, never a file
  copy), `data/raw/` and `data/recovery/`; ~196 MB, verified before it reaches the
  folder, keeps 4. Folder `D:\Users\Nikla_000\DATA\hockey-edge-backups` (second HDD,
  synced to Google Drive). Restore steps in `docs/BACKUP.md`.
- **Alerts:** `src/hockey_edge/ops/heartbeat.py`, four healthchecks.io checks in
  `Europe/Helsinki`: `snapshot-job` (every tick, 45 min grace), `missed-windows` (fail
  ping listing newly missed windows), `nightly-sync` (daily 23:00, 2 h grace), `backup`
  (Sunday, 1 day grace). Email on failure or silence, green email on recovery. Ping
  URLs live in `.env` and are secrets; the code never logs them. A ping can never stop
  a job. Confirmed again on 2026-10-17 (up, then down for the evening).
- `snapshot-job` alerts on any day the PC is off during 11:00–23:00, game day or not.
  Accepted noise; pause the check in the web UI when away on purpose.
- Alerts shorten a gap from days to hours. Only an always-on machine prevents one
  (open decision 5).

## Step 5 in short

- **Harness:** `src/hockey_edge/models/` (Elo, link, baselines, `versions.py`) and
  `src/hockey_edge/validation/` (folds, metrics, harness, market, report, CLI).
  Output `data/backtest.db`, gitignored and rebuildable, append-only runs. **Not the
  prediction log.** Step 6 plugs LightGBM and the blend into the same folds and report.
- **Folds:** 2015 burn-in; test seasons 2018–2026 tuned on 2016…n−1; 2027 is the live
  fold. Ratings update online within a season. Predictions are made for a whole UTC
  date, then that date's results are applied.
- **`elo-1`** (2027 fold, tuned on 2016–2026): K 10, HFA 0, carry-over r 0.5, margin
  multiplier on, OT/SO winner score 0.5 ± 0.15, promoted-team K ×2 for 15 games.
  Link c1 −0.7342, c2 0.2740, β 3.0804.
- **HFA is fixed at 0** (decided 2026-10-13). The link's cutpoints carry the home edge,
  so tuning can't identify HFA.
- **Results, pooled 2018–2026, 4,075 regular-season games:** Elo beats base rates by
  0.0316 ± 0.0039 log loss (about 8 SE). It beats a naive goal-difference rating by
  only 0.0028 ± 0.0014. Results alone carry limited signal; the gains for step 6 have
  to come from goalies, lineups, shots and special teams.
- **Market:** on 49 games of 2027 with a Pinnacle closing line, Pinnacle beats Elo by
  0.049 ± 0.020. Expected for a results-only model. The per-game SD of the difference
  is 0.143, so a true gap of 0.01 needs ~816 games to reach 2 SE: about two seasons of
  closing lines.
- **Carried forward:** the promoted-team cold-start rule shows no measurable gain over
  a flat entry (73 games); 2025 was a weak season for every results-only rating;
  PLAYOFFS predictions are stored but not evaluated.

## The finding that changes how to treat liiga.fi

**liiga.fi degrades and rearranges its own historical data.** `game_detail` loses
goalkeeper events and roster rows on refetch and moves penalties between the home and
away arrays; `game_stats` now serves 2015–2024 with period stats stripped; 2025/2026
team stats were stripped before we ever fetched them. The API feeds a website, not an
archive. Consequences:
- **The local copy (`data/hockey.db` + `data/raw/`) is better than the source**, and
  for 2015–2024 period stats it is the only good copy. It is not on GitHub. Since F3c,
  skater ice time from that copy is a model input.
- **Never bulk-refetch history without a backup and a value-level diff**
  (`scripts/recovery_report.py --diff` section 8). A row count proves nothing.
- The value guard covers `game_stats` only. `game_detail` and `shotmap` still rely on
  the row-count guard, so don't bulk-refetch `shotmap` history.
- **Known hole in the `game_stats` value guard:** its totals (including `player_toi`)
  are summed over both teams of a game, refused below 50% of the old value. If one
  team's values are stripped to 0, about half the total remains, right at the floor,
  so the reparse can pass. The nightly `resync --days 7` refetches live 2027 games
  through this guard. Fix is next step 1.

## Data rules the feature store must respect

Full lists: `docs/DATA_PIPELINE.md` → "Known data gaps" and `docs/FEATURE_AUDIT.md`.
The ones that shape design:
- **Regulation result comes from `games.finished_type`**, never from goal-event counts.
- **`game_penalty_events.team_id` is the wrong team in most 2017–2022 and 2024 games**
  (liiga.fi's arrays, per game). The team comes from `penalty_events_resolved` in
  `features.db`. No ingest change.
- **A competitive game with zero penalty events is missing data, not a clean game.**
  Six remain (2016:7862, 2017:4409, 2020:252, 2021:480, 2022:317, 2022:332).
  2021:480 is damaged at source (0–1 final, no goal events).
- **Power plays: coincident-adjusted count on roster-corrected teams**, for every
  season. Never raw penalty-event counts, never `game_team_period_stats` for
  2025/2026 or the seven 2027 opening-night games.
- **Goals from the final score** (`games.home_goals`/`away_goals`), never team-period
  stats.
- **Shot features from `shot_events`**, not liiga.fi's corsi (player corsi steps up
  ~10% in 2023). Even strength from `shot_events.strength_type`. On-ice counts change
  convention in 2025 and are not used. `shooting_team_id` is a bare number while other
  tables use `number:slug`. **Corsi only, no Fenwick (D4, 2026-11-06):** from 2023
  liiga.fi labels ~5.6 attempts per game as blocked that it earlier labelled missed.
  Total attempts are stable across seasons, unblocked attempts are not. Never split
  missed vs blocked in a feature.
- xG: game-level complete from 2021; player-level only from 2025; no per-goalie xG.
- **Goalie ice time is 0 for all of 2026 and partial in 2025** (958 of 1,090 team-games
  have any). 89 competitive 2025 games have goalie rows with zero shots (79 are the
  `shot_events` gap); 51 games in 2016 have no goalie stats.
- **Skater ice time exists in every season** (97–99.5% of skater rows > 0; team totals
  97–98% of five skaters for the whole game). Gaps: 51 games of 2016 (no stats rows at
  all, so no lineup either), all of 2016-01-05, 2021:51700 HPK, and the ten 2025
  PLAYOUT/QUALIFICATIONS games (rows present, ice time 0). Nine other team-games have
  partial ice time (17–78% of a game) and are kept.
- **`game_rosters` is the squad list, not the lineup** (see F3c). It has no `line`
  column; the parser drops it, though raw `game_detail` carries it for some seasons.
- Puck control: values only from 2023. Not a planned feature input.
- Penalty `player_id = 0` is a team/bench penalty, not a player.
- The 2025 Pelicans–Jokerit qualification series and 2022:49298 (a 35-minute
  sudden-death tiebreak) do not feed ratings.
- Season team lists come from `team_season`, never from games seen so far (league
  membership is known before a season starts; deriving it from games leaks).
- Standings are derived from games, never read from liiga.fi's `standings` table.

## Open decisions (mine, not Claude Code's)

1. **Thin odds benchmark.** Closing lines exist only from 2026-09-17 (49 usable games
   at 2026-10-13, plus ~15–20 a week). Step 5 put a number on it: ~816 games, about
   two seasons, before a 0.01 gap is measurable. Until then, model choices are judged
   by walk-forward log loss against the baselines; the market comparison grows in the
   background. A historical Liiga odds session is the only way to shorten that.
2. **PLAYOFFS as a separate evaluation slice** (v1 evaluates RUNKOSARJA only).
3. **In-house location xG** from `shot_events` coordinates — later or never.
4. **Veikkaus as an additional odds source.** Optional; its own session.
5. **When to move scheduling to the i5 Linux box.** The 10-08–10-10 and 10-17 gaps
   show the cost of a desktop that gets shut down. An alert reports a gap; only an
   always-on machine prevents one.

## Immediate next steps, in order

1. **Per-team `game_stats` value guard** (short Claude Code session; ingest code, so
   backup first and a backtest against real corrections, as the original guard had).
   Check each guarded total per team as well as per game, so one team stripped to 0 is
   refused. Matters now because the nightly resync refetches live games and ice time
   is a model input.
2. **Clean-up before step 6:** a full regeneration of `fixtures/features/mini.sql`
   (appended to since F2, 5.5 MB, with F3c slices cut at 2026-11-01), with the tests
   re-pinned in one go. Aim to bring the suite back toward ~3 min and lower its memory
   use.
3. **Plan step 6 in the design chat** (LightGBM + blend), scored by the step 5 harness.
   Carried into it: pivoting the per-team F2/F3 tables; shrinkage for the shot windows
   (none yet); tuning `k` for goalie and special teams; how to use window game counts
   early in a season (availability is NULL at a team's first game, and short after);
   whether motivation features earn their place beyond the last weeks of a season;
   whether the build (~45 s) needs to become incremental.

## Deferred (explicitly, with reasons)

- **NHL ingest** — after the Liiga feature store and Elo. When it comes, the feature
  store's UTC-date history cutoff must be revisited (NHL games cross UTC midnight).
- **`shotmap` historical refetch** — no value guard, and not needed: `strength_type`
  gives even strength directly.
- **Goalkeeper-event recovery** — permanently downgraded; starters come from
  `game_goalie_period_stats`.
- **2016 lineups from raw `line`** — would need a `game_rosters` parse change; those
  51 games have no ice time anyway, and 2016 is tuning-only. Left NULL.
- **Opening-line comparison** — `benchmark_odds` holds closing rows only.
- `player_info`/`player_list`/`team_info`/`teams_stats`/`milestones` — cataloged, no
  parser.
- Totals market, news-article injury parsing, football, payments. Public site.

## Gotchas that cost real time

- **The no-shrink guard counts rows; it cannot see stripped values.** Backed by the
  value guard for `game_stats` only (with the per-team hole above). All-or-nothing per
  game. `--salvage-from-raw` is the one approved exception.
- **A plain "no total may drop" rule is wrong.** Real live corrections drop TOI by up to
  5.3%, corsi by up to 12%, player goals by 1 toward the final score.
- **`backfill.py --force` has no guard at all.** Use `resync.py` for any refetch.
- **`backfill --season <live season>` needs `--only-ended`.**
- **`nightly_sync.py` must force the season-level endpoints**, or `games` freezes.
- **Feature history is cut by UTC date, not start time**, so an earlier same-day game
  still in play can't leak. Fine for Liiga, wrong for NHL. The Elo pass follows the
  same rule.
- **Task triggers must be stored in plain local time, without a UTC offset**, or they
  shift with DST (the nightly sync ran at 22:00 from 2026-10-25 until the 2026-11-09
  fix). Check with `(Get-ScheduledTask -TaskName <name>).Triggers[0].StartBoundary`.
- **OddsPapi rate-limits bursts separately from the monthly quota.** `job.py` sleeps
  20 s between books; no blind retry (a retry is another billed request).
- **OddsPapi returns 404 `FIXTURE_NOT_FOUND`** for a tournament with no posted
  fixtures; don't reintroduce a bare `raise_for_status()`.
- **`bookmaker` is required and single-valued** — cost scales with books.
  `/v4/odds` (all 213 bookmakers, 11.6 MB) is not for polling.
- **A new team's OddsPapi id is never guessed.** Names aren't unique.
- **Pinnacle marks its book inactive far from puck drop** (43% beyond 12 h); closing
  is reliably live. `benchmark_odds` can still hold an early row when the closing
  window was missed — filter on `minutes_before_start` ≤ 60.
- **Task Scheduler XML import is encoding-fragile**; `schtasks` inline can't set a
  working directory (matters for `.env`); editing the snapshot task needs an elevated
  PowerShell, the nightly sync does not.
- **All three tasks run `pythonw.exe`** (snapshot job, nightly sync, backup), so the
  log file and the healthchecks.io ping body are the only record. A failure
  before logging shows only as a non-zero `LastTaskResult` (`Get-ScheduledTaskInfo`).
- **The tasks run from the working checkout.** A half-finished edit to job code during
  11:00–23:00 can break a live capture.
- **Snapshot window is 11:00–23:00**, because some fixtures start at or before 14:00.
- `games_by_date` 502s intermittently and ignores `season`. `tournament=playoffs`
  returns a superset including PLAYOUT and QUALIFICATIONS. `serie` and `roleCode`
  vocabularies are open-ended.
- Playoff formats vary by season: 2022's first round was a two-game aggregate with a
  sudden-death tiebreak (49298). Never assume a bracket shape. Playoff games of the
  live season are listed round by round, so a build check on playoff team counts must
  allow fewer teams than spots until a later season exists.

## Open items

- `homePreviousGames`/`awayPreviousGames` absent from `game_preview` — unresolved.
- Review liiga.fi (and, if used, Veikkaus) ToS before anything public.
- 2027 playoff spots are confirmed by the source but not yet by the data; the build
  check covers it once the 2027 playoffs are listed.

Closed 2026-11-09 (after F3b): F3c and F3d, so build step 4 is done; the 2026
relegation rule (none); the 2027 format (12 playoff spots, bottom 3 relegated
directly); the 2026-10-17 capture gap (PC off, alerts fired); the Espoo Blues /
K-Espoo id question (no mix outside liiga.fi's `standings`); the 2016-01-05 ice-time
day and the NULL-`role` positions; task triggers for DST; the `CLAUDE.md` cleanup.
Earlier on 2026-11-09: the bench-penalty team (sufferer roster, proven against
goal-shortened penalties); why 2016 and 2023 fit the PP derivation worse (the bench
bug); the goal-event surplus (`VT0`/`RL0` are not goals); F3a and F3b.
Closed 2026-11-07: F2 (starters, goalie quality, Corsi, team xG); the
Corsi/Fenwick decision (D4: Corsi only); the early-change bug in the actual-starter
logic. Earlier on 2026-11-06: off-machine backup and failure alerting, both confirmed
in normal use.

## Meta — the pattern worth remembering

Every significant problem in this project has been an **unverified assumption that
failed silently rather than loudly**: `game_id` uniqueness, season numbering, the NTFS
colon bug, `sync_state` blindness to empty-but-200, OddsPapi's docs vs its live
behaviour, the row-count guard passing gutted values, liiga.fi's home/away penalty
arrays swapped in whole seasons. Step 5 added a leak that only a test could see:
building each season's team list from games already played made predictions depend
on future games, and the truncation test caught it. F3c added one more caught in
time: a table called `game_rosters` that sounds like "who played" but is the whole
squad, found only because the pre-check compared it with the stats rows and the
snapshots.

Habits worth keeping: check a value against a second source before trusting it;
backtest a guard against real corrections before relying on it; when something looks
like bad data, test it before excluding it; give every point-in-time computation a
truncation and a poison test; and run a read-only pre-check before building on a
table nobody has measured yet.
