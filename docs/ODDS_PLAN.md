# ODDS_PLAN — Liiga odds capture via OddsPapi (drafted 2026-09-17)

> **Status 2026-09-18: Phases 0–3 built; Phase 4 (watch a live game night) is
> the only one open** — first full game night is 2026-09-18. Live so far:
> every poll 200 on both books since the 20 s spacing, retry gap working,
> 0 odds windows missed. See CLAUDE.md's 2026-09-18 Status entry.
>
> **Record from 2026-09-17:** Phase 0 spent exactly 2 requests: **bet365 passed and is
> the second book** (all 5 board fixtures had market 153 with 3 active prices,
> overround 1.059–1.079, home/away and start matching `hockey.db`); **betsson
> failed** (2 fixtures only, one with no odds at all, the other suspended with
> every price inactive — response saved as the record). Phase 1 parsing lives
> in `snapshot/odds/oddspapi.py`; Phase 2's `capture_windows.kind` migration
> ran on `data/snapshots.db` (backup: `data/snapshots.pre-kind-migration.db`,
> 117 lineup rows unchanged, 27 odds twins added) and the curated team map is
> `snapshot/odds/oddspapi_teams.json` — all 17 teams: 14 from the saved
> boards, then HIFK 3839, Kärpät 3835 and HPK 3837 the same evening, each
> mapped from the first board it appeared on, never from the participants
> list. Phase 3 wired `OddsPapiProvider` into
> `job.py` for `["pinnacle", "bet365"]`. Tests: `python -m unittest discover
> -s tests` (31 as of 2026-09-18).
>
> **One addition to Phase 3 not in the original plan: `ODDS_RETRY_GAP`.** A
> window stays pending until a book prices its game, so an
> unposted fixture would otherwise be re-polled every 15 min from T-24h to
> puck drop (~80 requests on one game). A due window whose fixture was already
> looked for by a successful poll less than 60 min ago is skipped; **closing
> windows are exempt**, since a late-posted closing price is the one
> non-negotiable capture. This needed no extra schema: a board poll covers
> every posted fixture, so `api_usage.requested_at` at or after a window's
> `due_at` already records an attempt on it.
>
> **Changed 2026-09-18: a window is satisfied by ANY book, not Pinnacle
> only.** Phase 3 as written satisfied a window only on a Pinnacle price. The
> first live evening showed why that's wrong: Pinnacle had no
> KooKoo–SaiPa (2026-09-18) on either 2026-09-17 poll while bet365 did — it
> only posted it at 11:00 the next day — so pinnacle-only would have left a
> game we held good odds for pending, and missed had Pinnacle never posted. Now either book satisfies
> it, `capture_windows.satisfied_by` records which (`pinnacle,bet365` or
> `bet365`), and a fallback-only capture logs a WARNING because it lacks the
> benchmark line. The `ODDS_RETRY_GAP` rationale above still holds for a game
> on no board at all.

Implementation plan for replacing `NullOddsProvider` with real Liiga odds.
Liiga only — NHL (The Odds API) is a separate, later session.

## Facts this plan rests on (recon 2026-09-17, 3 OddsPapi requests)

Saved (git-tracked): `fixtures/oddspapi/odds-by-tournaments_134_pinnacle_2026-09-17.json`
(full board, 7 fixtures, Pinnacle prices) and
`fixtures/oddspapi/participants_sport15_2026-09-17.json` (full id→name map).
The 11.6 MB `/v4/odds` all-bookmaker response was not kept; its findings are
recorded below.

- `GET /v4/odds-by-tournaments?tournamentIds=134&bookmaker=<one>` **does carry
  full prices in-season.** Returned all 7 posted Liiga fixtures (~2.5 days
  ahead) each with `bookmakerOdds.<book>.markets`. August's metadata-only
  response was the board being too early, not the endpoint lacking prices.
  One request = every fixture on the board, for exactly one bookmaker
  (`bookmaker` is required and single-valued).
- `GET /v4/odds?fixtureId=` returns all 213 bookmakers for one fixture — 11.6 MB.
  Not for polling. **Veikkaus is not among the 213.**
- Market ids (Pinnacle, Jokerit–JYP 2026-09-17):
  - `153` = 3-way regulation 1X2 (`bookmakerMarketId` ends `/6/moneyline`).
    Outcomes `153`/`154`/`155` = home/draw/away — **inferred**: consistent with
    the 2-way prices and with participant1 being home. Phase 1 must confirm.
  - `151` = 2-way moneyline incl. OT (`/0/moneyline`), outcomes `151`/`152` =
    home/away. `151281` = period-1 moneyline; `1528`/`151287`… totals; spreads.
  - Price path: `markets.<mid>.outcomes.<oid>.players["0"].price` (decimal),
    with `active` and `changedAt` alongside.
- `participant1Id` = **home** — confirmed against `hockey.db` for all 3 games on
  2026-09-17 (Jokerit, Lukko, TPS home).
- `GET /v4/participants?sportId=15` = 1,554 hockey teams, id→name. 14 of the 17
  Liiga teams were seen on the board so far. **Names are NOT unique**: HIFK
  Helsinki = ids 3839, 372432, 668713; Kärpät Oulu = 3835, 668723; HPK
  Hameenlinna = 3837, 372434, 668721 (likely junior/women's sides). 3839 is
  the senior HIFK (it was TPS's opponent on the 2026-08-23 board, matching the
  real TPS–HIFK game 2026-09-05). 3835/3837 are the likely senior Kärpät/HPK
  (same contiguous 3822–3846 range as the other senior teams) — confirm when
  they first appear on a board, before trusting the mapping.
- Second-bookmaker check, same game, from the saved per-fixture response:

  | book | has 1X2 (153) | 1X2 overround | note |
  |---|---|---|---|
  | pinnacle | yes | 1.072 | primary |
  | betsson / nordicbet | yes | 1.052 | identical prices (same group) |
  | bet365 | yes | 1.062 | |
  | paf / unibet | yes | **1.30** | implausible — likely bad mapping or a different market; do not use unverified |
  | coolbet | **no** | — | only 2-way; ruled out for 1X2 |

  n=1 game. Phase 0 verifies on the full board before committing.

- Budget, counted from the season-2027 schedule (distinct 15-min poll ticks,
  remaining regular season): **3 windows = 39–91 polls/month per bookmaker**
  (peak Oct). Two bookmakers = max 182/month, under the job's 200 ceiling and
  the 250 tier.

## Blocking bug to fix before wiring (found while planning)

`capture_windows` has **one shared `status`** for odds and lineups.
`run_once` marks every due window `satisfied` after a successful odds poll,
then `capture_lineups` calls `get_due_windows` (which filters `status =
'pending'`) and gets nothing. **Wiring a real odds provider as-is would silently
stop lineup capture.** It only works today because `NullOddsProvider` never
returns snapshots. Needs per-kind satisfaction (see Phase 2). This is a schema
change to a reviewed table → confirm with user before doing it.

## Phases

### Phase 0 — confirm the second book (2 requests, exact)
1. Board call `bookmaker=betsson`, board call `bookmaker=bet365`.
2. For each: every fixture has market `153` with 3 active prices, overround
   within ~1.02–1.12.
3. User has no strong preference — **default to bet365** if both are clean;
   pick Betsson only if bet365 fails the checks. Save the chosen book's board
   response to `fixtures/oddspapi/` (Pinnacle's is already saved).

Stop condition: if neither is clean, report and ask — don't try more books
without saying how many requests.

### Phase 1 — parsing (`snapshot/odds/oddspapi.py`, 0 requests)
- Parse from the board response per fixture: market `153` → one row
  `market='1x2_regulation'` (home/draw/away), market `151` → one row
  `market='moneyline_incl_ot'` (home/away, draw NULL).
- `parsed=True` only if all outcomes present, `active`, prices > 1.0, and
  overround within a sanity band; otherwise `parsed=False` + WARNING, raw kept.
- Keep full per-fixture JSON in `raw_payload` (per book) — reparse must stay
  possible without refetching.
- Outcome-mapping confirmation: check favourite direction against 2-way prices
  on every fixture in the Phase 0 samples; unit tests from the fixtures.

### Phase 2 — join + schema (needs user OK before touching schema)
- **Team mapping as a curated table/fixture**, not string-matching in code
  (cleaning-layer rule): `oddspapi_participant_id → liiga.fi team_id`, all 17
  teams, hand-verified. Unknown participant → WARNING, row stays unresolved.
- `odds_snapshots`: add nullable `season`, `game_id`, `home_participant_id`,
  `away_participant_id`, `start_utc`. Resolved at capture by (home team, away
  team, start date) against `discovered_fixtures`; `startTime` is only a
  tie-breaker. Table has 0 rows → migration is trivial.
- `capture_windows`: split satisfaction per capture kind (either a `kind`
  column with rows per kind, or `odds_status`/`lineup_status`). Existing
  87+ rows' current status belongs to lineups; odds status starts `pending`
  for future windows only. Pick the variant that keeps `mark_missed_windows`
  simplest.

### Phase 3 — wire into `job.py` (0 requests until run)
- `OddsPapiProvider` polled once per book per tick when any odds window is due
  (books list: `["pinnacle", <chosen>]`).
- One `api_usage` row per HTTP request (per book), ceiling unchanged at 200.
- Mark an odds window satisfied **only for games whose fixture got a parsed,
  resolved row from Pinnacle**; a game missing from the board stays pending.
- `--dry-run` reports odds windows and projected requests.

### Phase 4 — verify live (requests = normal job traffic only)
- `python -m hockey_edge.snapshot.job --once` during a real due window; check
  `odds_snapshots` rows, resolution rate (target 100% of due games),
  `api_usage` count, lineup capture still firing.
- Watch one full game night of ticks before calling it done.
- Update CLAUDE.md Status/Gotchas, DATA_PIPELINE.md OddsPapi section (August
  "metadata only" conclusion is superseded), close the "re-opened" provider
  note.

## Explicitly out of scope
NHL odds, Veikkaus scraping, totals/spreads parsing (raw kept, can reparse
later), backfilling historical odds.
