# RESYNC — Phase 4 findings and the re-sync mechanism (2026-08-24)

## 4a. Scope check: does only `game_stats` enrich retroactively?

**No. All three per-game endpoints (`game_detail`, `game_stats`, `shotmap`)
return different content on refetch for the same completed historical game —
and not all in the direction of "more data."** Refetched season=2024,
game_id=1 (Lukko vs. HPK, 2023-09-12) across all three, diffing each fresh
response's content hash against the stored `raw_responses` row (5 liiga.fi
requests total for this investigation, no OddsPapi budget touched):

| endpoint | changed? | nature of the change |
|---|---|---|
| `game_stats` | No (already matched the known 2026-08-22 enrichment) | — |
| `shotmap` | Yes | All 88 shots present both times, matched 1:1 by (period, gameTime, shooterId). The **only** fields that ever differ across all 88: `ownTeamPlayersOnIce`/`otherTeamPlayersOnIce`, +1 in every case. A clean, systematic correction — nothing lost, nothing added, one stat corrected. |
| `game_detail` | Yes, and this is the one that matters | See below. |

**`game_detail`'s goalkeeper-event data got smaller on refetch, not richer.**
Home team's `goalKeeperEvents` went from 4 real entries (a full
period-by-period goalie assignment) down to 1 (just the final pulled-goalie/
empty-net entry); `goalKeeperChanges` went from 3 entries to **0**. Away
team: `goalKeeperEvents` 3→**0**, `goalKeeperChanges` 2→**0**. This directly
contradicts the "liiga.fi retroactively enriches completed games" framing
that CLAUDE.md's CRITICAL FINDING (2026-08-23) and this project's whole
Phase 4 problem statement were built on — that framing came from a single
observation on `game_stats` and turned out not to generalize. `game_detail`
also showed `penaltyEvents` entries reassigned between the home/away team
arrays (looks like a genuine upstream correction of which team an event
belonged to, not corruption — a player's own penalty moved to the correct
side), and several players' `alternateCaptain` flags flipped. The actual
`goalEvents` arrays and final score were verified identical across every
fetch — goals, the target variable, were never in question here.

`parsers.py` ingests `goalEvents`, `penaltyEvents`, `goalKeeperEvents`, and
`homeTeamPlayers`/`awayTeamPlayers` into curated tables
(`game_goalkeeper_events`, `game_penalty_events`, `game_rosters`). A re-sync
that treated "content changed → take the new version as ground truth" would,
for this exact game, have **deleted 7 correct goalkeeper-event rows and
replaced them with 1** — a regression, not a fix.

**Ruling (2026-08-24, from the user): implement a universal no-shrink guard
in the re-sync mechanism itself, not a per-endpoint policy** — since one
game isn't enough to know which endpoints can regress, the guard has to
catch shrinkage wherever it happens, for all three endpoints alike.

## 4b. Endpoint-scoped `--force`

`backfill.py --force` used to force-refetch all five endpoints
(`games_by_season`, `standings`, `game_detail`, `game_stats`, `shotmap`)
unconditionally — fine for a full backfill, wasteful (triples the traffic)
for a targeted recovery. `--endpoints` now scopes which endpoints `--force`
applies to; anything not named still uses normal cache-first fetching:

```
python -m hockey_edge.ingest.liiga.backfill --season 2024 --force --endpoints game_stats
```

Omitting `--endpoints` preserves the exact original behavior (force
everything) — no behavior change for any existing command someone might
already be running.

### The deferred 2015–2024 `game_puck_control` recovery — exact command, and a hard warning

CLAUDE.md's CRITICAL FINDING already identified ~11,000 missing
`game_puck_control` rows across seasons 2015–2024 (average ~0.95 rows/game
vs. ~2.9 in 2025/2026), recoverable via a `game_stats`-scoped `--force`
refetch of ~5,517 games. That work is **not run this session** — it's
~2.3h of traffic and this is explicitly "build the mechanism, don't run the
archaeology." The command, once someone decides to run it, per season:

```
python -m hockey_edge.ingest.liiga.backfill --season <2015..2024> --force --endpoints game_stats
```

`puckStats` (→ `game_puck_control`) lives under `game_stats` only. `shotmap`
is the other endpoint confirmed safe to bulk-refetch historically (4a found
it only ever corrects a stat in place, never loses shot events) —
`--endpoints game_stats,shotmap` is the safe combination if a shotmap
recovery is ever bundled with it.

**Correction (2026-08-25, from the user): the earlier ruling here —
"`game_detail` must never be bulk-refetched against historical seasons" —
predated the no-shrink guard and is superseded by it.** The guard (4c,
below) is exactly what makes a *targeted* refetch of *identified-broken*
`game_detail` games safe: it blocks any reparse that would leave a guarded
table (`game_rosters`, `game_penalty_events`, `game_goalkeeper_events`) with
fewer rows than it already has, so there is no shrink risk left to guard
against for games selected this way. The real constraint was never
"`game_detail` is unsafe," it was "`backfill.py --force`'s blind
delete-and-reinsert is unsafe" — those are different things, and the guard
only protects the latter's replacement path, not `backfill.py` itself.

**Practical consequence: recovering `game_detail` for known-broken games
should go through the guarded path (`resync.py`), not through
`backfill.py --force --endpoints game_detail`.** `backfill.py`'s upsert has
no guard, and while the specific rows known to be broken (empty
`game_penalty_events`/`game_goalkeeper_events`) can only improve or stay
flat — they can't shrink below zero — the same blind upsert *also* rewrites
`game_rosters` for that game in the same call, which currently holds good
data and has no such floor. `resync.py`'s guard protects all three tables
uniformly; `backfill.py`'s does not protect any of them. See
`docs/RECOVERY_BACKLOG.md` for the concrete `game_detail` recovery command
and this exact trade-off spelled out.

## 4c. Time-windowed re-sync — `src/hockey_edge/ingest/liiga/resync.py`

New module, separate from `backfill.py` (season-scoped, one-time-per-season)
and separate from `hockey_edge.snapshot.job` (pre-puck-drop capture against
`snapshots.db`, different database, different failure modes, different
leakage rules — deliberately kept apart in code and in logs, per the
original brief). Runs against `data/hockey.db` only, against games that have
already ended.

```
python -m hockey_edge.ingest.liiga.resync --days 7                          # default: all 3 endpoints
python -m hockey_edge.ingest.liiga.resync --days 7 --dry-run                # zero HTTP requests
python -m hockey_edge.ingest.liiga.resync --days 7 --endpoints game_stats   # scope to one endpoint
```

**Candidate selection**: games where `ended=1` and `start_utc` falls within
the last `--days` days (default 7). **Staleness within that candidate set**
is derived from `sync_state.fetched_at` vs. now — columns that already
exist — rather than a new `last_verified_at` column, so this needed **no
DDL change**. A `(game, endpoint)` is due once its last fetch is older than
`--min-hours-since-fetch` (default 12h); this is what keeps a 15-minute
scheduler cadence from refetching the same completed game dozens of times
across the `--days` window — the outer schedule can be dumb (fire every 15
min, same as the snapshot job) because this check, not the schedule, decides
what's actually stale.

**Per `(game, endpoint)` flow**: force-refetch (bypassing `sync_state`'s
normal "already success, skip" shortcut — that shortcut is exactly the bug
this module exists to route around). The raw file + `sync_state` +
`raw_responses` are written unconditionally on any real fetch, same as
always — "still write the new raw response to disk, append-only, no loss"
is satisfied by `raw_cache.fetch` itself, nothing extra needed. Content hash
decides whether to reparse: **unchanged content → no curated write at all.**
Changed content → parse, then:

**No-shrink guard** (`reparse_with_shrink_guard`): captures each guarded
table's current row count *and* a full row-for-row backup for this
`(game_id, season)` before applying the upsert (which deletes-and-reinserts,
same as `backfill.py`'s existing pattern). After the upsert, compares
per-table counts. If any guarded table's new count is lower than its old
count, every guarded table for this game is restored to its exact pre-upsert
state (original rows, including original ids) and a WARNING is logged with
before/after counts per table — verified end-to-end against a copy of
`hockey.db` (season=2024 game_id=1): `game_detail`'s reparse was correctly
blocked (`game_goalkeeper_events` 7→1 detected, reverted, final state
unchanged at 7), `game_stats` correctly recognized as unchanged (matches the
known 2026-08-22 hash, zero curated writes), `shotmap` correctly applied (88
shots both before and after, content updated in place) — all three raw-layer
writes (`sync_state`/`raw_responses`) landed regardless of what the guard
did to curated tables. **Compared per-table, not per-team** — an event
moving between the home/away arrays (the `penaltyEvents` finding in 4a) is a
legitimate correction and does not trip the guard.

Guarded tables per endpoint (all `(game_id, season)`-scoped
delete-and-reinsert tables — see `resync.py`'s `GUARDED_TABLES`):
`game_detail` → `game_rosters`, `game_penalty_events`,
`game_goalkeeper_events`. `game_stats` → `game_team_period_stats`,
`game_player_period_stats`, `game_goalie_period_stats`,
`game_puck_control`. `shotmap` → `shot_events`. **`players` is deliberately
excluded** — it's a global upsert-by-`player_id` table, not a per-game
delete-and-reinsert, so it can't "shrink" in the sense this guard checks;
letting a player's bio fields update from a corrected fetch is fine
regardless of what happens to this game's event tables.

**Not yet true of `backfill.py`'s own `--force` path** — see the 4b warning
above. The guard only protects `resync.py`'s targeted per-game re-sync.

## Correction to CLAUDE.md's framing

CLAUDE.md's CRITICAL FINDING (2026-08-23) says liiga.fi "retroactively
enriches completed games' responses" — true for `game_stats` and `shotmap`,
**false as a general claim**: `game_detail` can retroactively *lose* data.
Treat "liiga.fi's data for a completed game can still change after original
fetch, in either direction" as the accurate framing going forward.
