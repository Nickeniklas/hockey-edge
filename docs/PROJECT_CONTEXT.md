# PROJECT_CONTEXT — hockey-edge (Liiga + NHL prediction & edge tool)

Paste-ready summary for Claude project memory. Crystallized 2026-07-06; rewritten
2026-08-23.

Owned by the design chat. Emitted whole and replaced wholesale at session close. Not
edited by Human or by Claude Code sessions.

## Project

Niklas (IT student, Finland; Python/data/ML skills; Windows RTX 3060 Ti desktop + M1
Mac) is building a **personal-use-first hockey prediction tool for Liiga and NHL**.
Core loop: ingest historical data → capture pre-game info (confirmed lineups,
starting goalies, bookmaker odds) timestamped before puck drop → rolling features →
calibrated win probabilities → compare vs odds to surface edges → immutable,
timestamped prediction log.

It's an **edge finder** (model probability vs bookmaker odds), not a stats site and
not bare predictions. Primary user is Niklas himself; selling is optional and later.
If it sells, Liiga is the wedge (nobody models it, softer odds, local knowledge) and
the public timestamped track record is the entire marketing. No affiliate deals ever.

## Decided stack (don't re-open)

Python, SQLite, local compute, `src/hockey_edge/` package layout. **Packaging is now
resolved (2026-08-23)**: `pyproject.toml` exists and the package is installed
editable (`pip install -e .`) into a real `.venv`, so the old `PYTHONPATH=src` prefix
is gone — commands are plain `python -m hockey_edge...`. Note that the backfill still
must run **from the repo root**, because `data/`, `logs/`, and `data/raw/` are
relative paths; the install only removed the import-path prefix, not the
working-directory requirement.

Liiga data from liiga.fi's undocumented JSON API at `https://liiga.fi/api/v2`; 16
endpoints confirmed, catalog and `verified_seasons` in
`src/hockey_edge/ingest/liiga/endpoints.py` (source of truth). Official Liiga
lineups/goalies also mirrored at veikkaus.fi/kokoonpanot; liigakokoonpanot.com as
fallback; liiga.fi itself has no odds. NHL from the official free NHL API; NHL xG
bootstrapped from MoneyPuck/Natural Stat Trick.

Odds split across two free tiers: NHL via The Odds API (1 credit = all NHL games per
market+region, 500/mo — solved) and Liiga via OddsPapi. OddsPapi verified 2026-07-12:
billing per HTTP request, one `/v4/odds-by-tournaments` call returns the entire Liiga
fixture board at 1 request, Liiga `tournamentId=134` (not 34596 Auroraliiga or 48851
Hokiliiga). Empty board returns HTTP 404 with code `FIXTURE_NOT_FOUND`, not 200 +
`[]`. Guaranteed fallback: scrape Veikkaus — also the book actually bettable in
Finland, so "beat Veikkaus closing" is the practical benchmark if Pinnacle is
unavailable. Odds capture sits behind a swappable provider interface. Models:
Elo-style baseline + LightGBM, blended. Targets: NHL binary moneyline; Liiga
three-way regulation 1X2. Metrics: log loss + calibration, never accuracy; benchmark
is odds-implied probabilities (vig removed) from the last pre-game snapshot.

## Hard invariants

Pre-puck-drop information only (append-only snapshots with `captured_at` UTC);
immutable prediction log; strict walk-forward validation; resumable sync
(`sync_state` table, raw JSON cached as files under `data/raw/`, SQLite metadata
only); polite scraping (1.5s, normal UA); no autonomous git commits; no affiliate
integrations.

## Season numbering — CONFIRMED

The liiga.fi API uses the **ending-year convention**: `season=2024` is the 2023–24
season. Verified 2026-08-22 by probing seasons 2024–2027 and checking returned date
ranges. This was the source of a real gap — the original backfill covered 2015–2024
and silently omitted two completed seasons.

## Status (2026-08-23)

**Step 1 (Liiga ingest): COMPLETE for seasons 2015–2026.** `data/hockey.db` holds
**6,736 games** with composite `(season, game_id)` primary key — `game_id` is a
per-season counter that resets annually and is NOT globally unique. Zero collisions
verified across the full range. Seasons 2025 (614 games) and 2026 (605 games) were
backfilled 2026-08-22/23 after the season-numbering probe revealed they were missing;
both ran 100% clean in `sync_state` (zero failed_permanent, zero failed_retryable)
and both have full xG coverage on all competitive games — the 20/32 xG gaps in each
are 100% PRACTICE (preseason friendlies). These two seasons are the most valuable in
the dataset: full xG era, current league composition.

**`PLAYOUT` / `QUALIFICATIONS` serie values: CONFIRMED** (season 2025) — the
long-open question from `SCHEMA_DRAFT.md` design principle 3 is closed. Season 2025
has 5 PLAYOUT games (Pelicans–Jukurit, Jukurit 4–1) and 5 QUALIFICATIONS games
(Pelicans–Jokerit, Pelicans 4–1). Related gotcha: `tournament=playoffs` returns a
**superset** that already includes both — the tournament query param and the `serie`
field are not 1:1. No double-counting resulted (upsert + INSERT OR IGNORE), but the
backfill's printed summary over-reports total games.

**Step 2 (snapshot capture): skeleton only, and now the critical path.**
`src/hockey_edge/snapshot/` has the odds provider interface, a working
`OddsPapiProvider`, a stubbed `VeikkausProvider`, append-only SQLite storage
(`data/snapshots.db`), and a manually-runnable job. Odds parsing is stubbed (all rows
`parsed=False`), lineup capture is stubbed, scheduling/placement undecided.

**Step 3 (NHL ingest): not started.** Prompt not yet written. NHL doesn't start until
October, so it has slack Liiga doesn't.

## CRITICAL FINDING — liiga.fi historical data is MUTABLE

Confirmed 2026-08-23 by force-refetching `game_stats` for season 2024, `game_id=1`: it
now returns **3 `puckStats` period entries where the month-old cache has 1**, with
period-1 values bit-for-bit identical. liiga.fi retroactively enriches completed
games' responses after the initial fetch. This falsifies a core design assumption.

Two consequences, one deferred and one urgent:

1. **Deferred (archaeology, no deadline):** seasons 2015–2024 average ~0.95
   `game_puck_control` rows/game vs ~2.9 in 2025/2026 — roughly **11,000 unrecovered
   rows**, purely due to fetch timing, not availability. Recoverable via a
   `game_stats`-scoped `--force` refetch of ~5,517 games (~2.3h at 1.5s). Note
   `--force` currently operates per-season across ALL endpoints, which would triple
   the traffic for no evidenced benefit — it needs endpoint scoping first. Also
   untested: whether `shotmap`/`game_detail` enrich the same way. Cheap scope check
   before committing hours — refetch one old game across all three endpoints and diff
   content hashes against `raw_responses`. Puck control is not in any of the seven
   planned feature families, so this can wait until a quiet week.

2. **Urgent (affects this season):** `sync_state` skips the network entirely on
   `status='success'`, so staleness is **structurally undetectable** — `content_hash`
   is only compared after a refetch has already happened. For the live season this
   means fetching a game on game night, storing partial stats, marking success, and
   permanently locking in incomplete data. **The ingest path needs a staleness rule**
   — re-sync any game whose `start_utc` is within the last N days regardless of
   status, or add `last_verified_at` and re-check on an interval. This belongs in the
   snapshot-job work, not a follow-up backfill. The raw cache layer already handles
   mutation correctly (differing content writes a new hash-suffixed file, never
   overwrites) — only the sync layer is wrong.

## Other data-quality findings (logged, not chased)

- **Season 2025 shot-coordinate gap:** 79 RUNKOSARJA games (2025-02-15 → 2025-03-15)
  return empty-but-HTTP-200 `shotmap` responses, still empty on live refetch —
  league-wide, spread proportionally across all 16 teams, consistent with a tracking
  provider outage never backfilled. **Coordinates only**: all 79 have intact xG, team
  period stats, and non-null Corsi, so feature family 2 is unaffected. Only matters if
  a custom xG model is ever built (not in v1).
- **`sync_state` never tracked empty-but-200 responses** — only HTTP failures. A
  global audit across 2015–2026 is now recorded in `SCHEMA_DRAFT.md`; zero-row rates
  run 2–12% per season for `shot_events` (mostly PRACTICE), with 2025 the outlier at
  24.1%. Note the audit measures games with *zero* rows and therefore cannot see the
  puck-control deficit above, where games have one row instead of three.
- **Goal-event surplus is NOT a consistent pattern.** Season 2026 runs +62 vs the
  score sum (~1.8%); season 2025 runs +3. Previously assumed to be a uniform
  overturned-goal artifact; that no longer holds. Unexplained. Goals are the target
  variable and feed the special-teams features, so worth one diagnostic query
  eventually.

## Structural changes for 2026–27 (season starts 1 September 2026)

The league changed in ways that invalidate assumptions baked into `MODEL.md` and
`DATA_PIPELINE.md` (both still say "15 teams, 60 games"):

- **17 teams, 64 games each, 544 regular-season games.** K-Espoo joined in 2024–25
  (seasons 2025 and 2026 are 16 teams); Jokerit is promoted for 2026–27.
- **Bottom three relegate directly. No playout, no qualification series.** The
  `PLAYOUT`/`QUALIFICATIONS` values confirmed above will not appear in season 2027 —
  season 2025 was the last chance to observe them. This also reshapes the
  motivation-proxy feature family: relegation pressure is now a season-long signal at
  the bottom, not a late-season bracket.
- **Jokerit has zero rows in the 2015–2026 backfill.** The Elo baseline needs an
  explicit **promoted-team cold-start rule**, not just regression to the league mean.
- Playoff format: top 4 straight to quarterfinals, seeds 5–12 in a best-of-5 first
  round. Formats have varied across seasons — never assume a fixed bracket, carry
  whatever `serie` the API reports.
- **`season=2027` already returns all 544 fixtures** with `started=false` /
  `ended=false` (confirmed 2026-08-22). The snapshot job has its full game list nine
  days before puck drop.

## Immediate next step: snapshot job, before 1 September

This is the only work with a hard deadline — missed pre-game captures are
unrecoverable, unlike anything historical. Preseason games are being played right now,
which unblocks everything previously marked "blocked until games start":

1. **Odds parsing** — a live Liiga fixture board now exists, so the market/outcome
   JSON shape can finally be confirmed and `OddsPapiProvider` parsing implemented.
   Every prior call hit the off-season `FIXTURE_NOT_FOUND` case.
2. **Lineup pre-puck-drop test** — verify whether `game_detail`/`game_preview` expose
   lineups *before* puck drop for a scheduled game. Untested; blocks
   `snapshot/lineups.py`. This is a no-leakage-rule question, so test it explicitly
   rather than assuming.
3. **Staleness rule** (see CRITICAL FINDING above) — otherwise this season's data
   gets locked in partial.
4. **Scheduling and placement** — needs an always-on machine; still undecided.

## Deferred (explicitly, with reasons)

- `game_stats` refetch of 2015–2024 for puck-control periods — ~11k rows, no feature
  depends on it, needs endpoint-scoped `--force` first.
- NHL ingest (build-order step 3) — October start, has slack.
- Feature store, Elo baseline + validation harness, LightGBM blend.
- `player_info`/`player_list`/`team_info`/`teams_stats`/`milestones` — cataloged, no
  parser/table; `teams_stats` still needs a devtools capture.
- Public site, totals market, news-article injury parsing, football, payments.

## Open items

- Snapshot job scheduling/placement — needs an always-on machine, not decided.
- Failure alerting beyond CRITICAL log lines (email/Slack) — not built.
- Goal-event surplus variance — unexplained.
- Whether `shotmap`/`game_detail` retroactively enrich like `game_stats` — untested.
- `MODEL.md` and `DATA_PIPELINE.md` still state 15 teams / 60 games — needs updating
  to 17/64 plus the promoted-team cold-start rule.
- GitHub repo description: leaning "Edge finder for Liiga and NHL. Model
  probabilities vs bookmaker odds." — not final.
- Review liiga.fi ToS before anything public.
