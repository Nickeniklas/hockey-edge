# DATA_PIPELINE — contract

The pipeline has two distinct jobs with different failure modes. Keep them separate:
**backfill/sync** (historical, re-runnable, idempotent) and **snapshot capture**
(live, time-critical, unrecoverable if missed).

## Layer 1 — Historical ingest

### Liiga (the moat, and the ugly part)
- liiga.fi is a JS SPA; all data comes from an underlying JSON API at
  `https://liiga.fi/api/v2`. **Endpoint discovery is done** (2026-07-12) — instead of
  browser devtools, endpoints were found by grepping the site's Vite JS bundle for
  axios call sites, then confirming each candidate with a direct rate-limited curl.
  16 endpoints are confirmed with real fixtures; one (`teams_stats`) is a real path
  that 500/502'd on every param combination tried and still needs a devtools capture.
  The full catalog, per-endpoint gotchas, and `verified_seasons` live in
  `src/hockey_edge/ingest/liiga/endpoints.py` — that module is the source of truth,
  not this doc. Games are confirmed back to season=1976 (the league's first season).
  Community prior art (e.g. github.com/hmm/liigadata) was not needed in the end but
  remains a fallback reference if the API shape ever changes.
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
- Raw JSON responses cached **as files on disk** under `data/raw/<league>/<endpoint>/<hash>.json`
  (gitignored), with SQLite holding metadata only (`raw_responses`: url, fetched_at,
  content_hash, file_path) — decided 2026-07 in the first Claude Code session. Parsing
  can always be redone without refetching.

## Layer 2 — Snapshot capture (game-day job)

Runs on game days, scheduled (cron/Task Scheduler on an always-on machine — TBD).
Everything written **append-only with `captured_at` (UTC)**. Never update rows; new
information = new row. Rationale: "what was known before puck drop" is what the model
is allowed to see, and what makes validation honest.

Captures, per upcoming game, polled at increasing frequency as puck drop approaches:
1. **Confirmed lineups + starting goalies** — liiga.fi game `/kokoonpanot`;
   veikkaus.fi/kokoonpanot mirrors Liiga lineups early; liigakokoonpanot.com as
   manual-check fallback. NHL: official API starting goalies + lineups.
2. **Odds** — moneyline (and later totals), captured behind a small provider
   interface so the source is swappable without touching the rest of the pipeline:
   - **NHL: The Odds API free tier** — one call returns all NHL games for a
     market+region at 1 credit; 500 credits/mo = 16 pulls/day. Solved, no risk.
   - **Liiga: OddsPapi free tier** (Liiga listed on all plans, checked 2026-07).
     Full 250 req/mo budget goes to Liiga (~70–90 games/mo). A real free-tier key is
     now in the untracked `.env` (added 2026-07-12). Guaranteed fallback: scrape
     Veikkaus, which posts odds on every Liiga game — also the odds actually
     bettable in Finland.

     **OddsPapi verified 2026-07-12** (`scripts/oddspapi_probe.py`, 2 HTTP requests
     total, off-season so no live fixtures — see script docstring for how to re-run):
     - Base URL `https://api.oddspapi.io/v4`; auth is `apiKey=<key>` as a query
       param on every request (not a header).
     - **Liiga tournamentId = 134** (`GET /v4/tournaments?sportId=15`, ice hockey
       → 361 tournaments returned; `tournamentId=134` has `tournamentName='Liiga'`,
       `categoryName='Finland'`, `tournamentSlug='liiga'`). Two other candidates
       matched the naive "liiga" name/slug filter and must not be confused with
       this one: `34596` = Auroraliiga (Finland's *women's* league) and `48851` =
       Hokiliiga (Estonia, different country). Use `134` for all Liiga odds calls.
     - Billing semantics: **per HTTP request — CONFIRMED** against the OddsPapi
       dashboard counter (2026-07-12): total usage after all manual tests + probe
       runs was 9/250. The first manual call alone returned the full ice-hockey
       tournament list (361 tournaments, a large payload); per-fixture or
       per-item billing would have consumed far more than 9 for that alone. One
       call to `/v4/odds-by-tournaments?tournamentIds=134&bookmaker=<book>`
       returns the *entire* tournament's fixture board (all upcoming Liiga games
       with odds) in one response, billed as a single request regardless of
       payload size.
     - **GOTCHA**: when a tournament has zero fixtures with odds posted (our
       off-season case), `/odds-by-tournaments` returns **HTTP 404** with
       `{"error": {"code": "FIXTURE_NOT_FOUND", ...}}` — not `HTTP 200` with `[]`
       as originally assumed. The snapshot job must treat this specific 404/code
       combination as "no odds yet" (normal, not an alert-worthy failure), while
       still alerting on other 4xx/5xx or a missing/different error code.
     - Because one call returns the whole tournament board, polling budget is
       bounded by **poll events (calendar slots), not game count** — a single
       request during a multi-game Liiga night captures odds for every game that
       night at once. This makes 250 req/mo far less tight than the per-game
       framing suggests; see the request-pattern note below.
     - **RESOLVED 2026-09-17 — `/odds-by-tournaments` does carry prices
       in-season, and parsing is implemented.** The 2026-08-23 conclusion that
       the endpoint returns fixture metadata only (no `bookmakerOdds`) was the
       board being too early in the off-season, not a limit of the endpoint:
       an in-season call returned every posted fixture with a full
       `bookmakerOdds.<book>.markets` block. `bookmaker` is required and takes
       exactly one book, so a poll costs one request *per book*. Prices live at
       `markets.<marketId>.outcomes.<outcomeId>.players["0"].price` (decimal).
       Market `153` = 3-way regulation 1X2 (outcomes 153/154/155 =
       home/draw/away), market `151` = 2-way moneyline incl. OT (151/152 =
       home/away); `participant1Id` is the home side. `OddsPapiProvider` now
       parses both into `odds_snapshots`, keeping the full per-fixture JSON in
       `raw_payload` either way — see `docs/ODDS_PLAN.md` for the checks a row
       must pass to count as parsed, and the books compared (pinnacle +
       bet365 in use; betsson and coolbet failed, paf/unibet look mismapped).
     - **Join key**: `GET /v4/participants?sportId=15` resolves participant ids
       to names, but names are not unique (2–3 ids per Finnish club), so the
       job does not string-match. `snapshot/odds/oddspapi_teams.json` is a
       curated participant id → liiga.fi `teamId` map, each entry backed by a
       saved board fixture that matched exactly one real game; an unmapped
       participant leaves the row unresolved with a WARNING rather than
       guessing.
   - Multiple captures per game give open→close movement; if budget forces
     rationing, the **last capture before puck drop (≈ closing line) is the one
     non-negotiable poll** — it's the validation benchmark (Pinnacle preferred,
     Veikkaus closing as the practical benchmark otherwise).
   - **Liiga request pattern**: one `/odds-by-tournaments` pull (`tournamentIds=134`)
     per poll, covering every fixture currently on the board — the job polls the
     *tournament*, not individual games. Liiga plays ~70–90 games/mo but typically
     in batches on shared game nights (2–7 games/night), so the number of poll
     events/mo is much smaller than the game count. Budget check: even a generous
     schedule of one poll per game night plus one extra closing-line poll per
     night (~2 polls × ~15–20 game nights/mo ≈ 30–40 req/mo) leaves most of the
     250 req/mo budget unused — room for several intra-day captures (open, mid,
     close) per night rather than the tight per-game rationing originally assumed.
     Still guarantee the last-poll-before-puck-drop per game even on multi-game
     nights (poll close enough to the *earliest* puck drop that night, or poll
     per-game near each game's own drop time if spacing allows).

Missed capture = data gone forever. Job must alert on failure (even just a log/email),
and start running as early in the build as possible.

## Storage shapes (SQLite, one file per concern is fine)

Guideline shapes — final DDL decided in implementation, but keep these separations:

> **`game_id` alone does not identify a Liiga game.** liiga.fi reuses small
> per-season `game_id` counters for regular-season and preseason games, so
> every shape written `(game_id, ...)` below is keyed `(game_id, season)` in
> the shipped Liiga schema — `games`, `game_events`, and the per-game stats
> tables alike. Confirmed the hard way during the 2015–2024 backfill; see
> `docs/BACKFILL_RESULTS.md`. Check the NHL API's id semantics before
> assuming the same or the opposite there.

- `games` (game_id, league, season, date_utc, home, away, result fields, status)
- `game_events` (game_id, event_type, period, time, players…, raw payload ref)
- `players` / `rosters` (league-scoped IDs; **name normalization across sources is a
  known pain** — Liiga vs Veikkaus vs community spellings)
- `lineup_snapshots` — **as shipped** (2026-09-01, `snapshot/storage.py`), one
  append-only row per `(league, season, game_id, team_role)` per capture:
  team_name, window, captured_at, source, confirmed_player_count,
  confirmed_goalie_count, roster_json, starter_player_id/_name/_jersey,
  **starter_source + starter_confidence**, parsed, raw_payload. The two
  starter_* provenance columns are not decoration: liiga.fi has no literal
  "starter" field, so the starting goalie is an inference (currently
  `line == 1`) and downstream feature code must be able to see that. The
  original guideline shape here was (game_id, source, captured_at,
  goalie_confirmed?, payload) — superseded, and `goalie_confirmed` in
  particular was a boolean that would have hidden exactly the provenance the
  shipped columns expose.
- `odds_snapshots` (league, book, market, fixture_ref, captured_at, home_odds,
  draw_odds, away_odds, parsed, raw_payload, season, game_id,
  home/away_participant_id, start_utc) — append-only, as shipped 2026-09-17.
  Liiga/European books price regulation 1X2 three-way (`market='1x2_regulation'`);
  NHL moneyline is two-way incl. OT (`'moneyline_incl_ot'`, draw NULL) — market
  type is always explicit. Notes on the columns beyond the original guideline
  shape: `raw_payload` keeps the provider's full per-fixture JSON so parsing can
  be redone without refetching, and `parsed` says whether the price columns can
  be trusted (a row failing any check is stored unparsed rather than dropped).
  `season`/`game_id` are the resolved liiga.fi game and are **nullable on
  purpose** — a fixture that doesn't match exactly one discovered game stays
  unresolved rather than being guessed at, and the provider's own
  `participant_id`s/`start_utc` are kept so resolution can be redone later.
- `capture_windows` (league, season, game_id, **kind**, window, due_at, status,
  …) — job bookkeeping, not observational data, so it is updated in place.
  `kind` is 'odds' or 'lineups': the two capture independently, and one shared
  status let a successful odds poll hide a window from lineup capture.
  `satisfied_by` (odds only, added 2026-09-18) lists the books that priced the
  game on the satisfying poll, e.g. `pinnacle,bet365` or `bet365` — any book
  satisfies a window, so this is how a benchmark (Pinnacle) capture is told
  apart from a fallback-only one.
- `sync_state` (see above)
- `raw_responses` (url, fetched_at, content_hash, file_path) — metadata only; body
  lives under `data/raw/`

## Feature store (Layer 3) — contract with the model

Derived tables, rebuilt from raw at any time. Every feature row is keyed
(game_id, season) for Liiga — see the note under Storage shapes — computed
strictly from data with timestamps **before that game's puck drop**. Feature families (from planning discussion, in signal order):

1. Goalie: rolling save% (vs expected where xG exists) for the **confirmed starter**
2. Team shot quality: rolling xG/Corsi for & against per 60 at EV, windows 10/25/40
3. Special teams: PP/PK rates, penalty draw/take — regress hard to league mean early
4. Schedule: back-to-back, games in last 6 days, travel/TZ (NHL only — irrelevant in
   Liiga's geography)
5. Home advantage: league constant + team offsets (bigger in Liiga than NHL)
6. Roster availability: share of team ice time missing (crude version OK)
7. Motivation proxy: playoff-probability delta late season; Liiga relegation
   pressure at the bottom. **From 2026-27, bottom-three relegate directly —
   no playout/qualification bracket** (confirmed empty for season=2027;
   `PLAYOUT`/`QUALIFICATIONS` `serie` values were real through season 2025,
   see `docs/SCHEMA_DRAFT.md`). This makes relegation pressure a season-long
   signal for the bottom teams rather than a late-season bracket the way it
   was in seasons with a playout round.

Explicitly excluded: win/loss streaks, head-to-head history, player point streaks
(noise / superstition).

### Known data gaps the store must respect (after the 2026-09-24 recovery)
Details and per-game lists are in `docs/RECOVERY_BACKLOG.md`.
- **A competitive game with zero `game_penalty_events` is missing data, not
  a clean game.** Seven remain in 2015–2026: six RUNKOSARJA (2016:7862,
  2017:4409, 2020:252, 2021:480, 2022:317, 2022:332) and one PLAYOFFS
  (2022:49298). None occurs in the 960 clean 2025/2026 regular-season
  games. Special-teams features skip these games.
- **2021:480 is damaged at source.** The final is 0–1, but it has no goal
  events and no goalkeeper events.
- **Raw penalty-event counts are not power-play opportunities.** Some games
  (heavily 2015) list misconduct (`VKV`) and untyped entries as separate
  events: 36 vs 16 PIM per game, and 83% vs 98% two-minute minors. A
  2026-09-24 refetch confirmed this comes from the source, not from damage.
  Count minors (`penalty_minutes = 2`) or derive power plays from penalty
  timing.
- `game_puck_control` for 2015–2024 is still thin (~1 row per game against
  ~3). The `game_stats` recovery (RECOVERY_BACKLOG section 2) has not run.
- A penalty `player_id` of 0 means no individual player, mostly the team
  penalty. It is not a player.

## Gotchas
- Leakage sneaks in via post-game box scores used to build "pre-game" features —
  the `captured_at` discipline exists to catch exactly this.
- Liiga season structure keeps changing — never assume a fixed bracket, carry
  whatever `serie`/`playOffPhase` the API reports per game. 2024-25: top-4
  straight to quarterfinals, 5–12 play a best-of-5 first round, playout
  best-of-7 at the bottom. **2026-27: 17 teams (Jokerit promoted), 64-game
  regular season, bottom three relegate directly — no playout/qualification
  round at all** (season=2027's `games_by_season` returns zero `PLAYOUT`/
  `QUALIFICATIONS` games, confirmed 2026-08-23). Regular season vs. playoffs
  must be flagged per game regardless of format.
- Liiga season length isn't fixed either: 60 games historically, 64 from
  2026-27 (16 teams in 2024-25/2025-26 after K-Espoo joined, 17 from
  2026-27). Whatever the length, early-season features are mostly prior;
  blend with league-mean priors instead of trusting 5-game windows.
