# PROJECT_CONTEXT — hockey-edge (Liiga + NHL prediction & edge tool)

Paste-ready summary for Claude project memory. Crystallized 2026-07-06; rewritten
2026-08-24.

Owned by the design chat. Emitted whole and replaced wholesale at session close. Not
edited by Human or by Claude Code sessions.

## Project

Niklas (IT student, Finland; Python/data/ML skills; Windows RTX 3060 Ti desktop + M1
Mac, plus an unused 5th-gen i5 desktop earmarked as a possible Linux box) is building
a **personal-use-first hockey prediction tool for Liiga and NHL**. Core loop: ingest
historical data → capture pre-game info (confirmed lineups, starting goalies,
bookmaker odds) timestamped before puck drop → rolling features → calibrated win
probabilities → compare vs odds to surface edges → immutable, timestamped prediction
log.

It's an **edge finder** (model probability vs bookmaker odds), not a stats site and
not bare predictions. **Primary user is Niklas himself, and as of 2026-08-24 the
public/commercial angle is leaning toward being dropped entirely** — he doesn't
follow much sport, doesn't gamble, and the selling motivation was always weak. That
decision has its own session (see Open decisions); until it's made, don't let
"someday public" quietly shape build choices the way it has been.

## Decided stack (don't re-open)

Python, SQLite, local compute, `src/hockey_edge/` package layout. Packaging resolved
2026-08-23: `pyproject.toml` exists, `pip install -e .` into a real `.venv`, no
`PYTHONPATH=src` prefix. Backfill-style commands still must run **from the repo
root** — `data/`, `logs/`, `data/raw/` are relative paths.

Liiga data from liiga.fi's undocumented JSON API at `https://liiga.fi/api/v2`; 16
endpoints confirmed, catalog and `verified_seasons` in
`src/hockey_edge/ingest/liiga/endpoints.py` (source of truth). NHL from the official
free NHL API; NHL xG bootstrapped from MoneyPuck/Natural Stat Trick.

Models: Elo-style baseline + LightGBM, blended. Targets: NHL binary moneyline; Liiga
three-way regulation 1X2. Metrics: log loss + calibration, never accuracy; benchmark
is odds-implied probabilities (vig removed) from the last pre-game snapshot.

**Odds sourcing is no longer settled — see Open decisions.**

## Hard invariants

Pre-puck-drop information only (append-only snapshots with `captured_at` UTC);
**immutable prediction log** (append-only, never edited or deleted — corrections go in
as new rows under a new `model_version`, because a log that can be rewritten can't
evidence what was predicted before a game); strict walk-forward validation; resumable
sync (`sync_state` table, raw JSON cached as files under `data/raw/`, SQLite metadata
only); polite scraping (1.5s, normal UA); no autonomous git commits; no affiliate
integrations.

## Season numbering — CONFIRMED

liiga.fi uses the **ending-year convention**: `season=2024` is the 2023–24 season.
Verified 2026-08-22. This was the source of a real gap — the original backfill covered
2015–2024 and silently omitted two completed seasons.

## Status (2026-08-24)

**Step 1 (Liiga ingest): seasons 2015–2026 in `data/hockey.db`, 6,736 games**,
composite `(season, game_id)` primary key — `game_id` is a per-season counter that
resets annually and is NOT globally unique.

**CORRECTION to the previous PROJECT_CONTEXT: `season=2027` is NOT in the database.**
The prior version claimed it held all 544 fixtures. `SELECT COUNT(*) FROM games WHERE
season=2027` returns **0**, and no `sync_state` row references season 2027. The *live
API* returns all 544 `RUNKOSARJA` fixtures (17×64÷2, all `started=false`) — they exist
upstream, they've simply never been ingested. This doesn't block the snapshot job,
which reads live by design, but it's a real gap: see Live-season ingest below.

**Step 2 (snapshot capture): Phases 1, 2 and 4 complete. Phase 3 blocked.**

Phase 1 recon (`docs/SNAPSHOT_FINDINGS.md`) and Phase 2 build shipped:
- New tables in `snapshots.db`: `discovered_fixtures` (append-only),
  `capture_windows` (per-game-per-window state), `api_usage` (append-only, three-state
  outcome — success / empty / failure).
- New modules: `snapshot/fixtures.py` (live discovery via `games_by_date`),
  `snapshot/odds/null_provider.py` (zero-HTTP stub). `job.py` rewritten with
  `--dry-run` / `--once` and budget-ceiling enforcement.
- **Missed-tick logic verified against a simulated sleep**: windows are evaluated as
  "due, unsatisfied, and still in the future," never by timestamp proximity, so a
  sleeping desktop that wakes at 20:00 still fires a 20:05 closing poll instead of
  writing the whole evening off. Genuinely-passed windows are recorded as `missed`
  rather than left absent, so validation can exclude those games rather than
  discovering a silent hole later.
- API-key redaction added to `oddspapi.py` (the `raise_for_status()` leak path).
- `docs/snapshot_job_task_scheduler.xml` written with `StartWhenAvailable=true`,
  `WakeToRun=false`; launchd/cron variants as scaffolding.

Phase 4 (staleness/re-sync) complete: `backfill.py` gained `--endpoints` scoping for
`--force`, and `src/hockey_edge/ingest/liiga/resync.py` is new — time-windowed
(`--days`, default 7), staleness derived from `sync_state.fetched_at` with no DDL
change, and the **no-shrink guard** described below. Verified end-to-end against a
scratch copy of `hockey.db`, never production.

Phase 3 (odds parsing + lineup capture) is blocked on both halves: no odds provider is
decided, and the lineup question isn't answered.

**Step 3 (NHL ingest): not started.** October start, has slack.

## CRITICAL FINDING — liiga.fi responses mutate in BOTH directions

**This corrects the previous PROJECT_CONTEXT's "retroactive enrichment" framing, which
was wrong and actively misleading.** The first observed case (a `game_stats` response
gaining `puckStats` periods) was enrichment, and naming the phenomenon after that one
instance baked a directional assumption into every downstream decision. Phase 4 was
originally scoped around "refetch to get the complete version," which is only coherent
if mutation is monotonic.

It isn't. Refetching `game_detail` for season 2024 `game_id=1` returned **fewer**
goalkeeper events than the stored response (7 rows → 1). A re-sync treating "content
hash differs → new version is truth" would have silently deleted correct data and
called it an update.

Across four samples the behaviour is **shrink, grow, no-change, and no-change** — the
source is unreliable in multiple directions and no single mechanism explains it. The
useful term is **non-monotonic mutation**, not enrichment.

**The fix, implemented in `resync.py`: a universal no-shrink guard.** A refetched
response is reparsed into curated tables only if no curated table it feeds would end up
with fewer rows. If any would shrink: skip the reparse, keep existing curated rows
untouched, still write the new raw response to disk (append-only, nothing lost), log
WARNING with per-table before/after counts. Comparison is per-table totals, not
per-team — a penalty event legitimately reassigned between the home and away arrays
must not trip it. Applied to all three endpoints, not special-cased to `game_detail`,
because one game isn't enough to know which endpoints can regress.

**Variation is temporal, not per-request.** Three back-to-back fetches of the same game
returned identical hashes, so refetching isn't a coin flip and the re-sync doesn't need
to union multiple fetches.

**Rate limiting ruled out explicitly.** Across 20,280 `sync_state` and 20,122
`raw_responses` rows: zero non-200 statuses, zero nulls, zero `failed_retryable`. This
project has never been throttled or blocked. The 160 `failed_permanent` rows are all
HTTP 200 with an error-placeholder body.

## The 2026-07-19/21 bad-response window — ~1,164 damaged games

The largest data-quality problem in the project, found by chasing the mutation
question rather than by any error.

**1,164 of 6,736 games have zero `game_penalty_events` despite a `success` sync_state
row.** Hockey games always have penalties, so this is source damage, not real data.
Season 2023 is 92% affected (517/562); the baseline elsewhere runs 2–39%.

All damage clusters in one ~34-hour window, **2026-07-19T19:55Z to
2026-07-21T05:54Z** — the original 10-season backfill run. Zero damaged files outside
it, including the 2025/2026 backfill and everything fetched since. Two distinct
symptom classes:

1. **483 files lack a top-level `game` key entirely** — rosters present, everything
   else (score, periods, `penaltyEvents`, `goalEvents`, referees) missing.
2. **Files with the `game` key present but `penaltyEvents` empty for both teams** —
   season 2023's 450 RUNKOSARJA games are all of this type.

**Both classes are recoverable.** A live refetch of a class-1 game (season 2015)
returned the full `game` block; refetching two class-2 games returned populated
`penaltyEvents` in both. **Caveat: `goalKeeperEvents` recovered in only one of the
two** — so penalty recovery looks reliable, goalkeeper recovery does not. That matters
because historical starting-goalie identification depends on
`game_goalkeeper_events`, which is feature family #1; period-1 goalie ice time in
`game_stats` is the fallback path.

**Unexplained, logged and not theorised about:** 45 PLAYOFFS games were fetched in the
exact same 78-minute window that broke 450 RUNKOSARJA games, and came through 100%
clean. Pure server flakiness should have hit some of them. No mechanism proposed.

Recovery is scoped and ready but **not run** — see `docs/RECOVERY_BACKLOG.md`.

## Other findings this session

- **New `serie` value: `"PITSITURNAUS"`**, found in `season=2027`
  `valmistavat_ottelut` (52 preseason games, 2026-08-07 → 08-27). Not in `endpoints.py`
  or any doc. Treat as a third preseason-family value, not a `PRACTICE` synonym.
- **`game_preview` requires a full ISO datetime** for `gameDate`
  (`2026-09-01T15:30:00Z`); a bare date 500s. Undocumented in `endpoints.py`.
- **`game_detail` never resolves to a confirmed starter** — pre-game it returns the
  extended squad (37/33 players, 4 goalies per team, `line` null throughout), and even
  a *completed* historical game still lists 2–3 goalies with `line` null. Historical
  starter identification must come from `game_goalkeeper_events` or `game_stats` ice
  time, which is inherently post-hoc.
- **Jokerit — corrects the previous "zero rows anywhere" claim.** `hockey.db` has 18
  Jokerit rows across 5 seasons, mostly `PRACTICE` friendlies, plus a real 5-game
  season-2025 `QUALIFICATIONS` series vs Pelicans. **Pelicans won it 4–1** — Jokerit
  lost and did not go up that way, so it is not the promotion path. The Elo cold-start
  problem therefore stands roughly as originally stated: five competitive games from 16
  months ago, all losses, is barely distinguishable from no data.
- **Regular season opens 2026-09-01T15:30:00Z** (18:30 local), confirmed from two
  independent live sources.
- `games_by_date` 502s intermittently — retry/backoff, not fatal.
- Season 2025 `shot_events` gap (79 RUNKOSARJA games, coordinates only) and the
  `sync_state` blindness to empty-but-200 responses both still stand as previously
  documented.

## OddsPapi — Shape B CONFIRMED, budget maths broken

`/v4/odds-by-tournaments` is a **fixture-board listing, not a prices endpoint**. The
live response carries `fixtureId`, participant ids, `startTime`, and a `hasOdds` flag —
**no `bookmakerOdds`, no markets, no prices**, despite `hasOdds: true`. OddsPapi's own
documentation describes the full-markets shape; the live API disagrees with its docs.
Confirmed twice (requests 1 and 4 of the session's 8-request budget; 4 used total).

Consequences:
- Real pricing needs one `/v4/odds` call **per fixture**. At ~100 Liiga games/month
  across three capture windows that's ~225–300 requests against a 250/month free
  tier — over the limit. The "one poll covers the whole board" premise the whole
  design rested on is dead.
- `bookmaker` is a **required** parameter, not a filter (400 `INVALID_PARAMETER`
  without it). Existing code already passes it.
- **Good news:** `/v4/participants?sportId=15` returns the whole sport's id→name map in
  one cacheable call, so the join key is a one-time lookup, not a per-poll cost.
- The remaining join problem: OddsPapi gives bare numeric participant ids with no
  names, and `startTime` alone can't disambiguate — five liiga.fi games share one
  2026-09-05T14:00:00Z kickoff. Join must be participant-id → name → liiga.fi team,
  with `startTime` as tie-breaker only.

## Open decisions (mine, not Claude Code's)

1. **Odds provider.** OddsPapi at Shape B doesn't fit the free tier at three windows
   per game. Options: drop to closing-only OddsPapi/Pinnacle as a sharp benchmark
   (~100 req/month, fits), or make **Veikkaus** primary via scraping. Veikkaus is also
   the book actually bettable in Finland, so a Veikkaus edge is the actionable one.
   **Nothing about Veikkaus has been tested** — it's been "guaranteed fallback" in the
   docs since July purely on the observation that they post odds on every Liiga game.
   Unknown: server-rendered vs JSON-behind-the-page, bot protection, market shape, how
   far ahead lines post. **Veikkaus is its own session** (recon + provider
   implementation behind the existing interface). Their ToS almost certainly prohibits
   automated access — low-stakes for a personal tool at low rate, a real blocker for
   anything public.
2. **Public vs personal.** Own session. Leaning toward dropping public entirely. Note
   that a public version wouldn't need to republish anyone's odds — publishing the
   model's own timestamped probabilities is the track record; the edge calculation can
   stay private. Dropping public removes roughly half the deferred list.
3. **Where the snapshot job runs.** Windows Task Scheduler on the main desktop for now
   — deliberately imperfect, because preseason is the right stakes to shake out
   scheduling bugs on games that don't matter. The idle 5th-gen i5 desktop is the
   likely Linux host later; a small VPS is a fallback, not a decision. Becomes real
   around October.

## Immediate next steps, in order

1. **Lineup probe, Tuesday 2026-08-25 at 14:00Z and 15:00Z** (T-90 and T-30 before the
   15:30Z TPS–Jokerit preseason game). `python scripts/lineup_probe.py --season 2027
   --game-id 2701831` — standalone, read-only, no DB writes. Being run from a laptop
   clone, not the main desktop. At T-25h `goaliesToWatch` was populated for TPS but
   empty for Jokerit, still listing all 4 goalies. This decides whether `lineups.py`
   targets liiga.fi at all or whether lineup capture needs a different source. If
   nothing narrows, note it's a preseason friendly — the 2026-09-01 opener is the real
   confirmation.
2. **Odds provider decision** (Open decision 1), which unblocks Phase 3's odds parsing.
3. **Phase 3**: odds parsing + lineup capture, scoped to whatever 1 and 2 support.
4. **Live-season ingest for 2027.** Nothing currently puts completed 2026-27 games into
   `hockey.db`, so features would have no current-season data. Smaller than it sounds:
   `backfill --season 2027` is already resumable and idempotent, so scheduling it
   nightly plus the Phase 4 re-sync mostly covers it. Fold into the Phase 4 area rather
   than treating it as a new build.
5. NHL ingest, feature store, Elo baseline + validation, LightGBM blend.

## Deferred (explicitly, with reasons)

- **`game_detail` recovery of the ~1,137 damaged 2015–2024 games** — commands ready in
  `docs/RECOVERY_BACKLOG.md`. Recommended path is `resync.py --days 4380 --endpoints
  game_detail` (~2.8h, sweeps all ended games because the guard can't be season-scoped)
  rather than the faster unguarded `backfill.py` per-season alternative. The no-shrink
  guard is what makes this safe. 27 games from 2025/2026 excluded — fetched in a
  confirmed-clean run, more likely genuine than broken.
- **`game_stats`/`shotmap` refetch for ~11,000 missing `game_puck_control` rows** —
  cross-referenced in `RECOVERY_BACKLOG.md`. No feature family depends on puck control.
- `player_info`/`player_list`/`team_info`/`teams_stats`/`milestones` — cataloged, no
  parser; `teams_stats` still needs a devtools capture.
- Totals market, news-article injury parsing, football, payments.
- Public site (pending Open decision 2).

## Open items

- 45 clean PLAYOFFS games inside the 78-minute window that broke 450 RUNKOSARJA games
  — unexplained, no mechanism proposed.
- `goalKeeperEvents` recovery is unreliable (1 of 2 test games) — don't assume the
  recovery run restores feature family #1's historical inputs.
- `roleCode` vocabulary differs between pre-game and older completed-game payloads
  (`P/H/MV/KP` vs `KH/VP/OL/VL/MV/OP/H`) — season drift or shape difference, unresolved.
  Don't hardcode one as universal.
- `homePreviousGames`/`awayPreviousGames` absent from `game_preview` despite being
  noted in `endpoints.py` — shape change or proximity-dependent, unresolved.
- Whether `MODEL.md`/`DATA_PIPELINE.md` were actually updated from "15 teams, 60 games"
  to 17/64/544 plus the promoted-team cold-start rule — instructed, not verified.
- Goal-event surplus variance — unexplained.
- Failure alerting beyond CRITICAL log lines — not built.
- Review liiga.fi (and, if used, Veikkaus) ToS before anything public.

## Meta — the pattern worth remembering

Every significant problem in this project has been an **unverified assumption that
failed silently rather than loudly**: `game_id` uniqueness, ending-year season
numbering, the NTFS colon bug, `sync_state` blindness to empty-but-200, the API key in
`raise_for_status()` text, OddsPapi's docs vs its live behaviour, and the
enrichment-vs-mutation framing above. None surfaced as an error. All surfaced by
someone checking a claim that read like a fact. The stop-and-report gates in Claude
Code prompts exist for exactly this reason and have paid off every time they've fired.
