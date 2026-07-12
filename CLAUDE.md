# CLAUDE.md — hockey-edge operating brief

## What we're building
**hockey-edge**: personal-use-first hockey prediction & edge tool for **Liiga + NHL**: data pipelines
→ feature store → calibrated win probabilities → compared against bookmaker odds →
immutable prediction log. Liiga is the differentiator (nobody models it; softer odds).
Full plan: `docs/PLAN.md`. Data contract: `docs/DATA_PIPELINE.md`. Model contract:
`docs/MODEL.md`. Read them before structural work.

## Decided stack — do not re-litigate without being asked
Python · SQLite · liiga.fi JSON API (endpoints via devtools discovery) · NHL official
API · MoneyPuck/NatStatTrick for NHL xG bootstrap · lineups from liiga.fi
`/kokoonpanot` + veikkaus.fi mirror (liiga.fi has no odds — lineups/goalies only) ·
odds: NHL via The Odds API free tier (1 credit = all NHL games per market+region),
Liiga via OddsPapi free tier (listed on all plans, checked 2026-07; verify
per-fixture vs per-board billing with a test key before building the snapshot job;
guaranteed fallback = scrape Veikkaus, which is also the book actually bettable in
Finland) · odds capture behind a swappable provider interface · Elo baseline +
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
- **Step 1 (Liiga ingest) in progress, endpoint discovery not yet done.** Scaffolding
  is built: `src/hockey_edge/` package, `requirements.txt`, `.gitignore`, and the
  endpoint catalog module `src/hockey_edge/ingest/liiga/endpoints.py`. Its 3 entries
  (`game_kokoonpanot`, `stats_en`, `pelaajat_fi`) are **UNCONFIRMED placeholders**
  copied from `docs/DATA_PIPELINE.md` — none verified against real devtools traffic
  yet, `verified_seasons` is empty on all of them.
- `fixtures/liiga/` convention is set up (see `fixtures/liiga/README.md`) but has no
  files in it yet — no real sample responses captured.
- `docs/SCHEMA_DRAFT.md` not started; blocked on seeing real payload shapes first.
- **Next step**: paste liiga.fi devtools network captures (schedule page first, per
  plan) so placeholders can be replaced with confirmed URLs + a fixture sample each;
  then draft the SQLite schema.

## Gotchas
- Liiga playoff format changed 2024-25; flag season phase per game; formats vary by season.
- Liiga odds are three-way (regulation 1X2); NHL moneyline is two-way incl. OT. Store market type.
- Player-name normalization across sources (liiga.fi vs Veikkaus vs community) is a known pain.
- Liiga small samples: regress early-season features hard to league mean.
- Snapshot job needs an always-on machine — placement not yet decided (open item).

## Secrets
Odds API keys via environment / untracked `.env`. Never commit keys. If the repo goes
public, gitignore any personal betting/bankroll data (same pattern as market-advisor's
gitignored portfolio.md).
