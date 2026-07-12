# PROJECT_CONTEXT — hockey-edge (Liiga + NHL prediction & edge tool)

Paste-ready summary for Claude project memory. Crystallized 2026-07-06.

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
Python, SQLite, local compute. Liiga data from liiga.fi's undocumented JSON API
(it's a JS SPA; endpoints found via devtools; game pages at
`liiga.fi/fi/peli/{season}/{gameId}/...` incl. `/kokoonpanot` lineups). Official
Liiga lineups/goalies also mirrored at veikkaus.fi/kokoonpanot;
liigakokoonpanot.com as fallback. NHL from the official free NHL API; NHL xG
bootstrapped from MoneyPuck/Natural Stat Trick. Odds are split across two free
tiers: NHL via The Odds API (1 credit = all NHL games per market+region, 500/mo —
solved, no risk) and Liiga via OddsPapi (250 req/mo, all budget for Liiga's ~70–90
games/mo; billing semantics per-fixture vs per-board must be verified with a test
key before the snapshot job is built). Guaranteed Liiga fallback: scrape Veikkaus,
which posts odds on every Liiga game and is the book actually bettable in Finland —
so "beat Veikkaus closing" is the practical benchmark if Pinnacle (via OddsPapi)
is unavailable. Odds capture sits behind a swappable provider interface. liiga.fi
itself has no odds — lineups/goalies only. Models: Elo-style baseline +
LightGBM, blended (GBM alone = overconfident early-season). Targets: NHL binary
moneyline; Liiga three-way regulation 1X2. Metrics: log loss + calibration, never
accuracy; benchmark is odds-implied probabilities (vig removed) from the last
pre-game odds snapshot.

## Hard invariants
Pre-puck-drop information only (append-only snapshots with `captured_at`);
immutable prediction log; strict walk-forward validation; resumable sync designed in
from day one (`sync_state` table, cached raw responses) — lesson from Niklas's
eduskunta-analysis project; polite scraping; no autonomous git commits.

## Build order
Liiga backfill (~10 seasons) → game-day snapshot job (deploy EARLY — missed live
data is unrecoverable) → NHL ingest → feature store → Elo + validation harness →
LightGBM blend → prediction log + local dashboard. Deferred: public site, totals
market, news-article parsing, football, payments.

## Feature families (signal order)
Confirmed-starter goalie rolling save% (vs expected); rolling EV xG/Corsi for &
against per 60 (windows 10/25/40); special teams rates (regressed hard early);
schedule fatigue (B2B, density; travel NHL-only); home advantage (bigger in Liiga);
roster availability (share of ice time missing); late-season motivation proxy.
Excluded as noise: streaks, head-to-head, point streaks.

## Tooling (decided in first Claude Code session)
Plain venv + requirements.txt; src/hockey_edge/ package layout; raw JSON cached as
files under data/raw/ (gitignored) with SQLite holding metadata only; endpoint
catalog isolated in src/hockey_edge/ingest/liiga/endpoints.py.

## Open items
Where the always-on snapshot job runs; OddsPapi billing semantics (per-fixture vs
per-sport-board — verifiable now with a free test key, do before building the
snapshot job); Liiga book depth / how early lines post (in-season check only);
liiga.fi ToS review before anything public; Liiga
shot-coordinate availability
(affects whether own Liiga xG model is possible later).
