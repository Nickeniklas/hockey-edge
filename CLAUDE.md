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

**Settled again 2026-09-17: OddsPapi is primary for Liiga, polling
`pinnacle` + `bet365`.** The 2026-08-25 doubt — whether `/odds-by-tournaments`
returns usable prices at all — is resolved: it does, in-season; August's
metadata-only response was the board being too early, not the endpoint. The
budget math this design rested on therefore holds, with one correction: the
`bookmaker` param is single-valued, so a poll costs **one request per book**,
not one per tick. `job.py` runs `OddsPapiProvider` (no longer
`NullOddsProvider`). See `docs/ODDS_PLAN.md`.

Tests (added 2026-09-17): `tests/`, **stdlib `unittest`, no pytest** — run
`python -m unittest discover -s tests`. New tests go in `tests/test_*.py`.
They assert against **real saved API responses** (`fixtures/oddspapi/`,
`fixtures/liiga/`), never invented payloads, and make zero HTTP requests; a
test needing a DB builds a temp one via `storage.get_connection(tmp_path)`
and never touches `data/*.db`.

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
   — **done** for 2015–2027; live season kept current by `scripts/nightly_sync.py`
2. Snapshot capture job (lineups/goalies/odds) — deploy EARLY; missed data is gone forever
   — **deployed 2026-09-02**; lineups capturing, **odds capturing for real since
   2026-09-17** (OddsPapi, pinnacle + bet365)
3. NHL ingest
4. Feature store (see feature families in `docs/DATA_PIPELINE.md`)
5. Elo baseline + validation harness (benchmark: odds-implied log loss)
6. LightGBM + blend
7. Prediction log + local dashboard

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
- **A live season needs `backfill.py --only-ended`; a historical one doesn't.**
  Without it, per-game endpoints get fetched for not-yet-played games,
  liiga.fi returns a pre-game shell, `raw_cache` records it `success`, and
  `sync_state` then skips that game **forever** — the real post-game data
  never arrives via backfill again (only a `resync.py` run landing inside its
  `--days` window could repair it). Flag added 2026-09-01, default off so
  historical backfills are unchanged.
- **`games_by_season`/`standings` must be force-refetched on every live-season
  run, or the schedule freezes.** `sync_state` marks them `success` on first
  fetch and skips the network forever after, so `games.ended` never updates and
  the job never learns last night's games finished. `nightly_sync.py` passes
  `--force --endpoints games_by_season,standings` for exactly this
  (verified: an unforced second run left `fetched_at` unchanged). Per-game
  endpoints are deliberately NOT force-refetched there — that's the unsafe
  bulk `game_detail` operation `docs/RESYNC.md` warns about; `resync.py` is
  the guarded route.
- **`game_goalkeeper_events` cannot tell you who started a game.** Zero rows
  anywhere have `begin_time=0`, and 36% of season-2026 games have no rows in
  it at all — it only records mid-game substitutions and empty-net pulls. Use
  `game_goalie_period_stats` (period-1 `shots_on_goal > 0`) for the actual
  starter, as `scripts/verify_starters.py` does.
- **Snapshot task window is 11:00–23:00 local, not 14:00.** Liiga is *mostly*
  17:00/18:30 starts, but season 2027 has 15 RUNKOSARJA games starting
  earlier, including one at 12:00 and two at 14:00. The binding constraint is
  the **closing** (T-25min) window, not T-24h: `storage.get_due_windows` only
  returns a window while `start_utc > now`, so a closing window that comes due
  before the first tick is recorded MISSED permanently, not fired late. Verify
  against the real schedule before narrowing this window again.
- **Registering the scheduled task from PowerShell 7 requires stripping the
  XML declaration**: `Register-ScheduledTask -Xml` takes a *string*, which PS
  holds as UTF-16, so any encoding declaration is a contradiction the parser
  rejects. `$xml -replace '<\?xml[^>]*\?>', ''` first. Separately, the file's
  declaration used to claim UTF-16 while the bytes were UTF-8, which breaks
  `schtasks /XML` — fixed to UTF-8. Both notes are in the XML's own header.
- **`serie` is an open-ended vocabulary.** Season 2027 introduced
  `PITSITURNAUS` (a preseason tournament) on top of the known
  RUNKOSARJA/PLAYOFFS/PRACTICE/PLAYOUT/QUALIFICATIONS. Never treat the list as
  closed or constrain a column to it.
- **`roleCode` vocabulary changes as a game approaches**, on the same game_id:
  `{H, P, MV}` (generic) at T-9 days vs. the full
  `{KH, VL, OL, H, VP, OP, MV, P, 7. P, 13. H, 8. P}` at T-30. The
  earlier-flagged "season change or pre/post-game shape?" question is
  resolved — it's proximity to game time. Don't hardcode either vocabulary.
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
  (decided 2026-08-24, registered 2026-09-02 — not an always-on host;
  `docs/snapshot_job_task_scheduler.xml`, every 15 min, 11:00–23:00 local,
  `StartWhenAvailable=true`, **`WakeToRun=true`**). WakeToRun was `false`
  through preseason — a deliberate choice to exercise missed-tick recovery
  on a sleeping desktop, back when a missed August friendly cost nothing.
  **Flipped to `true` 2026-09-02 once the season started**: relying on the
  machine happening to be awake at T-25min makes the job close to useless in
  practice, and a missed capture is unrecoverable. Depends on the power plan
  permitting wake timers, and only wakes a *sleeping* machine (not
  hibernated/shut down) — hence StartWhenAvailable stays on too. The job's
  own due/missed window logic (`storage.get_due_windows`/
  `mark_missed_windows`) is still what makes a late wake-up correct, not the
  scheduler — never replace that with a naive "is now ≈ the window time"
  check. launchd/cron variants are scaffolding only, not deployed.
- **Both scheduled tasks run `pythonw.exe`, not `python.exe`** (changed
  2026-09-03, so a tick stops popping a console window that steals focus;
  `<Hidden>` would not have done it — for an Exec action that setting governs
  UI listing, not whether the process gets a console). Under pythonw
  `sys.stdout`/`sys.stderr` are None, so a failure outside the logger — an
  import error, or anything before `_configure_logging()` finishes — leaves no
  record anywhere except Task Scheduler's non-zero Last Run Result. Check it
  with `Get-ScheduledTaskInfo -TaskName "hockey-edge snapshot job"`. Editing
  the snapshot task via `Set-ScheduledTask` needs an elevated PowerShell (its
  XML carries an explicit `<Principal>`); the schtasks-registered nightly sync
  does not.
- OddsPapi returns HTTP 404 with `code: "FIXTURE_NOT_FOUND"` for a tournament
  with no fixtures currently posted, not `HTTP 200` with `[]` — the snapshot job
  handles this explicitly as "no odds yet," not a failure; don't reintroduce a
  bare `raise_for_status()` that would misclassify it.
- **`GET /v4/odds-by-tournaments` DOES carry prices — when the board is
  in-season.** Corrects the 2026-08-23 entry that said it never does (that
  test ran in the off-season, weeks before any book had posted a price; the
  `hasOdds: true`-but-no-`bookmakerOdds` shape is what an early board looks
  like). Live 2026-09-17: every posted fixture came back with a full
  `bookmakerOdds.<book>.markets` block. **`bookmaker` is required and takes
  one book per request**, so cost scales with books, not fixtures. `GET
  /v4/odds` (per-fixture, all 213 bookmakers, 11.6 MB) is not for polling,
  and **Veikkaus is not among those bookmakers**.
- **OddsPapi rate-limits bursts separately from the monthly quota: two
  back-to-back board calls got the second one HTTP 429** (first live run,
  2026-09-17 21:30 — pinnacle 200, bet365 429). A 429 still bills as a
  request and returns nothing, so it's a lost capture *and* spent budget.
  `job.py` sleeps `ODDS_BOOK_DELAY_SECONDS` (20 s) between books; don't
  remove that spacing, and don't add a blind retry (a retry is another billed
  request). Verified: every poll since the spacing went in returned 200 on
  both books.
- **A parsed odds row needs `bookmakerIsActive: true`, and Pinnacle turns it
  off per fixture** (5 of 8 fixtures on 2026-09-18 11:00, prices still in the
  payload). Those rows land `parsed=0` with the payload kept, so a later
  reparse can decide differently if inactive prices turn out to matter. Not
  a failure; the other book normally covers the window.
- **Odds market/outcome ids (ice hockey, both books checked):** market `153`
  = 3-way regulation 1X2 with outcomes `153`/`154`/`155` = home/draw/away;
  market `151` = 2-way moneyline incl. OT with `151`/`152` = home/away;
  price at `...outcomes.<oid>.players["0"].price` (decimal). `participant1Id`
  is home. Pinnacle labels outcomes literally `home`/`draw`/`away`; bet365's
  labels are opaque numbers, so its mapping rests on the shared outcome ids
  plus favourite direction — the parser rejects any row whose text label
  contradicts its slot. OddsPapi participant names are **not unique** (2–3
  ids per Finnish club), so never string-match teams: use the curated
  `snapshot/odds/oddspapi_teams.json`.
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
