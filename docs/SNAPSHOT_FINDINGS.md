# SNAPSHOT_FINDINGS — Phase 1 recon (2026-08-23)

> **SUPERSEDED IN PART, 2026-09-17.** This document's central OddsPapi
> conclusion — that `/odds-by-tournaments` carries no prices and that real
> pricing needs a per-fixture endpoint — was **wrong, for a defensible
> reason**: it was tested on 2026-08-23, when the board was too early in the
> off-season to have prices. Re-tested in-season, the same endpoint returned
> full `bookmakerOdds.<book>.markets` for every posted fixture, and parsing
> is now implemented against it. The Shape A/B budget analysis below is
> therefore moot (Shape A — the "docs are right" case — is what's real). The
> rest of this document (liiga.fi lineups, fixtures, participants) stands.
> Current odds facts: `docs/ODDS_PLAN.md` and `docs/DATA_PIPELINE.md`.

Recon only. No parsing/capture code written. Read `data/hockey.db`; wrote nothing
to it. Live liiga.fi calls: read-only, 1.5s delay, normal UA, no writes to
`sync_state`/`raw_responses` (so as not to touch `data/hockey.db` outside its
owning ingest path). OddsPapi calls: **3 of 8** budget used (see 1c — two of
those three were avoidable, noted below).

## 1a. What fixtures actually exist right now

**Contradicts `docs/PROJECT_CONTEXT.md`: `data/hockey.db` does NOT hold season
2027.** `SELECT COUNT(*) FROM games WHERE season=2027` → **0**. No `sync_state`
rows mention season 2027 or a `2027`-prefixed entity_id either — season 2027 has
never been fetched into this DB, backfill or otherwise. `PROJECT_CONTEXT.md`
states "`season=2027` already returns all 544 fixtures ... confirmed
2026-08-22" and the task prompt repeated this as already-true — both are wrong
about the **database**. They are right about the **live API**: `games_by_season`
(`tournament=runkosarja&season=2027`) returns exactly **544** games live, all
`serie="RUNKOSARJA"`, all `started=false`, matching the 17×64÷2 sanity check.
The 544 fixtures exist on liiga.fi; they simply haven't been ingested. This
doesn't block Phase 2 — the job is speced to read `games_by_date`/`games_by_season`
live, not `data/hockey.db` — but it means nobody should assume `hockey.db` is
current for season 2027 until a backfill run is explicitly done (out of this
session's scope: this session owns `snapshot/`, not `ingest/`, outside Phase 4).

**Regular-season opening date, confirmed from live data, not from anything in
the prompt or docs:** earliest `start` among the 544 `season=2027 RUNKOSARJA`
games is **2026-09-01T15:30:00Z** (18:30 Finnish local). `games_by_date` for
2026-08-23 with `tournament=runkosarja` independently confirms this —
`nextGameDate: "2026-09-01"`. Two independent live sources agree; 9 days out
from today as stated.

**Preseason (`valmistavat_ottelut`) for 2026-27 IS exposed via `games_by_season`**,
contrary to the open question in `endpoints.py`/`CLAUDE.md` (which only ever
tested `games_by_date` for live/current games): `tournament=valmistavat_ottelut&season=2027`
returns **52 games**, dated 2026-08-07 through 2026-08-27, all `season=2027` in
the payload. **New `serie` value not in any existing catalog or doc:
`"PITSITURNAUS"`** (alongside `"PRACTICE"`) — a named preseason tournament, not
just generic exhibition games. Neither `endpoints.py` nor `CLAUDE.md`'s Gotchas
section mentions this value; anything that filters/asserts on `serie` values for
preseason should treat this as a third preseason-family value, not `PRACTICE`'s
synonym, until confirmed otherwise (e.g. it may map to a specific in-season
tournament like a pre-season cup).

**`games_by_date` gotcha confirmed exactly as documented** (season param
silently ignored): using it with `tournament=runkosarja` for a date in the past
season still resolves against the live schedule (`previousGameDate`/`nextGameDate`
point at 2026-09 / 2027-03, i.e. the *current* 2026-27 season), regardless of
season intent. Also observed: `tournament=runkosarja` 502'd twice (dates
2026-08-24 and 2026-08-27, "Internal server error") while `valmistavat_ottelut`
succeeded for the same dates — intermittent, not systematic; worth a retry/backoff
in the real job rather than treating a single 502 as fatal.

**`tournament=playoffs` superset gotcha**: not re-tested this session (no
playoff games exist yet for 2026-27), but per `CLAUDE.md`/`PROJECT_CONTEXT.md`
this was already confirmed in season 2025 and nothing here contradicts it —
noting only that fixture discovery code must still not assume `tournament` and
`serie` are 1:1.

**Jokerit — contradicts `docs/PROJECT_CONTEXT.md`'s "zero rows anywhere in the
2015–2026 backfill".** `data/hockey.db` actually has **18 Jokerit rows across 5
seasons** (2021, 2022, 2024, 2025, 2026) — all `PRACTICE` friendlies **except**
season 2025, which has a genuine 5-game `QUALIFICATIONS` series, Jokerit vs.
Pelicans, in April 2025. **Correction (2026-08-23, per user): Pelicans won that
series 4–1 — Jokerit lost it and did not go up this way.** It is not a
promotion-series result, and the inference that Jokerit has a real recent
competitive result to seed an Elo prior from does not hold. The Elo cold-start
problem for Jokerit stands close to the original assumption: five competitive
games from 16 months ago, all losses, plus scattered preseason friendlies, is
barely distinguishable from no data at all. What's still correct and worth
keeping: the 18 rows exist, and `PROJECT_CONTEXT.md`'s "zero rows anywhere in
the 2015-2026 backfill" is factually wrong regardless of what those rows mean
for Elo.

Encoding note (not a bug): non-ASCII team names (e.g. "Kärpät") looked like
mojibake in this session's terminal output. Verified directly — the JSON bytes
decode correctly as UTF-8; it was a console codepage display artifact, not
corrupted API data or a decoding bug in any fetch code. No action needed, but
worth knowing so nobody "fixes" real data based on how it prints in a shell.

## 1b. Does `game_detail` expose lineups before puck drop?

**Answer: it exposes a roster, not a confirmed lineup — and this is true even
after the game has ended, not just before puck drop.**

Tested two not-yet-started games (`started=false`, `ended=false`):
- Preseason: season=2027, game_id=2701831 (TPS vs Jokerit, PRACTICE,
  2026-08-25) → `fixtures/liiga/game_detail/2027_preseason_pregame_not-started.json`
- Regular-season opener: season=2027, game_id=2701274 (Jukurit vs HPK,
  RUNKOSARJA, 2026-09-01) → `fixtures/liiga/game_detail/2027_runkosarja_opener_pregame_not-started.json`

Both return **`homeTeamPlayers`/`awayTeamPlayers` fully populated** — 37/33 and
37/27 players respectively. That's the extended squad, not a ~20-23 player game
roster: **4 goalies** listed per team (`roleCode='MV'`), and every player's
`line` field is `null` across the board. There is no field in this payload that
says "these N players are dressed tonight" or "this is the starter," pre-game.

**Comparison against a completed historical game** (`fixtures/liiga/game_detail/2024.json`,
season=2024 game_id=1, `started=true ended=true`): roster narrows to 21–26
players — closer to a real game roster — but **still lists 2–3 goalies per
team, and `line` is still `null` for every player, even after the game ended.**
So `game_detail` never resolves to a single named starter, pre-game or post-game;
identifying the actual starting goalie for historical/training data has to come
from somewhere else (e.g. `game_goalkeeper_events` / ice-time in `game_stats`),
which is inherently post-hoc and can't be the pre-game signal the live job needs.

Also noted: the `roleCode` vocabulary differs between the two payloads
(`P/H/MV/KP` pre-game vs `KH/VP/OL/VL/MV/OP/H` in the older completed-game
fixture) — could be a season-to-season vocabulary change or could be
pre-game-vs-post-game shape difference; not resolved this session, flagging so
nobody hardcodes one vocabulary as universal.

**`game_preview` gotcha not documented in `endpoints.py`:** the endpoint 500s
unless `gameDate` is passed as a **full ISO datetime** (`2026-09-01T15:30:00Z`);
a bare date (`2026-09-01`) 500s even with all three required params present.
`endpoints.py`'s notes only say the three params are required, not this format
constraint. Once fixed, `game_preview` for the regular-season opener
(`fixtures/liiga/game_preview/2027_runkosarja_opener_pregame.json`) returned
200 but with **`goaliesToWatch` empty for both teams** — no goalie signal 9 days
out. `playersToWatch` is populated but is season-stats/rankings, not a lineup
signal. `homePreviousGames`/`awayPreviousGames` (mentioned in `endpoints.py`'s
notes from the 2024 discovery session) were **absent** from this response
entirely — either a shape change or those only appear closer to game day;
unresolved.

**What's still unverified**: whether either endpoint fills in closer to puck
drop. Both test games are 3–9 days out from today (2026-08-23) — too far to
re-poll within this session. **Recommend re-fetching both saved fixtures' game
IDs again within a day or two of their actual puck drop** (2701831 on
2026-08-25, 2701274 on 2026-09-01) to see whether the roster narrows further or
a starter signal ever appears, before concluding the veikkaus.fi fallback is
required. Given what's been seen so far (full squad, no line data, at T-9d and
T-2d respectively), it's plausible the squad never narrows to a single
confirmed starter via this endpoint at all — but that's not proven yet.

## 1c. OddsPapi — one call, budget

**Used 3 of 8 requests, not the planned 1** — two were my own inefficiency, not
required by the task: request 2 (below) and an immediately-repeated identical
call to re-read its error body that I should have captured from the first
response instead of re-requesting. Logged transparently rather than silently
absorbed. **5 requests remain in this session's budget.**

1. `GET /v4/odds-by-tournaments?tournamentIds=134&bookmaker=pinnacle` → **HTTP
   200**, not `FIXTURE_NOT_FOUND`. Saved verbatim:
   `fixtures/oddspapi/odds-by-tournaments_134_pinnacle.json`.
2. `GET /v4/odds-by-tournaments?tournamentIds=134` (no `bookmaker`) → **HTTP
   400**, `INVALID_PARAMETER`: `"Please provide exactly one bookmaker using the
   'bookmaker' query parameter."` — confirms `bookmaker` is **required**, not
   optional, contradicting the impression in `docs/DATA_PIPELINE.md` that it's
   a filter. `OddsPapiProvider.fetch_odds` already always passes it
   (`book: str = "pinnacle"` default), so current code is already correct here —
   just flagging that this endpoint cannot be called without it.
3. Repeat of #2 to read the body — wasted, see above.

**The board has exactly 1 fixture right now**, not the whole 544-game season:

```json
{
  "fixtureId": "id1500013472116186",
  "participant1Id": 3836,
  "participant2Id": 3839,
  "sportId": 15,
  "tournamentId": 134,
  "hasOdds": true,
  "startTime": "2026-09-05T14:00:00.000Z"
}
```

**Important, unplanned finding: this payload has no market/outcome/price data
at all** — no `bookmakerOdds`, no `markets`, no `outcomes` field anywhere,
despite `hasOdds: true`. `OddsPapiProvider`'s docstring and the code's
assumption (`bookmakerOdds.markets.outcomes`) describe a shape that **does not
exist in this response**. `/odds-by-tournaments` is a fixture-board listing
endpoint — it tells you *which* fixtures currently have odds posted, not what
those odds are. Getting actual home/draw/away prices almost certainly requires
a second, different, not-yet-discovered endpoint (something like a per-fixture
odds call). **This session did not spend budget guessing at that endpoint's
name** — recommend either checking OddsPapi's own API docs (if accessible) or
budgeting 1-2 targeted calls for it explicitly in Phase 2/3 once confirmed
worth pursuing, rather than trial-and-error against the live API.

**The join-key problem is worse than "name spelling differences" — there are no
names at all.** `participant1Id`/`participant2Id` (3836/3839) are bare OddsPapi
internal IDs with zero human-readable label in this response. Resolving them to
actual teams needs a separate OddsPapi lookup (participants/teams endpoint,
undiscovered). **Worse: `startTime` alone can't disambiguate on a shared game
night.** Cross-referencing this fixture's `startTime` (2026-09-05T14:00:00Z)
against the already-fetched `games_by_season` season=2027 payload (no extra
HTTP cost — reused the saved fixture) found **five liiga.fi games at that exact
kickoff time**: Jukurit–Sport, K-Espoo–Pelicans, Kärpät–JYP, Lukko–Jokerit,
TPS–HIFK (all 2026-09-05T14:00:00Z, game_ids 2701286–2701290). Date+time alone
resolves to 1-of-5, not a unique game. **The join key has to be
(participant1Id, participant2Id) → team names, resolved via an OddsPapi
teams/participants endpoint, then matched to liiga.fi team slugs/names** —
startTime is a necessary tie-breaker for ambiguous cases (e.g. postponements)
but not sufficient on its own, exactly the multi-game-night scenario Phase 2's
spec already anticipated for the *odds-poll* side; it turns out to matter for
the *join*, too.

**Whether a 200 can come back empty/partial (constraint 6):** not directly
observed — this board had exactly 1 fixture with `hasOdds: true` and no empty
placeholder entries. Not yet known whether `/odds-by-tournaments` can return
`hasOdds: false` rows or an empty list at 200 (as opposed to 404
`FIXTURE_NOT_FOUND`) — worth watching for once the board fills in closer to
the season opener.

## Summary for go/no-go

- **Fixture discovery**: live `games_by_date`/`games_by_season` works now,
  confirms Sept 1 opener and Sept 5 as the earliest date OddsPapi has odds
  posted for. `data/hockey.db` is NOT populated for season 2027 — irrelevant
  to Phase 2's design (which reads live, not the DB) but a real gap versus what
  the docs claimed; someone should backfill it separately, outside this
  session's scope.
- **Lineups**: `game_detail` gives a roster (with all goalies, no line/starter
  data), never a confirmed single lineup, at T-9d/T-2d and even post-game
  historically. `game_preview`'s `goaliesToWatch` is empty this far out.
  Genuinely unresolved whether either ever narrows closer to puck drop — needs
  a re-check within ~1-2 days of an actual game, which this session couldn't
  do. Do not build lineup capture logic on an assumption either way yet.
- **Odds**: board is live (not off-season 404), but `/odds-by-tournaments`
  alone doesn't carry prices — a second endpoint is needed and undiscovered.
  Join key is OddsPapi participant IDs → names (own lookup needed) + startTime
  as a disambiguator only, not a primary key.
- **OddsPapi budget used**: 3 of 8 this session (5 remain).

---

# Phase 2 addendum (2026-08-23)

## OddsPapi documentation recon — zero live API requests

Read `https://oddspapi.io/en/docs/` directly (methods index, `get-odds`,
`get-odds-by-tournament`, `requests-and-quota`, `get-participants`). Publicly
accessible, no auth needed to read. **Zero requests against the live OddsPapi
API** — this section adds nothing to the 3/8 session total.

**There is a bulk odds endpoint — but the docs and Phase 1's live sample
disagree about what it returns, and that disagreement is the whole ballgame.**

- `GET /v4/odds` — **single-fixture only.** Requires one `fixtureId`; no
  comma-separated multi-id form documented anywhere. This is definitely
  per-fixture pricing if it's needed at all.
- `GET /v4/odds-by-tournaments` — takes multiple `tournamentIds` (not fixture
  ids) and, **per the docs**, each returned fixture object should include a
  full `bookmakerOdds` block: `markets` → `outcomes` → `players` with live
  `price` data in three formats, bet limits, timestamps. That is a
  **materially richer shape than what Phase 1 actually got back live**
  (`fixtureId`/`participant1Id`/`participant2Id`/`sportId`/`tournamentId`/
  `seasonId`/`statusId`/`hasOdds`/`startTime`/`updatedAt` — no `bookmakerOdds`
  key at all, despite `hasOdds: true`). **This contradiction is unresolved**
  — it was not chased further per the user's "zero live requests" instruction
  for this spike. Two explanations seem plausible, not distinguished yet:
  (a) `bookmakerOdds` really is there once real markets open for a fixture,
  and the one live sample (a Sept 5 preseason friendly, over a week out at
  capture time) just hadn't had a market populate under it yet despite
  `hasOdds: true`; or (b) the docs describe a richer/older shape than what
  the endpoint actually returns for `bookmaker=<single>`-filtered calls. Only
  a fresh live call closer to a game (spending real budget) resolves this.
- `GET /v4/participants?sportId=<id>` — **exactly the lookup Phase 1 flagged
  as missing.** No fixture/tournament scoping — one call returns the *entire*
  sport's participant-id → name map (`{"3409": "Chicago Bulls", ...}`) for
  `sportId=15` (ice hockey). This is a one-time (or rarely-refreshed) cost,
  not a per-poll one — resolving Phase 1's bare `participant1Id: 3836` /
  `participant2Id: 3839` to team names costs **1 request total**, cacheable
  for the whole season. The join-key problem is smaller than Phase 1 feared:
  not "no names ever," just "one extra one-time call, plus the still-real
  startTime-can't-disambiguate-a-shared-game-night problem from Phase 1c."
- **Billing, confirmed from docs (not just the dashboard-counter check from
  2026-07-12)**: "1 Request = 1 call to a billable API endpoint, regardless
  of the response" — `/v4/odds` and `/v4/odds-by-tournaments` cost the same 1
  request each; cost does **not** scale with fixture/bookmaker count in the
  response for either endpoint. `/v4/historical-odds` is called out as always
  free (doesn't count against quota) — not needed for a live snapshot job but
  worth knowing it exists uncounted. Monthly quota figure itself wasn't
  restated in the docs excerpts pulled; already independently confirmed as
  250/month via the account dashboard (2026-07-12, `docs/DATA_PIPELINE.md`).

## Request-cost-per-game-night, both shapes

Assuming 2–7 games on a typical Liiga night, 3 capture windows/night
(opening/mid/closing), and the 1-request-flat billing confirmed above:

**Shape A — if `/odds-by-tournaments` really does carry full market data (per
docs) once a fixture's market is actually open:** 1 request/poll covers the
*entire* tournament board regardless of game count. **3 requests/night**, flat,
independent of whether it's a 2-game or 7-game night. At ~15–20 game
nights/month: **45–60 requests/month.** Comfortably inside 250, exactly the
original design assumption in `docs/DATA_PIPELINE.md`.

**Shape B — if `/odds-by-tournaments` is metadata-only (matching what Phase 1
actually observed) and real prices require one `/v4/odds` call per fixture:**
per window, 1 discovery call + G per-fixture calls, where G = games that
night. **3 × (1 + G) requests/night:**
- G=2 (light night): 9 requests/night
- G=7 (busy night): 24 requests/night
- Average G≈4 across a typical 15–20-night month: ~15 requests/night ×
  15–20 nights ≈ **225–300 requests/month** — blows through the 250/month
  tier, matching the user's own back-of-envelope estimate exactly. Busy
  nights alone (G=7, 24 req/night) eat the whole month's headroom in ~8 such
  nights.

Plus, either shape: **+1 one-time request** for `/v4/participants` to resolve
team names (not per-night, doesn't change the maths above meaningfully).

**Settled 2026-08-23, request 4/8**: repeated
`GET /v4/odds-by-tournaments?tournamentIds=134&bookmaker=pinnacle` (same call
as Phase 1c). Saved verbatim:
`fixtures/oddspapi/odds-by-tournaments_134_pinnacle_request4_2026-08-23.json`.
Same single fixture (`id1500013472116186`, the 2026-09-05 game), same
metadata-only shape — `hasOdds: true` but **no `bookmakerOdds` key present at
all**. **Shape B confirmed: `/odds-by-tournaments` is metadata-only.** Per
the user's instruction, not hunting further for a prices endpoint this
session — stopping here.

**This means the plan as originally scoped (one call/poll covers the whole
board including prices) does not hold.** Real per-fixture pricing needs
`/v4/odds` (one `fixtureId` per call, confirmed single-fixture-only from the
docs read above), and Shape B's economics apply: **~225–300 requests/month at
3 polls/night**, over the 250/month tier. This was exactly why
`NullOddsProvider` was wired instead of guessing — the capture mechanism
(fixture discovery, windows, satisfaction tracking, budget ceiling) is
already correct and complete regardless of which provider/cadence gets
chosen; only the *cadence* needs to change once a decision is made, e.g.
fewer polls/night, per-fixture calls only for games actually near their
closing window rather than every due window, or reconsidering Veikkaus as
primary rather than fallback. That decision is explicitly the user's to make,
not this session's.

One more data point worth carrying into that decision: this was still the
same single, month-out fixture (`updatedAt: 2026-07-26`, unchanged since
Phase 1c) — the board genuinely hasn't grown yet, so the "1 discovery +
G per-fixture" Shape B maths above is still theoretical, not yet observed
against a real multi-game night.

## Correction applied

Per the user's correction: the 2025 Jokerit–Pelicans `QUALIFICATIONS` series
was **won by Pelicans 4–1** — Jokerit lost it. The "Jokerit — contradicts
`PROJECT_CONTEXT.md`" paragraph above has been corrected in place (the 18-rows/
zero-rows-is-wrong finding stands; the promotion-series inference does not).

---

# 1b resolved (2026-09-01) — lineups confirmed at T-30; starter signal still unverified

**`game_detail` DOES publish a confirmed lineup pre-puck-drop — it just doesn't
show up until close to game time.** `scripts/lineup_probe.py --date
2026-09-01 --label T-30`, run ~30 minutes before the 2026-09-01 opening
slate's 18:30 local puck drop, probed all 7 games live. Result: **`line` is
non-null for exactly 22 players per team-side (20 skaters + 2 goalies), on
all 14 team-sides, zero exceptions** — versus `line=null` for every player on
the same games at T-9 days (§1b above). This settles the "genuinely
unresolved whether either [endpoint] ever narrows closer to puck drop"
question left open in the original 1b write-up: yes, `game_detail` narrows,
but only close in.

Same-game roleCode-vocabulary comparison, now resolvable: `game_id=2701274`
at T-9d had roleCodes `{H, P, MV}` only (generic); the identical game_id at
T-30 had the full `{KH, VL, OL, H, VP, OP, MV, P, 7. P, 13. H, 8. P}` set —
matching the "older completed-game" vocabulary from the original 1b note,
not the sparse pre-game one. So the vocabulary difference flagged as
"unresolved — season change or pre/post-game shape" in the original 1b is
**neither** — it's proximity to game time, confirmed on the same game_id at
two points in its own lifecycle.

**No literal "starter" field exists — every field on every T-30 goalie
object was dumped and checked** (id, jersey, handedness, height/weight,
captain/rookie/alternateCaptain, injured/suspended/removed, pictureUrl,
awards, sponsors, extra_*). What is real: `line`, the same depth-chart field
used for forward lines/D-pairs, is populated for goalies too, and splits
**exactly one goalie at `line=1` and one at `line=2` per team-side, on all 14
team-sides, no exceptions**. `hockey_edge.snapshot.lineups` uses
`line==1` as the primary starter signal (`starter_source=
'goalie_line_value'`) on that basis.

**This conflicts with a competing candidate raised the same day**: a manual
Flashscore cross-check against array order (the raw position of a goalie in
`homeTeamPlayers`/`awayTeamPlayers`) reportedly matched the actual starter in
7/7 games checked. A structural check across the same 2026-09-01 T-30
payloads found **array-order-first agrees with `line==1` in only 7 of 14
team-sides — a 50/50 split, statistically indistinguishable from a randomly
ordered array**. The two signals cannot both be right in the 7 team-sides
where they disagree, and nothing in this session's data can arbitrate that
independently.

**`scripts/verify_starters.py` exists specifically to settle this — not by
further guessing, but against real results.** It compares
`lineup_snapshots.starter_player_id` (captured pre-game) against the actual
starter derived from `data/hockey.db` once a game has `ended=1`. Building it
surfaced its own gotcha: **`game_goalkeeper_events` — the table originally
named for this check — never records who started** (zero rows anywhere with
`begin_time=0`, checked across all of season 2026; it only logs mid-game
subs/empty-net pulls), and **36% of season-2026 games have zero rows in it
at all** (a goalie who plays the whole game with no empty-net pull leaves no
trace there). The script instead derives ground truth from
`game_goalie_period_stats`: the goalie with period-1 `shots_on_goal > 0` is
who started, cross-checked against `game_goalkeeper_events` only to flag (not
silently resolve) games with a substitution inside the first 5 minutes.
Verified against a real completed game (season=2026, game_id=2, HIFK @
Jukurit) with injected correct/incorrect test rows — both scored correctly
(1 agree, 1 disagree) — before being pointed at real captured data.

## Provisional result (2026-09-01, same evening): `line==1` went 14/14

Once season 2027 was ingested into `hockey.db` (see the nightly-sync section
of `docs/RESYNC.md`), the 2026-09-01 slate's real results became available to
check against the T-30 probe fixtures. Scored **read-only and in memory** —
nothing was written to `lineup_snapshots`, since the probe data's provenance
is the user's call, not this session's:

| candidate signal | agreement with actual starter |
|---|---|
| `line == 1` | **14 / 14 (100%)** |
| first goalie in array order | 7 / 14 (50%) |

Zero undetermined — ground truth resolved cleanly for all 14 team-sides.
**The structural prediction held exactly**: `line==1` identified every
starter, and array order performed at precisely chance, as the 7/14
structural split implied it would. The earlier manual Flashscore check that
appeared to validate array order 7/7 must have been drawn from the subset
where the two signals happen to coincide (they agree on 7 of 14 team-sides
here — e.g. HPK, Ilves, SaiPa, Tappara, KooKoo, Sport, Jokerit — and diverge
on the other 7).

Caveats, so this isn't over-read: **n = 14 team-sides from a single night**,
all season-openers, scored against a ground-truth derivation
(period-1 `shots_on_goal`) that is itself an inference from stats rather than
a declared field. It is strong evidence for the choice already made in
`lineups.py`, not proof for the season. `starter_confidence` stays
`'inferred_structural'` — the flag describes the *basis* (a structural
inference, no literal field), which this result does not change.

**Run `verify_starters.py` periodically once the 2026-27 season has more
completed games AND real job-captured snapshots exist.** As of 2026-09-01 the ground-truth half of that is now in
place — season 2027 is ingested (595 games, 58 ended) — but the *captured*
half is not: `lineup_snapshots` has **zero rows**, because the 2026-09-01
T-30 capture was a manual `scripts/lineup_probe.py` run, which by design
writes only to `fixtures/liiga/lineup_probe/` and never to a database, and
`job.py` has not run a pass with a due window for those games. So
`verify_starters.py` reports "nothing to verify yet" — correct behavior, not
a bug. It starts producing real numbers once `job.py` captures a slate
itself. Until a monitored agreement rate accumulates that way, treat every
`starter_source='goalie_line_value'` value written by the snapshot job as an
**inference**, not a fact — `starter_confidence='inferred_structural'` is
there on every row specifically to make that visible downstream.

`hockey_edge.snapshot.lineups` and `job.py`'s `capture_lineups` are wired
(previously stubbed) on the basis of the above; `lineup_snapshots`' schema
was replaced (the old stub had never had a row written to it, so no
migration was needed — see `storage.py`) with one row per
(league, season, game_id, team_role) per capture, keeping the full raw
`game_detail`(+`game_preview`) payload regardless of parse outcome.
