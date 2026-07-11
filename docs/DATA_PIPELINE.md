# DATA_PIPELINE — contract

The pipeline has two distinct jobs with different failure modes. Keep them separate:
**backfill/sync** (historical, re-runnable, idempotent) and **snapshot capture**
(live, time-critical, unrecoverable if missed).

## Layer 1 — Historical ingest

### Liiga (the moat, and the ugly part)
- liiga.fi is a JS SPA; all data comes from an underlying JSON API. **First task is
  endpoint discovery via browser devtools** (network tab while browsing games,
  schedules, stats pages). Known URL shapes to start from:
  - game pages: `liiga.fi/fi/peli/{season}/{gameId}/...` (incl. `/kokoonpanot`)
  - stats: `liiga.fi/en/stats` (skater/goalie/team), `liiga.fi/fi/pelaajat`
  - community prior art exists (e.g. github.com/hmm/liigadata parses liiga.fi game
    data to JSON) — read for endpoint hints, don't depend on it
- Backfill target: ~10 seasons of games, per-game events (goals, penalties, shots if
  available), rosters, goalie stats, team stats. Take whatever granularity the API
  gives; shot coordinates may or may not exist — record what's there.
- Polite scraping: rate-limit requests, cache raw responses to disk, identify with a
  normal UA. Review liiga.fi ToS before anything public.

### NHL
- Official NHL API: schedules, box scores, play-by-play with shot coordinates,
  rosters, starting goalies. Free.
- Bootstrap advanced stats (xG, Corsi) from MoneyPuck / Natural Stat Trick downloads
  instead of computing own xG initially.

### Sync strategy (both leagues)
- **Resumable by design**: a `sync_state` table tracks per-entity status
  (game_id, endpoint, fetched_at, http_status, content_hash). Re-running sync is
  always safe and only fetches what's missing/stale. This was the painful retrofit
  in eduskunta-analysis — here it's day-one design.
- Raw JSON responses stored verbatim (raw table or files) so parsing can be redone
  without refetching.

## Layer 2 — Snapshot capture (game-day job)

Runs on game days, scheduled (cron/Task Scheduler on an always-on machine — TBD).
Everything written **append-only with `captured_at` (UTC)**. Never update rows; new
information = new row. Rationale: "what was known before puck drop" is what the model
is allowed to see, and what makes validation honest.

Captures, per upcoming game, polled at increasing frequency as puck drop approaches:
1. **Confirmed lineups + starting goalies** — liiga.fi game `/kokoonpanot`;
   veikkaus.fi/kokoonpanot mirrors Liiga lineups early; liigakokoonpanot.com as
   manual-check fallback. NHL: official API starting goalies + lineups.
2. **Odds** — moneyline (and later totals) from 1–2 books. OddsPapi free tier
   (Pinnacle incl.) for NHL; Liiga coverage unverified → possibly scrape
   Veikkaus/Pinnacle. Multiple captures per game give open→close line movement,
   and the **last capture before puck drop ≈ closing line** = validation benchmark.

Missed capture = data gone forever. Job must alert on failure (even just a log/email),
and start running as early in the build as possible.

## Storage shapes (SQLite, one file per concern is fine)

Guideline shapes — final DDL decided in implementation, but keep these separations:

- `games` (game_id, league, season, date_utc, home, away, result fields, status)
- `game_events` (game_id, event_type, period, time, players…, raw payload ref)
- `players` / `rosters` (league-scoped IDs; **name normalization across sources is a
  known pain** — Liiga vs Veikkaus vs community spellings)
- `lineup_snapshots` (game_id, source, captured_at, goalie_confirmed?, payload)
- `odds_snapshots` (game_id, book, market, captured_at, home_odds, away_odds, draw_odds)
  — Liiga/European books price regulation 1X2 three-way; NHL moneyline is two-way
  incl. OT. Store market type explicitly.
- `sync_state` (see above)
- `raw_responses` (url, fetched_at, body or file ref)

## Feature store (Layer 3) — contract with the model

Derived tables, rebuilt from raw at any time. Every feature row is keyed
(game_id, computed strictly from data with timestamps **before that game's puck
drop**). Feature families (from planning discussion, in signal order):

1. Goalie: rolling save% (vs expected where xG exists) for the **confirmed starter**
2. Team shot quality: rolling xG/Corsi for & against per 60 at EV, windows 10/25/40
3. Special teams: PP/PK rates, penalty draw/take — regress hard to league mean early
4. Schedule: back-to-back, games in last 6 days, travel/TZ (NHL only — irrelevant in
   Liiga's geography)
5. Home advantage: league constant + team offsets (bigger in Liiga than NHL)
6. Roster availability: share of team ice time missing (crude version OK)
7. Motivation proxy: playoff-probability delta late season; Liiga playout/relegation
   dynamics at the bottom

Explicitly excluded: win/loss streaks, head-to-head history, player point streaks
(noise / superstition).

## Gotchas
- Leakage sneaks in via post-game box scores used to build "pre-game" features —
  the `captured_at` discipline exists to catch exactly this.
- Liiga season structure changed 2024-25 (top-4 straight to quarterfinals, 5–12 play
  a best-of-5 first round; playout best-of-7 at the bottom) — regular season vs
  playoffs must be flagged per game, and formats differ across historical seasons.
- 60-game Liiga seasons → early-season features are mostly prior; blend with
  league-mean priors instead of trusting 5-game windows.
