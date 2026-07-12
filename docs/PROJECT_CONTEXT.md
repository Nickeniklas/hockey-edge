# PROJECT_CONTEXT — hockey-edge (Liiga + NHL prediction & edge tool)

Paste-ready summary for Claude project memory. Crystallized 2026-07-06; rewritten 2026-07-12.

Owned by the design chat. Emitted whole and replaced wholesale at session close. Not edited by Human or by Claude Code sessions.

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
Python, SQLite, local compute, plain venv + requirements.txt, `src/hockey_edge/`
package layout. Liiga data from liiga.fi's undocumented JSON API at
`https://liiga.fi/api/v2` — **endpoint discovery is done** (2026-07-12): endpoints
found by grepping the site's Vite JS bundle for axios call sites and confirming with
rate-limited curl (devtools not needed). 16 endpoints confirmed with real fixture
samples in `fixtures/liiga/`; catalog with per-endpoint gotchas and
`verified_seasons` lives in `src/hockey_edge/ingest/liiga/endpoints.py` (source of
truth). Games verified back to season 1976. Official Liiga lineups/goalies also
mirrored at veikkaus.fi/kokoonpanot; liigakokoonpanot.com as fallback; liiga.fi
itself has no odds. NHL from the official free NHL API; NHL xG bootstrapped from
MoneyPuck/Natural Stat Trick.

Odds split across two free tiers: NHL via The Odds API (1 credit = all NHL games per
market+region, 500/mo — solved) and Liiga via OddsPapi. **OddsPapi is verified
(2026-07-12)**: billing is per HTTP request, confirmed against the dashboard counter
(9/250 used across all tests). One `/v4/odds-by-tournaments` call returns the entire
Liiga fixture board at 1 request regardless of size, so the budget is bounded by
poll events per game night, not game count — 250/mo is comfortably enough for
open/mid/close captures. Liiga tournamentId is **134** (not 34596 Auroraliiga or
48851 Hokiliiga, both name-match false positives). Gotcha: an empty tournament board
returns HTTP 404 with code `FIXTURE_NOT_FOUND`, not 200 + `[]`. Guaranteed Liiga
fallback: scrape Veikkaus — also the book actually bettable in Finland, so "beat
Veikkaus closing" is the practical benchmark if Pinnacle is unavailable. Odds
capture sits behind a swappable provider interface. Models: Elo-style baseline +
LightGBM, blended. Targets: NHL binary moneyline; Liiga three-way regulation 1X2.
Metrics: log loss + calibration, never accuracy; benchmark is odds-implied
probabilities (vig removed) from the last pre-game snapshot.

## Hard invariants
Pre-puck-drop information only (append-only snapshots with `captured_at` UTC);
immutable prediction log; strict walk-forward validation; resumable sync from day
one (`sync_state` table, raw JSON cached as files under `data/raw/`, SQLite metadata
only); polite scraping; no autonomous git commits; no affiliate integrations.

## Status (2026-07-12)
- **Step 1 (Liiga ingest)**: endpoint discovery done (see above). Known endpoint
  gotchas documented in the catalog (e.g. `games_by_date`/`team_info` ignore the
  season param; `game_stats`/`shotmap` 500 on old seasons — recent-only data, skip
  during backfill). One entry (`teams_stats`) still 500s on every param combo and
  needs a real devtools capture. `docs/SCHEMA_DRAFT.md` not started — **next step:
  draft the SQLite schema against the real fixture shapes, then the ~10-season
  backfill.**
- **Step 2 (snapshot capture)**: skeleton built, not deployed.
  `src/hockey_edge/snapshot/` has the odds provider interface, a working
  `OddsPapiProvider`, a stubbed `VeikkausProvider`, append-only SQLite storage
  (`data/snapshots.db`), and a manually runnable job
  (`python -m hockey_edge.snapshot.job`) that logs to `logs/snapshot_job.log` and
  alerts at CRITICAL without crashing or writing partial data.

## Blocked until games start (Liiga preseason ~Aug–Sep)
- **Odds parsing**: every OddsPapi call so far hit the off-season FIXTURE_NOT_FOUND
  case, so the market/outcome JSON shape is unconfirmed. All snapshots are written
  with `parsed=False` and full `raw_payload`, so parsing can be implemented
  retroactively once a live fixture payload exists — missed capture is the only
  unrecoverable failure.
- **Lineup capture**: stubbed; blocked on testing whether `game_detail`/
  `game_preview` expose lineups *before* puck drop (untested — no scheduled games
  during the off-season check). Verify against a live pre-game fetch before wiring.
- **Odds-vs-model evaluation**: there is no historical Liiga odds source, so the
  edge track record only starts accumulating from the first captured season. The
  model itself can be fully built and walk-forward-validated on historical results
  before then.

## Off-season build window (everything here is doable now)
Schema draft → Liiga ~10-season backfill (resumable) → NHL ingest → feature store →
Elo baseline + validation harness → LightGBM blend. None of these depend on live
data. Goal: when the season starts, only parsing implementation, the lineup test,
and snapshot-job scheduling remain.

## Open items
- Snapshot job scheduling/placement: needs an always-on machine — not decided.
- Failure alerting beyond CRITICAL log lines (email/Slack hookup) — not built.
- `teams_stats` endpoint params — needs devtools capture.
- GitHub repo description: leaning "Edge finder for Liiga and NHL. Model
  probabilities vs bookmaker odds." — not final.
- Review liiga.fi ToS before anything public.
