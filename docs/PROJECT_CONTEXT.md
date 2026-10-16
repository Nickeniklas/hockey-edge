# PROJECT_CONTEXT — hockey-edge (Liiga + NHL prediction & edge tool)

Paste-ready summary for Claude project memory. Crystallized 2026-07-06; rewritten
2026-09-03, 2026-09-20, 2026-09-25, 2026-10-12; rewritten 2026-10-14.

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
(173 as of 2026-10-13). They assert against real saved data and make zero HTTP
requests.

## Hard invariants

Pre-puck-drop information only (append-only snapshots with `captured_at` UTC);
**immutable prediction log** (corrections go in as new rows under a new
`model_version`); strict walk-forward validation; resumable sync (`sync_state`, raw
JSON cached as files under `data/raw/`, SQLite metadata only); capture-before-parse;
corrections live in derived layers, never in raw; polite scraping (1.5s, normal UA);
no autonomous git commits; no affiliate integrations.

Composite `(season, game_id)` is the primary key everywhere. `game_id` is a per-season
counter on liiga.fi's side and gets reused.

## Where things stand (2026-10-14)

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
  **It captured nothing 2026-10-08 to 10-10 because the PC was shut down**: the 13
  games then have no confirmed lineup and no usable closing line, permanently.
  Nothing alerted. Odds rows resumed 2026-10-13; 2026-10-14 is the first slate with
  closing windows since the gap.
- **Step 4 (feature store): F0 and F1 done.** Plan: `docs/FEATURE_STORE_PLAN.md`.
  Audit: `docs/FEATURE_AUDIT.md`. `python -m hockey_edge.features.build` rebuilds
  `data/features.db` in ~6 s: game spine with the regulation 1X2 target and phase
  flags, `season_format`, `team_season`, home advantage, rest days, and
  `benchmark_odds`. `FEATURE_VERSION` is `f1.1` (2022:49298 dropped from
  `feeds_ratings`, with `ratings_exclusion_reason`). F2 (goalie, shot quality) and F3
  (penalty-team correction, special teams, roster, motivation) are next, for step 6.
- **Step 5 (Elo + walk-forward harness): done 2026-10-13.** Plan
  `docs/ELO_HARNESS_PLAN.md` (E0–E3), results `docs/ELO_RESULTS.md`. Summary below.

Repo: `origin/main` includes the E3 commit (2026-10-13; E2 was `33332e7`). GitHub
knowledge sync run 2026-10-14.

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
  for 2015–2024 period stats it is the only good copy. It is not on GitHub.
- **Never bulk-refetch history without a backup and a value-level diff**
  (`scripts/recovery_report.py --diff` section 8). A row count proves nothing.
- The value guard covers `game_stats` only. `game_detail` and `shotmap` still rely on
  the row-count guard, so don't bulk-refetch `shotmap` history.

## Data rules the feature store must respect

Full lists: `docs/DATA_PIPELINE.md` → "Known data gaps" and `docs/FEATURE_AUDIT.md`.
The ones that shape design:
- **Regulation result comes from `games.finished_type`**, never from goal-event counts.
- **`game_penalty_events.team_id` is the wrong team in most 2017–2022 and 2024 games**
  (liiga.fi's arrays, per game). The team comes from the penalised player's roster,
  in a derived table in `features.db` (F3). No ingest change.
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
  tables use `number:slug`.
- xG: game-level complete from 2021; player-level only from 2025; no per-goalie xG.
- Puck control: values only from 2023. Not a planned feature input.
- Penalty `player_id = 0` is a team/bench penalty, not a player.
- The 2025 Pelicans–Jokerit qualification series and 2022:49298 (a 35-minute
  sudden-death tiebreak) do not feed ratings.
- Season team lists come from `team_season`, never from games seen so far (league
  membership is known before a season starts; deriving it from games leaks).

## Open decisions (mine, not Claude Code's)

1. **Off-machine backup of `data/`** — settled as a zip in Google Drive (cloud copy,
   and the Mac can fetch it). Automating it is in `docs/PIPELINE_SAFETY_PLAN.md`.
2. **Thin odds benchmark.** Closing lines exist only from 2026-09-17 (49 usable games
   at 2026-10-13, plus ~15–20 a week). Step 5 put a number on it: ~816 games, about
   two seasons, before a 0.01 gap is measurable. Until then, model choices are judged
   by walk-forward log loss against the baselines; the market comparison grows in the
   background. A historical Liiga odds session is the only way to shorten that.
3. **PLAYOFFS as a separate evaluation slice** (v1 evaluates RUNKOSARJA only).
4. **In-house location xG** from `shot_events` coordinates — later or never.
5. **Veikkaus as an additional odds source.** Optional; its own session.
6. **When to move scheduling to the i5 Linux box.** The 10-08–10-10 gap shows the
   cost of a desktop that gets shut down. An alert reports a gap; only an always-on
   machine prevents one.

## Immediate next steps, in order

1. Commit this file and `docs/PIPELINE_SAFETY_PLAN.md`; push; sync.
2. **Pipeline safety session** per `docs/PIPELINE_SAFETY_PLAN.md`: weekly backup to
   Google Drive (S1), then heartbeat and missed-window alerts via healthchecks.io (S2).
   S2 edits the live job code, so it is built off the live checkout and merged only
   outside a capture window.
3. **F2 then F3** per `docs/FEATURE_STORE_PLAN.md`.
4. **Plan step 6 in the design chat** (LightGBM + blend), scored by the step 5 harness.

## Deferred (explicitly, with reasons)

- **NHL ingest** — after the Liiga feature store and Elo. When it comes, the feature
  store's UTC-date history cutoff must be revisited (NHL games cross UTC midnight).
- **`shotmap` historical refetch** — no value guard, and not needed: `strength_type`
  gives even strength directly.
- **Goalkeeper-event recovery** — permanently downgraded; starters come from
  `game_goalie_period_stats`.
- **Opening-line comparison** — `benchmark_odds` holds closing rows only.
- `player_info`/`player_list`/`team_info`/`teams_stats`/`milestones` — cataloged, no
  parser.
- Totals market, news-article injury parsing, football, payments. Public site.

## Gotchas that cost real time

- **The no-shrink guard counts rows; it cannot see stripped values.** Backed by the
  value guard for `game_stats` only. All-or-nothing per game. `--salvage-from-raw` is
  the one approved exception.
- **A plain "no total may drop" rule is wrong.** Real live corrections drop TOI by up to
  5.3%, corsi by up to 12%, player goals by 1 toward the final score.
- **`backfill.py --force` has no guard at all.** Use `resync.py` for any refetch.
- **`backfill --season <live season>` needs `--only-ended`.**
- **`nightly_sync.py` must force the season-level endpoints**, or `games` freezes.
- **Feature history is cut by UTC date, not start time**, so an earlier same-day game
  still in play can't leak. Fine for Liiga, wrong for NHL. The Elo pass follows the
  same rule.
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
- **Both tasks run `pythonw.exe`**, so the log file is the only record. A failure
  before logging shows only as a non-zero `LastTaskResult` (`Get-ScheduledTaskInfo`).
- **The tasks run from the working checkout.** A half-finished edit to job code during
  11:00–23:00 can break a live capture.
- **Snapshot window is 11:00–23:00**, because some fixtures start at or before 14:00.
- `games_by_date` 502s intermittently and ignores `season`. `tournament=playoffs`
  returns a superset including PLAYOUT and QUALIFICATIONS. `serie` and `roleCode`
  vocabularies are open-ended.
- Playoff formats vary by season: 2022's first round was a two-game aggregate with a
  sudden-death tiebreak (49298). Never assume a bracket shape.

## Open items

- **Failure alerting and automated backup** — planned in `docs/PIPELINE_SAFETY_PLAN.md`,
  not built.
- 2026 relegation format is NULL in `season_format`; needed only for F3 motivation.
- `homePreviousGames`/`awayPreviousGames` absent from `game_preview` — unresolved.
- Goal-event surplus variance across seasons — unexplained; doesn't block anything.
- Why the power-play derivation fits 2016 and 2023 less well — not investigated.
- Review liiga.fi (and, if used, Veikkaus) ToS before anything public.

Closed since the last version: 2022:49298 dropped from ratings with a recorded reason;
team ids are continuous across 2015–2027 (18 ids, one per club); no team plays twice
on one UTC date in rating games; all seven 2027 opening-night games are ingested and
scored; the Elo link and HFA decisions.

## Meta — the pattern worth remembering

Every significant problem in this project has been an **unverified assumption that
failed silently rather than loudly**: `game_id` uniqueness, season numbering, the NTFS
colon bug, `sync_state` blindness to empty-but-200, OddsPapi's docs vs its live
behaviour, the row-count guard passing gutted values, liiga.fi's home/away penalty
arrays swapped in whole seasons. Step 5 added a leak that only a test could see:
building each season's team list from games already played made predictions depend
on future games, and the truncation test caught it.

Habits worth keeping: check a value against a second source before trusting it;
backtest a guard against real corrections before relying on it; when something looks
like bad data, test it before excluding it; and give every point-in-time computation a
truncation and a poison test.
