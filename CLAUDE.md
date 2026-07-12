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

Tooling (decided session 1): plain venv + requirements.txt (no uv/poetry) ·
src/hockey_edge/ package layout · raw JSON cached as files under data/raw/
(gitignored), SQLite stores metadata only · each `Endpoint` in the catalog module
tracks `verified_seasons`, since historical seasons may use different endpoints
or shapes · one real sample response per verified endpoint/season checked into
`fixtures/liiga/<name>/<season>.json` (git-tracked, unlike `data/raw/`).

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

## Status (as of 2026-07-12)
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
  `game_stats`/`shotmap` 500 for old seasons (recent-seasons-only data, not a
  broken endpoint — skip and move on during backfill); whether `game_detail`/
  `game_preview` expose lineups *before* puck drop is untested (no games were
  scheduled during the 2026-07 off-season check) — verify against a live pre-game
  fetch before wiring the snapshot job, per the no-leakage rule.
- `docs/SCHEMA_DRAFT.md` not started; next step is drafting the SQLite schema
  against these real fixture shapes.
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
- Liiga playoff format changed 2024-25; flag season phase per game; formats vary by season.
- Liiga odds are three-way (regulation 1X2); NHL moneyline is two-way incl. OT. Store market type.
- Player-name normalization across sources (liiga.fi vs Veikkaus vs community) is a known pain.
- Liiga small samples: regress early-season features hard to league mean.
- Snapshot job needs an always-on machine — placement not yet decided (open item).
- OddsPapi returns HTTP 404 with `code: "FIXTURE_NOT_FOUND"` for a tournament
  with no fixtures currently posted, not `HTTP 200` with `[]` — the snapshot job
  handles this explicitly as "no odds yet," not a failure; don't reintroduce a
  bare `raise_for_status()` that would misclassify it.

## Secrets
Odds API keys via environment / untracked `.env`. Never commit keys. If the repo goes
public, gitignore any personal betting/bankroll data (same pattern as market-advisor's
gitignored portfolio.md).
