# PLAN — hockey-edge (Liiga + NHL prediction & edge tool)

*Crystallized 2026-07-06 from planning conversation.*

## What this is

A personal-use-first hockey prediction tool covering **Liiga and NHL**. It ingests
historical game data, captures pre-game information (confirmed lineups, starting
goalies, bookmaker odds) with timestamps, computes rolling features, and produces
calibrated win probabilities that are compared against bookmaker odds to surface
disagreement ("edges"). Every prediction is logged immutably with the odds available
at prediction time, building a verifiable track record.

**Primary user is Niklas himself.** Selling is optional and later. If it ever sells,
the differentiator is Liiga: nobody models it seriously, odds on it are softer, and
the public track record page becomes the entire marketing. No hustle-selling required
for the build to be worth it — worst case is a great personal tool.

## Why this project (decision trail)

- Chosen over contact-enrichment (too close to day job at Professio), QR analytics
  (zero moat, door-to-door sales), niche platform analytics (no candidate found),
  and report templates (SEO business, boring).
- Survives model/AI hype cycles: the moat is the data pipeline, features, and
  accumulated timestamped track record — not any LLM.
- Product framing: **edge finder** (model probability vs bookmaker odds), not a
  stats explorer (free sites do it better) and not bare predictions (meaningless
  without a reference point).
- Football deferred indefinitely. Hockey alone covers Oct–June across two leagues.
- **Never** take bookmaker affiliate deals (affiliate revenue pays when users lose —
  conflict with an honest analysis product).

## Scope

### v1 (MVP, personal use)
- Liiga historical backfill (~10 seasons) into SQLite
- Pre-game snapshot capture job (lineups, starting goalies, odds) — **starts running
  as early as possible**, because this data only exists live and is unrecoverable
- NHL pipeline (official free NHL API)
- Feature store (rolling team form, goalie form, special teams, schedule, home adv.)
- Elo-style baseline model → LightGBM → blend
- Walk-forward validation harness benchmarked against odds-implied probabilities
- Immutable prediction log + simple local dashboard/report

### Later (explicitly deferred)
- Public website with track record page (the sales asset, if selling ever happens)
- Totals (over/under) market — noted as often softer than moneyline; second market
- News-article parsing (Jatkoaika/MTV) for injury signals — enrichment, not MVP
- Football leagues
- Payments / subscriptions

## Stack & key decisions

| Decision | Choice | Why |
|---|---|---|
| Language | Python | Niklas's main language; whole ML ecosystem |
| Storage | SQLite | Proven pattern from eduskunta-analysis; single-file, easy resumable sync |
| Liiga data | liiga.fi underlying JSON API (SPA) | Site is a JS SPA → JSON API behind it; endpoints discovered 2026-07-12 via JS-bundle grep + direct curl (see `endpoints.py`). Building a clean Liiga dataset IS the moat |
| Lineups/goalies | Official pre-game lineups on liiga.fi (`/fi/peli/{season}/{gameId}/kokoonpanot`), mirrored by veikkaus.fi/kokoonpanot; liigakokoonpanot.com as fallback | No journalist-article NLP needed for MVP — structured official data exists, just capture it timestamped before puck drop |
| NHL data | Official NHL API | Free, documented, Niklas has used it (nhl-stats-app) |
| NHL advanced stats bootstrap | MoneyPuck / Natural Stat Trick downloads | Skip computing own xG at first; replace later if needed |
| Odds — NHL | The Odds API free tier (500 credits/mo; one call = all NHL games for a market+region = 1 credit, checked 2026-07) | 16 pulls/day covers closing captures with room to spare; NHL odds solved for free, zero risk |
| Odds — Liiga | OddsPapi free tier (250 req/mo; Liiga confirmed listed on all plans, checked 2026-07 off-season). **Guaranteed fallback: scrape Veikkaus**, which posts odds on every Liiga game | Splitting leagues across two free tiers frees the whole OddsPapi budget for Liiga (~70–90 games/mo → closing + 1–2 earlier captures per game even under worst-case per-fixture billing). Veikkaus odds are also the odds actually bettable in Finland, so a Veikkaus edge is the actionable one |
| Odds benchmark | Pinnacle closing (via OddsPapi) preferred; **Veikkaus closing as the practical benchmark** if Pinnacle unavailable | Pinnacle = sharp/academic benchmark; Veikkaus closing measures the edge at the book Niklas can actually use |
| Models | Elo/ridge baseline + LightGBM, blended | GBM alone is overconfident and drifts early-season; Elo is calibrated and works on small data (Liiga: 15 teams, 60-game seasons) |
| Metrics | Log loss + calibration, NOT accuracy | Product is probabilities vs odds; miscalibration produces fake edges |
| Validation | Strict walk-forward by season, pre-puck-drop info only | The place these projects usually lie to themselves |
| Compute | Local machines (RTX 3060 Ti desktop / M1 Mac) | GBM is CPU-trivial; no cloud needed for v1 |

## Architecture sketch

```
liiga.fi JSON API ──┐
NHL API ────────────┤→ [ingest: backfill + resumable sync] → SQLite (raw, append-only)
lineups (liiga.fi/  │
 veikkaus mirror) ──┤→ [snapshot job: game-day capture, captured_at on every row]
odds (OddsPapi /    │
 scrape fallback) ──┘
                          ↓
                   [feature store: derived tables,
                    strictly pre-puck-drop info]
                          ↓
                   [models: Elo baseline + LightGBM blend]
                          ↓
                   [prediction log (immutable) + local dashboard]
```

## Build order

1. **Liiga ingest** — endpoint discovery, schema, backfill ~10 seasons, resumable sync
   (designed in from day one, not retrofitted — lesson from eduskunta-analysis)
2. **Snapshot capture job** — lineups/goalies/odds on game days; deploy early even
   with no model, the data is unrecoverable
3. **NHL ingest** — mostly plumbing an existing API
4. **Feature store** — shared across leagues
5. **Elo baseline** + validation harness (log loss vs odds-implied)
6. **LightGBM** + blend
7. **Prediction log + local dashboard**

## Hard rules / invariants

- No feature may use information not available before puck drop (`captured_at` guards this)
- Prediction log rows are never edited or deleted
- Raw ingested data is append-only; corrections happen in derived layers
- Validation is walk-forward only; never shuffle games across time
- No bookmaker affiliate integrations, ever

## Known risks / open items

- **Liiga API is undocumented** — endpoints can change; scraping maintenance is the
  ongoing cost. Accepted: it's also the moat.
- **OddsPapi billing confirmed (2026-07-12)**: per HTTP request, verified against
  the dashboard usage counter (9/250 used across all manual tests + the probe
  script) — see `docs/DATA_PIPELINE.md` for detail, including Liiga's
  tournamentId (134). Liiga book depth / how early lines post still can only be
  verified in-season. Veikkaus scrape fallback stays in reserve either way.
- **Snapshot job needs an always-on machine** — where it runs (desktop, Mac, small
  VPS) not yet decided.
- **Small Liiga samples** — 15 teams × 60 games; wider uncertainty bands, regress
  early-season stats hard to league mean. Accepted as part of the challenge.
- **Season = Oct–June** — off-season is for backfill/model work.
- **liiga.fi terms of service** — review before any public/commercial use of the data.
