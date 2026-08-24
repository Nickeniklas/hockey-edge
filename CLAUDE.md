# CLAUDE.md — hockey-edge operating brief

## What we're building
**hockey-edge**: personal-use-first hockey prediction & edge tool for **Liiga + NHL**: data pipelines
→ feature store → calibrated win probabilities → compared against bookmaker odds →
immutable prediction log. Liiga is the differentiator (nobody models it; softer odds).
Full plan: `docs/PLAN.md`. Data contract: `docs/DATA_PIPELINE.md`. Model contract:
`docs/MODEL.md`. Read them before structural work.

/docs/PROJECT_CONTEXT: Owned by the design chat. Emitted whole and replaced wholesale at session close. Not edited by Human or by Claude Code sessions.

## Decided stack — do not re-litigate without being asked
Python · SQLite · liiga.fi JSON API (endpoints discovered 2026-07-12 via JS-bundle
grep + direct curl, not devtools — see `endpoints.py`) · NHL official
API · MoneyPuck/NatStatTrick for NHL xG bootstrap · lineups from liiga.fi
`/kokoonpanot` + veikkaus.fi mirror (liiga.fi has no odds — lineups/goalies only) ·
odds: NHL via The Odds API free tier (1 credit = all NHL games per market+region),
Liiga via OddsPapi free tier (listed on all plans, checked 2026-07; billing
confirmed per-HTTP-request 2026-07-12 against the dashboard counter; Liiga
tournamentId=134; guaranteed fallback = scrape Veikkaus, which is also the book
actually bettable in Finland) · odds capture behind a swappable provider
interface (scaffolded in `src/hockey_edge/snapshot/odds/`) · Elo baseline +
LightGBM blend · local compute only.

**Re-opened, not still decided, as of 2026-08-25: which odds provider is
primary for the live snapshot job.** OddsPapi's per-request billing and
tournament id are still correct facts above, but whether `/odds-by-
tournaments` actually returns usable prices at all is now in doubt (see the
Gotchas entry on this) — `job.py` currently runs `NullOddsProvider`, not
`OddsPapiProvider`. Don't treat "Liiga via OddsPapi" as settled until that's
resolved; this is the user's call to make, not something to re-decide
unilaterally either way.

Tooling (decided session 1): plain venv + requirements.txt (no uv/poetry) ·
src/hockey_edge/ package layout · raw JSON cached as files under data/raw/
(gitignored), SQLite stores metadata only · each `Endpoint` in the catalog module
tracks `verified_seasons`, since historical seasons may use different endpoints
or shapes · one real sample response per verified endpoint/season checked into
`fixtures/liiga/<name>/<season>.json` (git-tracked, unlike `data/raw/`).
**Added 2026-08-23: `pyproject.toml` (setuptools, `pip install -e .`) makes
`src/hockey_edge` an editable-installed package** — `python -m
hockey_edge.<module>` and `import hockey_edge` now work directly from an
activated venv, no `PYTHONPATH=src` needed. Not a stack change (still plain
venv + requirements.txt for actual dependencies, no uv/poetry) — this is
packaging only. Any doc/docstring still showing `PYTHONPATH=src python -m
...` outside a preserved historical-log section is stale; run without it.

## Hard rules
1. **No leakage.** Features use only information timestamped before puck drop.
   Snapshot rows are append-only with `captured_at` (UTC).
2. **Prediction log is immutable.** Never edit or delete rows.
3. **Walk-forward validation only.** Never shuffle games across time.
4. **Raw data is append-only**; corrections live in derived layers.
5. **Resumable sync from day one** — `sync_state` table, idempotent re-runs, raw
   responses cached so parsing can be redone without refetching.
6. **Polite scraping**: rate limits, cached responses, normal UA. liiga.fi API is
   undocumented and can change — isolate endpoint definitions in one module.
7. **Log loss + calibration are the metrics.** Accuracy is never the target.
8. No bookmaker affiliate integrations, ever.
9. No autonomous git commits or pushes (global rule; applies here too).

## Build order
1. Liiga ingest (endpoint discovery → schema → ~10-season backfill, resumable)
2. Snapshot capture job (lineups/goalies/odds) — deploy EARLY; missed data is gone forever
3. NHL ingest
4. Feature store (see feature families in `docs/DATA_PIPELINE.md`)
5. Elo baseline + validation harness (benchmark: odds-implied log loss)
6. LightGBM + blend
7. Prediction log + local dashboard

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
  that window — 1,164 games across 8 seasons (2015, 16, 17, 19, 20, 21, 23,
  24) have zero `game_penalty_events` despite a `success` sync_state row,
  in two distinct symptom shapes (missing the whole `game` JSON key vs. a
  `game` key present with empty event arrays) — both confirmed independently
  recoverable via a live refetch. Never recurred outside that window
  (2025/2026's own small zero-penalty counts were fetched 2026-08-22, a
  confirmed-clean run, and are more likely genuine than broken). Full
  per-season/per-symptom-class breakdown, the exact recovery commands (via
  the guarded `resync.py` path, not the unguarded `backfill.py` one — see
  Gotchas), and cost estimates are in **`docs/RECOVERY_BACKLOG.md`** — not
  run this session, ready to execute later. One open question logged there
  with no proposed mechanism: 45 `PLAYOFFS` games fetched in the exact same
  78-minute sub-window that broke 450 `RUNKOSARJA` games came through 100%
  clean — phase seems to matter, timing alone doesn't explain it.
- **`data/hockey.db` has zero rows for season=2027 (the live 2026-27
  season)** — confirmed 2026-08-23, contradicting an earlier (design-chat)
  belief that it was already backfilled. The live API has it (544
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
  all of 2015-2024; (2) season 2025 has a 79-game `shot_events` gap
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

## Gotchas
- **liiga.fi's data for a completed game can change after original fetch —
  in EITHER direction, not just enrichment. Historical data is NOT immutable
  on the API side.** Originally found 2026-08-22 on `game_stats` (season=2024
  game_id=1: 1 `puckStats` entry on first fetch, 3 on a later `--force`
  refetch, period 1 unchanged — genuine enrichment). **Corrected 2026-08-24**
  (`docs/RESYNC.md` has the full writeup — Phase 4a's scope check): the same
  game's `game_detail` does the *opposite* on refetch — its
  `goalKeeperEvents`/`goalKeeperChanges` arrays lost real rows (home team
  4→1, away team 3→0), not gained them. `shotmap` showed a third pattern:
  same event count, one stat corrected in place
  (`ownTeamPlayersOnIce`/`otherTeamPlayersOnIce`, +1 on every one of 88
  shots). **Do not assume "content changed" means "content improved" for any
  endpoint** — this is why the re-sync mechanism
  (`src/hockey_edge/ingest/liiga/resync.py`, `docs/RESYNC.md`) has a
  universal no-shrink guard: a refetched response is only reparsed into
  curated tables if no table it feeds would end up with fewer rows than it
  already has; a would-shrink reparse is skipped and logged, existing
  curated rows are kept untouched, and the raw response is still saved to
  disk regardless (append-only, no loss either way). Consequences: (a) a
  `game_stats`-scoped (and `shotmap`-scoped) `--force` refetch of seasons
  2015-2024 would likely recover the missing `game_puck_control` periods —
  still deferred, exact command in `docs/RESYNC.md`'s 4b section; (b) the
  live snapshot/ingest path needed a periodic re-sync of recently-completed
  games rather than fetch-once-and-mark-`success`-forever — built
  2026-08-24 as `resync.py --days N`, see `docs/RESYNC.md`. This is a
  raw-cache append-only-friendly append (a refetch with different content
  writes a new file, per `docs/SCHEMA_DRAFT.md`'s `raw_responses` naming
  scheme). **`backfill.py --force` still has no shrink guard of its own and
  should not be used for a bulk historical `game_detail` refetch** — but
  this is *not* a blanket "`game_detail` can never be safely refetched"
  rule (an earlier, since-superseded version of this note said that): a
  *targeted* `game_detail` recovery through the guarded `resync.py` path is
  safe precisely because the guard blocks any reparse that would shrink
  `game_rosters`/`game_penalty_events`/`game_goalkeeper_events`. 1,164
  games across 8 seasons are confirmed to need exactly this recovery (a
  bounded ~34h liiga.fi-side incident, 2026-07-19/21 — see the 2026-08-25
  Status entry above) — exact commands and per-season counts in
  `docs/RECOVERY_BACKLOG.md`, not run yet.
- **Goal-event surplus (`game_goal_events` count vs. final-score sum) is not
  a single consistent pattern — magnitude varies season to season and
  remains unexplained beyond the mechanisms already found.** Season 2026:
  +62 surplus events (3,403 vs. 3,341 score-sum, ~1.8%). Season 2025: +3
  surplus (3,480 vs. 3,477, ~0.09%) — an order of magnitude smaller than
  every other season checked (2015-2024, 2026 all ran ~2-4%). The known
  mechanisms (overturned/video-review goals, zero-goal-event foreign
  friendlies) aren't sufficient to explain why 2025 is so much smaller;
  not investigated further this session.
- **`game_id` is not globally unique across seasons** — liiga.fi reuses
  small `RUNKOSARJA`/`PRACTICE` game_ids per season (confirmed: 449/450
  season-2023 regular-season game_ids collided with season 2024's). Any
  code touching `games` or a per-game table must key on `(game_id, season)`
  together, never `game_id` alone — see the 2026-07-21 Status entry and
  `docs/BACKFILL_RESULTS.md` for the corruption this caused before the fix.
  `PLAYOFFS` game_ids did not collide in testing (large, apparently-global
  range) but treat the composite key as the rule, not the exception.
- Liiga playoff format changed 2024-25; flag season phase per game; formats vary by season.
- `games_by_season`'s `serie` field for preseason games reads `"PRACTICE"`, not
  `"VALMISTAVAT_OTTELUT"` (confirmed season=2024) — `standings` uses lowercase
  `valmistavat_ottelut` as its dict key for the same phase. The two endpoints
  don't share vocabulary; don't assume they do elsewhere either.
- On Windows, a raw `:` in a `data/raw/` cache filename is NTFS's alternate-
  data-stream separator — it does not error, it silently writes to a hidden
  stream invisible to `ls`/Explorer/git while reads keep working. Any code
  building cache filenames from API-derived strings (season+tournament,
  player names, etc.) must sanitize `:` same as `/`. Fixed once in
  `raw_cache.py`; if a new raw-cache path gets added elsewhere, check it too.
- Liiga odds are three-way (regulation 1X2); NHL moneyline is two-way incl. OT. Store market type.
- Player-name normalization across sources (liiga.fi vs Veikkaus vs community) is a known pain.
- Liiga small samples: regress early-season features hard to league mean.
- **Jokerit is not a blank slate for the Elo cold-start, but isn't much of a
  signal either.** `data/hockey.db` has 18 Jokerit rows across 5 seasons
  (2021, 22, 24, 25, 26) — mostly preseason friendlies, plus a real 5-game
  2025 `QUALIFICATIONS` series against Pelicans. **Pelicans won that series
  4-1** (confirmed 2026-08-24) — Jokerit lost it; it is not a promotion
  result, despite how it might read at a glance. Net: five competitive games
  from 16 months ago, all losses, plus scattered friendlies — barely
  distinguishable from no data. See `docs/MODEL.md`'s promoted-team
  cold-start note.
- **Snapshot job scheduler: Windows Task Scheduler on the main desktop**
  (decided 2026-08-24, not an always-on host — deliberate for preseason,
  since a missed capture on an August friendly costs nothing;
  `docs/snapshot_job_task_scheduler.xml`, `StartWhenAvailable=true`,
  `WakeToRun=false` so a sleeping machine catches up on wake rather than
  being forced awake). The job's own due/missed window logic
  (`storage.get_due_windows`/`mark_missed_windows`) is what makes a late
  wake-up correct, not the scheduler — never replace that with a naive
  "is now ≈ the window time" check. launchd/cron variants are scaffolding
  only, not deployed.
- OddsPapi returns HTTP 404 with `code: "FIXTURE_NOT_FOUND"` for a tournament
  with no fixtures currently posted, not `HTTP 200` with `[]` — the snapshot job
  handles this explicitly as "no odds yet," not a failure; don't reintroduce a
  bare `raise_for_status()` that would misclassify it.
- **`GET /v4/odds-by-tournaments` does not carry price/market data** —
  confirmed live 2026-08-23 (2 separate calls, `fixtures/oddspapi/`): a
  fixture with `hasOdds: true` still has no `bookmakerOdds` key at all, only
  metadata (participant ids, tournament id, `startTime`). This contradicts
  OddsPapi's own published docs, which describe a full `bookmakerOdds`/
  `markets`/`outcomes` block on that same endpoint — unresolved which is
  right; not re-tested with a real fixture board yet. Real per-fixture
  pricing likely needs `GET /v4/odds` (confirmed single-`fixtureId`-only, no
  bulk form) — see `docs/SNAPSHOT_FINDINGS.md`'s Shape A/B cost analysis
  before assuming the original "one poll/night covers everything" budget
  math still holds. This is why `OddsPapiProvider` is built but not wired
  into `job.py` — see the 2026-08-25 Status entry.
- `GET /v4/participants?sportId=<id>` resolves OddsPapi's bare numeric
  participant ids (e.g. `3836`) to team names in one call, no
  fixture/tournament scoping, cacheable for a season — solves half of the
  OddsPapi↔liiga.fi join-key problem cheaply. The other half doesn't have a
  cheap fix: two fixtures can share the exact same `startTime` on a
  multi-game night (confirmed: 5 liiga.fi games at one identical kickoff),
  so `startTime` alone can't disambiguate — the join needs resolved
  participant names, not just time.

## Secrets
Odds API keys via environment / untracked `.env`. Never commit keys. If the repo goes
public, gitignore any personal betting/bankroll data (same pattern as market-advisor's
gitignored portfolio.md).
