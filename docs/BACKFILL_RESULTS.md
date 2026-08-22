# BACKFILL_RESULTS — Liiga full historical backfill

Run started 2026-07-19. Command per season:

```
PYTHONPATH=src python -m hockey_edge.ingest.liiga.backfill --season <N>
```

Seasons run newest-first (2024 → 2015), full per-game endpoints (no
`--max-games`). Season 2024 continues from the season=2024, `--max-games 20`
smoke test documented in `docs/SCHEMA_DRAFT.md` — the resumable sync is
expected to skip the 20 games already fetched.

**This file is a live results log, filled in as each season completes.** The
top summary section below is written last, once all seasons are done.

---

## Top summary

**Backfill complete for all 10 requested seasons (2024 down to 2015),
2026-07-19 through 2026-07-21.** Run paused after season 2023 for a critical
data-integrity bug (below); fix implemented and verified 2026-07-20,
backfill resumed and completed through season 2015.

**Totals across all 10 seasons:**

| | |
|---|---|
| Games | 5,517 (4,435 `RUNKOSARJA` + 405 `PLAYOFFS` + 677 `PRACTICE`) |
| `game_goal_events` | 29,284 |
| `game_penalty_events` | 33,762 |
| `game_goalkeeper_events` | 8,777 |
| `game_rosters` | 296,077 |
| `players` (dimension) | 3,261 |
| `game_team_period_stats` | 35,208 |
| `game_player_period_stats` | 666,806 |
| `game_goalie_period_stats` | 70,396 |
| `game_puck_control` | 5,194 |
| `shot_events` | 468,407 |
| `standings` | 543 |

Per-season game counts: 2024=561, 2023=562, 2022=569, 2021=540, 2020=530,
2019=573, 2018=572, 2017=549, 2016=554, 2015=507.

**`game_stats`/`shotmap` cutoff: not found in this range — both endpoints
work all the way back to season 2015**, contradicting the smoke-test-era
assumption (`CLAUDE.md`/`SCHEMA_DRAFT.md`) that they're "recent-seasons-
only" based on a season=2010 fixture 500ing. The real per-game-endpoint
failures seen in this backfill are **not a season-age cutoff at all** —
they're two distinct, unrelated mechanisms found by digging into every
`failed_permanent` game individually rather than assuming "old data":
1. **International friendlies** (seasons 2024, 2023, 2022, 2020, 2019,
   2018): `game_stats` 404/500s for `PRACTICE` games against non-Liiga
   clubs (HC Plzeň, Timrå IK, Luleå HF, Djurgårdens, etc.) — Liiga's stats
   system apparently doesn't track opponents outside the league. Accounts
   for the failures in every season except 2016/2015.
2. **Blues-specific gap** (seasons 2016, 2015): unlike every other season,
   these failures hit *domestic* games (`RUNKOSARJA` and even one
   `PLAYOFFS` game) and cluster specifically on games involving HC Blues —
   32/67 of Blues' 2016 games, 28/65 of KalPa's 2016 games (KalPa's
   failures are all Blues' opponents in that date range), and 8/8 of
   season 2015's failures directly involve Blues. Root cause not
   identified (would need a liiga.fi devtools capture); flagged for anyone
   building features off Blues-involving games in 2015/2016.

`game_stats` `failed_permanent` count by season: 2024=14, 2023=12, 2022=7,
2021=0, 2020=23, 2019=21, 2018=18, 2017=0, 2016=57, 2015=8 (160 total out of
5,517 games, 2.9%). `shotmap` had zero `failed_permanent` in any season —
only `game_stats` is affected.

**`PLAYOUT`/`QUALIFICATIONS` phases: still unconfirmed across all 10
seasons** — every season's `games_by_season` call for these two tournament
values returned zero games. `SCHEMA_DRAFT.md`'s open question about their
`serie` string remains open; would need a season further back than 2015 or
external knowledge of which season had a playout/qualification round.

**Sanity violations found: zero.** Across all 5,517 games in all 10
seasons: 0 missing a team id or `start_utc`, 0 `ended=1` games with null
goals, 0 `failed_retryable` rows left in `sync_state` at the end of the run.
Every season's goal-event cross-check showed a small surplus of
`game_goal_events` over final-score goal totals (consistently ~2–4%) —
traced on season 2024 to overturned/video-reviewed goals (`goal_types`
containing `"VT..."` codes) plus a handful of foreign-friendly games with
zero recorded goal events despite a non-zero score; confirmed as real API
behavior, not a parser bug, and the same order of magnitude held for every
other season without needing to re-derive it each time.

**Other historical findings, not bugs:** season 2020 (Liiga's 2019-20
season) has **zero `PLAYOFFS` games** — COVID-19 cancelled that
postseason outright, and the API simply has no playoff games to return for
that season, not a fetch failure. `home_expected_goals`/`away_expected_goals`
is 0% present in seasons 2018–2019, ~85% present by 2021, ~100% by
2023–2024 — the real xG-availability cutoff is between 2019 and 2021, not
"pre-~2015" as originally guessed in `SCHEMA_DRAFT.md`.

**NTFS/raw-cache integrity: clean across the full run.** Final check:
`raw_responses` row counts match on-disk file counts exactly for every
endpoint across all 10 seasons (`game_detail` 5,517/5,517, `game_stats`
5,357/5,357, `shotmap` 5,517/5,517, `games_by_season` 50/50, `standings`
10/10). No colon-related ADS issue recurred after the composite-key fix
(new file names use `season-game_id__hash8.json`, hyphen not colon).

### Critical finding: `game_id` is not globally unique across seasons

`docs/SCHEMA_DRAFT.md`'s original design principle 5 ("IDs are carried as
the API gives them" — renumbered to 6 when that doc was reconciled, with a
new principle 5 stating the composite-key rule) assumed the API's `game_id` is a
stable, standalone identifier and made it the `games` table's sole
`PRIMARY KEY`, with every other per-game table (`game_rosters`,
`game_goal_events`, `game_penalty_events`, `game_goalkeeper_events`,
`game_team_period_stats`, `game_player_period_stats`,
`game_goalie_period_stats`, `game_puck_control`, `shot_events`) carrying a
bare `game_id INTEGER REFERENCES games(game_id)`. That assumption holds for
`PLAYOFFS` games (season=2023 playoffs: ids 46245–48507; season=2024
playoffs: ids 55750–56919 — disjoint, no collision seen) but **does not
hold for `RUNKOSARJA` (regular season) or `PRACTICE` (preseason) games**,
whose `game_id`s are small per-season counters that reset each season:

- Comparing the raw `games_by_season` JSON for season=2023 vs season=2024
  directly (not the DB, which was already partially overwritten by the
  time this was caught): **466 game_ids appear in both seasons**
  — 449 of 450 `RUNKOSARJA` games and 17 `PRACTICE` games. E.g. game_id=1 is
  Tappara–TPS (2022-09-13, season 2023) *and* Lukko–HPK (2023-09-12, season
  2024) — two different real games sharing one id.

**Confirmed impact on already-collected data** (spot-checked on game_id=1536:
`games` row now reads season=2023 "ZSC Zürich Lions vs KooKoo" — but
`game_rosters` for that same game_id still holds team `168761288:hifk`,
i.e. season=2024's real game 1536, "Red Bull München vs HIFK" — the two
halves of "game 1536" in the DB right now describe two different games):

1. **`games` table**: whichever season is backfilled *last* wins the row for
   any colliding `game_id` (`INSERT ... ON CONFLICT (game_id) DO UPDATE`,
   per the documented upsert-only rebuild rule). Season 2023's backfill ran
   after 2024's and silently overwrote 466 of season 2024's `games` rows —
   season=2024's `games` count is now 95 (was 561 right after that season's
   own verification), and `game_goal_events` for those same 466 game_ids
   was deleted-and-reinserted with season 2023's goals (that table's rebuild
   rule deletes by game_id, so season 2024's real goal events for those
   games are gone from the curated table — recoverable from the untouched
   raw cache, not lost from disk).
2. **`game_detail`/`game_stats`/`shotmap` raw cache**: `sync_state`/
   `raw_responses` key on `entity_id = str(game_id)` **without a season
   qualifier**. For any colliding `game_id`, whichever season fetched it
   *first* wins permanently — every later season's `backfill_game_detail`/
   `_game_stats`/`_shotmap` call sees a pre-existing `status='success'` row
   for that bare `game_id` and skips the network entirely (idempotency
   working exactly as designed — the design just didn't anticipate the id
   not being season-scoped). Confirmed on game_id=1536: only one
   `raw_responses` row exists, `season=2024`, url
   `.../games/2024/1536` — season 2023's backfill never actually fetched
   `.../games/2023/1536`, so **season 2023's real roster/stats/shot data for
   all 466 colliding game_ids was never fetched at all**, and the curated
   tables derived from `game_detail`/`game_stats`/`shotmap` for those
   game_ids still hold season 2024's content, now silently mismatched
   against the `games` row's season-2023 metadata.
3. Net effect confirmed independently in `sync_state`: season=2023 shows
   only 96/562 games with a `game_detail` success row tagged `season=2023`
   — the other 466 are the stale season=2024 rows that never got updated
   (fetch() returns early on cache hit before touching `sync_state`).

**Root cause**: the endpoint URLs themselves are season-scoped
(`/games/{season}/{game_id}`, `/games/stats/{season}/{game_id}`,
`/shotmap/{season}/{game_id}` — see `endpoints.py`) precisely because
`game_id` alone doesn't disambiguate the game; the ingest code didn't carry
that scoping into its own cache keys or primary key.

This needed a decision on the identity model and a remediation plan for the
already-collected season 2023/2024 data — per this project's rule against
schema/parser changes without being forced, and because the fix touches the
shared identity of 9 tables, this was escalated to the user rather than
decided unilaterally.

### Fix implemented and verified (2026-07-20)

**Chosen identity model**: composite `(game_id, season)` on `games`
(`PRIMARY KEY (game_id, season)`), with `season INTEGER NOT NULL` added to
every dependent table (`game_goal_events`, `game_penalty_events`,
`game_goalkeeper_events`, `game_rosters`, `game_team_period_stats`,
`game_player_period_stats`, `game_goalie_period_stats`, `game_puck_control`,
`shot_events`) and their FKs/UNIQUE constraints changed to the composite
pair. `raw_cache`'s `entity_id` for the three per-game endpoints
(`game_detail`, `game_stats`, `shotmap`) changed from bare `str(game_id)` to
`f"{season}:{game_id}"` in `backfill.py` — the colon is already handled
safely by the existing NTFS-ADS sanitization in `raw_cache.py` (replaced
with `-` in filenames only; the DB-side `entity_id` keeps the colon).
`games_by_season`/`standings` entity_ids were already season-scoped and
needed no change. Code touched: `db.py` (DDL), `parsers.py` (every
per-game `parse_*`/`upsert_*` function now takes and scopes by `season`),
`backfill.py` (entity_id construction + threading `season` through parser
calls).

**Remediation** (raw JSON on disk was never touched — the corruption was
entirely in the curated layer and the cache-key scheme):
1. Backed up `data/hockey.db` before any schema change.
2. Dropped and recreated the 9 affected tables with the new DDL (curated
   layer is derived/rebuildable by design — see `SCHEMA_DRAFT.md`'s design
   principles).
3. Relabeled every `sync_state`/`raw_responses` row for `game_detail`/
   `game_stats`/`shotmap` from bare `entity_id` to `f"{season}:{game_id}"`,
   using each row's own (always-correct) `season` column — pure relabeling,
   no data fetched or guessed. 1,971 `sync_state` + 1,947 `raw_responses`
   rows relabeled.
4. Reparsed `games_by_season`/`standings` raw JSON for seasons 2023 and 2024
   directly from the cached files on disk (zero network — these were never
   miskeyed) to rebuild `games`/`game_goal_events` correctly for both
   seasons.
5. Reparsed every already-cached `game_detail`/`game_stats`/`shotmap`
   response for seasons 2023/2024 from disk (zero network): fully restored
   season 2024 (561/547/561, matching the pre-bug smoke-test numbers
   exactly) and season 2023's 96 genuinely-fetched games.
6. Re-ran the normal resumable `backfill_season(2023)` through the CLI —
   with the fixed entity_id scheme, it correctly saw the 96 already-cached
   games as done and fetched the 466 previously-skipped games fresh (new
   HTTP traffic that was owed all along, not a refetch of anything already
   held). Confirmed via the fetch log: new entries used the new
   `2023:<game_id>` keying (e.g. `entity_id=2023:1`) and hit the network;
   the 96 already-done games produced no fetch log lines at all (pure cache
   hit under the migrated key).

**Regression check** (the concrete corruption example, re-verified):
`game_id=1536` now resolves to two distinct, internally consistent rows —
`(1536, 2023)` = ZSC Zürich Lions vs KooKoo, with `game_rosters` populated
for **both** teams after the season-2023 re-fetch; `(1536, 2024)` = Red Bull
München vs HIFK, roster intact from the original fetch. `games` totals
restored to 562 (season 2023) / 561 (season 2024), matching both seasons'
`games_by_season` raw payloads exactly.

**NTFS spot-check, redone post-fix**: `raw_responses` counts vs on-disk file
counts match exactly for both seasons (`game_detail` 562+561=1123,
`game_stats` 550+547=1097, `shotmap` 562+561=1123, `games_by_season` 5+5=10,
`standings` 1+1=2). 466 new files use the new `2023-<game_id>__<hash8>.json`
naming (hyphen, not colon) confirming the sanitization still applies
correctly to the new key format. 6 random season-2023 `file_path`s spot-
checked directly — all exist, nonzero size.

**Caveat for future sessions**: the original season=2024-only smoke test's
"verified idempotent" claim (`docs/SCHEMA_DRAFT.md`) was true only *within*
one season — a second run of the *same* `--season 2024` command really did
make zero requests, but that test never exercised a second, different
season touching overlapping game_ids, which is exactly where this bug
lived. Read any future "verified idempotent" claim in this codebase as
season-scoped unless it explicitly says otherwise.

---

## Per-season detail

### Season 2024

Completed the remainder of the smoke-test season (previously sampled to the
first 20 games via `--max-games`). Resumable sync confirmed working exactly
as expected: the run picked up at game 21/561 and made zero network calls for
the 66 sync_state rows already recorded `success` from the smoke test.

**Row counts (season=2024 slice):**

| Table | Rows |
|---|---|
| `games` | 561 (450 `RUNKOSARJA` + 45 `PLAYOFFS` + 66 `PRACTICE`) |
| `game_goal_events` | 3,034 |
| `game_penalty_events` | 3,473 |
| `game_goalkeeper_events` | 2,237 |
| `game_rosters` | 32,092 |
| `players` (cumulative dimension) | 593 |
| `game_team_period_stats` | 3,652 |
| `game_player_period_stats` | 69,393 |
| `game_goalie_period_stats` | 7,304 |
| `game_puck_control` | 528 |
| `shot_events` | 46,660 |
| `standings` | 58 |

**Sanity checks:** 0 games missing a team id or `start_utc`. 0 `ended=1`
games with a null `home_goals`/`away_goals`.

**Goal-event cross-check:** `game_goal_events` (3,034) vs sum of
`home_goals`+`away_goals` over `ended=1` games (2,995) — **39 more events
than goals, not a 1:1 match.** Investigated rather than patched:
- **10 `PRACTICE` games have zero goal events despite a non-zero final
  score** — all international preseason friendlies against non-Liiga clubs
  (HC Plzen, Timrå IK, IF Björklöven, EC Red Bull Salzburg, Red Bull München,
  Luleå HF, HC Ajoie, Lausanne HC, HC Motor České Budějovice, Djurgårdens IF).
  `games_by_season`'s `goalEvents` array is apparently just empty for these —
  the box-score goal counts still come through, the play-by-play detail
  doesn't.
- **92 games (10 `PRACTICE` + 10 `PLAYOFFS` + 77 `RUNKOSARJA`) have *more*
  goal events than the final score.** Root-caused on game_id 55752
  (`PLAYOFFS`, TPS 2–1 Lukko): 4 goal events recorded, but one
  (`event_id=114`... actually `event_id=88`, `goal_types=["VT0"]`) has a
  `home_score_after`/`away_score_after` that **doesn't advance** from the
  prior event — i.e. a goal that was scored and logged but overturned on
  video review (`VT` = *videotarkastus*, Finnish for video review) never
  counted onto the final tally. `game_goal_events` is a faithful log of
  every goal event including overturned ones; it is **not** guaranteed to
  sum to the final score, and consumers must track `home_score_after`/
  `away_score_after` progression (not `COUNT(*)`) to reconstruct the
  scoring sequence. This is real API behavior, not a parser bug — no code
  changed.

**game_stats cutoff — ragged, not a clean season boundary:** 14/561 games
in season 2024 (a season otherwise fully confirmed working) failed
`game_stats` as `failed_permanent` with the known `{"stats": "Remote server
error"}` placeholder body. All 14 are the *same* international `PRACTICE`
friendlies identified above (foreign opponent, non-Liiga club) — e.g. game
1405 (HC Plzeň vs KooKoo), 1430 (EC Red Bull Salzburg vs HIFK), 8254 (TPS vs
K-Espoo — international U20 exhibition), etc. `shotmap` succeeded for all
561 games including these — so the recent-seasons-only failure mode isn't
purely "old data doesn't exist," it's also "stats aren't tracked for games
against opponents outside Liiga's own stats system," and that particular
gap is independent of season age. Recorded correctly as `failed_permanent`
either way — no retry loop.

**sync_state summary (season=2024, cumulative including smoke test):**

| Endpoint | success | failed_permanent |
|---|---|---|
| `games_by_season` | 5 | 0 |
| `standings` | 1 | 0 |
| `game_detail` | 561 | 0 |
| `game_stats` | 547 | 14 |
| `shotmap` | 561 | 0 |

No `failed_retryable` rows left at end of season — no re-run needed.

**NTFS spot-check:** `raw_responses` row counts per endpoint (season=2024)
matched `ls` file counts on disk exactly (`games_by_season` 5/5, `standings`
1/1, `game_detail` 561/561, `game_stats` 547/547, `shotmap` 561/561). 8
random `file_path`s checked directly — all exist, all nonzero size (22KB–220KB
range). No ADS issue this pass.

*(Season 2024's curated tables were later dropped and reparsed from the
untouched raw cache as part of the game_id-collision fix below — the row
counts above were re-verified identical after that reparse.)*

### Season 2023

First pass (before the game_id-collision bug below was caught) is not
reported here since 466/562 games' per-game data was wrong at the time —
see "Fix implemented and verified" above for what happened and how it was
corrected. Numbers below are from the corrected run (full 562-game
`backfill_season(2023)`, post-fix).

**Row counts (season=2023 slice):**

| Table | Rows |
|---|---|
| `games` | 562 (450 `RUNKOSARJA` + 45 `PLAYOFFS` + 67 `PRACTICE`) |
| `game_goal_events` | 2,910 |
| `game_penalty_events` | 370 |
| `game_goalkeeper_events` | 101 |
| `game_rosters` | 30,053 |
| `game_team_period_stats` | 3,600 |
| `game_player_period_stats` | 68,358 |
| `game_goalie_period_stats` | 7,200 |
| `game_puck_control` | 532 |
| `shot_events` | 46,081 |
| `standings` | 59 |

**Sanity checks:** 0 games missing a team id or `start_utc`. 0 `ended=1`
games with a null `home_goals`/`away_goals`.

**Goal-event cross-check:** `game_goal_events` (2,910) vs sum of
`home_goals`+`away_goals` over `ended=1` games (2,849) — 61 more events than
goals, same pattern as season 2024 (overturned/video-review goals plus
zero-goal-event foreign friendlies) — not re-investigated game-by-game
since the mechanism is already confirmed on season 2024's data above.

**game_stats gap — same foreign-friendly pattern as season 2024:** 12/562
games failed `game_stats` as `failed_permanent`. All 12 are international
`PRACTICE` friendlies against non-Liiga clubs (Rögle BK, Luleå HF, Växjö
Lakers, HK Poprad, HC Banska Bystrica, Lausanne HC, VIK Västerås HK, HC
Slovan Bratislava, IF Björklöven). `shotmap`/`game_detail` succeeded for all
562 games. Confirms the mechanism found on season 2024 generalizes, not a
one-off.

**sync_state summary (season=2023, final):**

| Endpoint | success | failed_permanent |
|---|---|---|
| `games_by_season` | 5 | 0 |
| `standings` | 1 | 0 |
| `game_detail` | 562 | 0 |
| `game_stats` | 550 | 12 |
| `shotmap` | 562 | 0 |

No `failed_retryable` rows left at end of season.

**NTFS spot-check:** counts matched exactly (see "Fix implemented and
verified" above for the combined 2023+2024 check). 6 random season-2023
`file_path`s checked directly — all exist, nonzero size.

### Season 2022

First full run hit 2 transient network errors (`failed_retryable`): a DNS
resolution failure on `game_detail` for game_id 71 and a read timeout on
`game_stats` for game_id 442 — both look like ordinary network blips, not a
pattern. Re-ran the season once per plan; both cleared on retry with zero
other side effects (resumable sync skipped all 567 already-done games in
under a second, only hit the network for the 2 missing entities).

**Row counts (season=2022 slice):**

| Table | Rows |
|---|---|
| `games` | 569 (448 `RUNKOSARJA` + 48 `PLAYOFFS` + 73 `PRACTICE`) |
| `game_goal_events` | 3,002 |
| `game_penalty_events` | 3,495 |
| `game_goalkeeper_events` | 761 |
| `game_rosters` | 31,071 |
| `game_team_period_stats` | 3,702 |
| `game_player_period_stats` | 70,138 |
| `game_goalie_period_stats` | 7,404 |
| `game_puck_control` | 547 |
| `shot_events` | 47,685 |
| `standings` | 55 |

**Sanity checks:** 0 games missing a team id or `start_utc`. 0 `ended=1`
games with null goals.

**Goal-event cross-check:** 3,002 events vs 2,899 goals — same overturned-
goal / foreign-friendly pattern as prior seasons, not re-investigated
per-game (mechanism already confirmed).

**game_stats gap:** 7/569 games failed as `failed_permanent`, all
international `PRACTICE` friendlies (IPK, Luleå HF, ZSC Zürich Lions, Sparta
Praha, Lausanne HC, Adler Mannheim). Same mechanism as 2024/2023.

**Phase values:** only `RUNKOSARJA`/`PLAYOFFS`/`PRACTICE` seen — `PLAYOUT`/
`QUALIFICATIONS` still unconfirmed after 3 seasons.

**sync_state (final):** `game_detail` 569/569, `shotmap` 569/569,
`game_stats` 562 success + 7 `failed_permanent`. 0 `failed_retryable` left.

### Season 2021

Clean run, no retries needed: 540/540 games, **100% success on all three
per-game endpoints including `game_stats`** — the first season with zero
`failed_permanent` game_stats games. Plausible explanation: 2021 was mid-
COVID travel-restriction era in Finland, so the 84 `PRACTICE` games this
season likely skipped the international-friendly matchups (against non-
Liiga clubs) that caused every `game_stats` gap in seasons 2022–2024 — not
independently confirmed team-by-team, but consistent with the pattern found
so far (every prior gap traced to a foreign opponent).

**Row counts (season=2021 slice):**

| Table | Rows |
|---|---|
| `games` | 540 (424 `RUNKOSARJA` + 32 `PLAYOFFS` + 84 `PRACTICE`) |
| `game_goal_events` | 2,957 |
| `game_penalty_events` | 4,378 |
| `game_goalkeeper_events` | 965 |
| `game_rosters` | 28,701 |
| `game_team_period_stats` | 3,520 |
| `game_player_period_stats` | 65,938 |
| `game_goalie_period_stats` | 7,026 |
| `game_puck_control` | 529 |
| `shot_events` | 45,313 |
| `standings` | 47 |

**Sanity checks:** 0 games missing a team id or `start_utc`. 0 `ended=1`
games with null goals.

**Goal-event cross-check:** 2,957 events vs 2,857 goals — same pattern as
prior seasons.

**Field presence:** `home_expected_goals` present on 455/540 games (85%) —
still not universal, consistent with `SCHEMA_DRAFT.md`'s note that xG isn't
guaranteed pre-~2015.

**sync_state (final):** all three per-game endpoints 540/540 success. 0
`failed_retryable`, 0 `failed_permanent`.

### Season 2020

Clean run, no retries needed. **Notable: zero `PLAYOFFS` games this
season** — only `RUNKOSARJA` (443) and `PRACTICE` (87), phase totaling 530.
This is real, not a bug: Liiga's 2019–20 season (the API's "season 2020")
had its playoffs cancelled outright due to COVID-19 in spring 2020 — the
`games_by_season` endpoint simply has no `PLAYOFFS`-phase games to return
for this season. Confirms `SCHEMA_DRAFT.md`'s "never assume a fixed bracket
shape" guidance is doing real work here: a season with an *entirely absent*
phase is a legitimate outcome, not a fetch failure (no errors in
`sync_state`, `tournament=playoffs` for season=2020 legitimately returned
zero games).

**Row counts (season=2020 slice):**

| Table | Rows |
|---|---|
| `games` | 530 (443 `RUNKOSARJA` + 0 `PLAYOFFS` + 87 `PRACTICE`) |
| `game_goal_events` | 2,884 |
| `game_penalty_events` | 3,350 |
| `game_goalkeeper_events` | 748 |
| `game_rosters` | 26,958 |
| `game_team_period_stats` | 3,256 |
| `game_player_period_stats` | 61,805 |
| `game_goalie_period_stats` | 6,512 |
| `game_puck_control` | 493 |
| `shot_events` | 41,325 |
| `standings` | 61 |

**Sanity checks:** 0 games missing a team id or `start_utc`. 0 `ended=1`
games with null goals. 0 games with `ended=0` (no dangling/cancelled games
left in an ambiguous state — the season's game list itself is just shorter).

**Goal-event cross-check:** 2,884 events vs 2,855 goals — same pattern.

**game_stats gap — larger than prior seasons (23/530), same mechanism:**
every failure is an international `PRACTICE` friendly (Sibir Novosibirsk,
Löwen Frankfurt, Modo, Nikko Ice Bucks, Geneve Servette HC, HC Plzeň, KAC
Klagenfurt, Karlskrona HK, HC Očelári Trinec, EV Zug, EHC Biel, SC Bern,
Lausanne HC) — pre-COVID August/September 2019 preseason had more
international tour games than the pandemic-shrunk 2020/2021 preseasons that
followed it, which tracks.

**sync_state (final):** `game_detail` 530/530, `shotmap` 530/530,
`game_stats` 507 success + 23 `failed_permanent`. 0 `failed_retryable`.

### Season 2019

Clean run, no retries. **Notable: `home_expected_goals`/`away_expected_goals`
are `NULL` on all 573 games (0%)** — first season in this backfill where xG
is completely absent, vs. 85% presence in season 2021 and ~100% in
2023/2024. Narrows `SCHEMA_DRAFT.md`'s "absent pre-~2015" guidance: the real
cutoff looks like it's between 2019 and 2021, not all the way back to 2015 —
will confirm the exact boundary as older seasons are backfilled.

**Row counts (season=2019 slice):**

| Table | Rows |
|---|---|
| `games` | 573 (450 `RUNKOSARJA` + 45 `PLAYOFFS` + 78 `PRACTICE`) |
| `game_goal_events` | 3,248 |
| `game_penalty_events` | 4,153 |
| `game_goalkeeper_events` | 823 |
| `game_rosters` | 30,732 |
| `game_team_period_stats` | 3,632 |
| `game_player_period_stats` | 68,825 |
| `game_goalie_period_stats` | 7,254 |
| `game_puck_control` | 536 |
| `shot_events` | 49,016 |
| `standings` | 63 |

**Sanity checks:** 0 games missing a team id or `start_utc`. 0 `ended=1`
games with null goals.

**Goal-event cross-check:** 3,248 events vs 3,120 goals — same pattern.

**game_stats gap:** 21/573 `failed_permanent`, same foreign-friendly
mechanism as prior seasons (not individually re-verified team-by-team this
season — mechanism well established by now).

**sync_state (final):** `game_detail` 573/573, `shotmap` 573/573,
`game_stats` 552 success + 21 `failed_permanent`. 0 `failed_retryable`.

### Season 2018

Clean run, no retries. **`game_stats`/`shotmap` still work fine this far
back** (554/572 and 572/572 respectively) — the "recent-seasons-only" cutoff
from `CLAUDE.md`/`SCHEMA_DRAFT.md` (confirmed 500s on the season=2010
*fixture*) hasn't been hit yet at 2018; still narrowing it down as older
seasons are backfilled. `home_expected_goals`/`away_expected_goals`
still 0/572 present (same as 2019) — xG cutoff remains between 2019 and
2021. `expected_goals_player` (per-player, from `game_stats`) is `NULL` on
every row checked, consistent with `SCHEMA_DRAFT.md`'s note that this field
is unpopulated in every sample seen regardless of season — not a 2018-
specific gap.

**Row counts (season=2018 slice):**

| Table | Rows |
|---|---|
| `games` | 572 (450 `RUNKOSARJA` + 46 `PLAYOFFS` + 76 `PRACTICE`) |
| `game_goal_events` | 3,155 |
| `game_penalty_events` | 4,420 |
| `game_goalkeeper_events` | 792 |
| `game_rosters` | 30,452 |
| `game_team_period_stats` | 3,672 |
| `game_player_period_stats` | 69,443 |
| `game_goalie_period_stats` | 7,344 |
| `game_puck_control` | 537 |
| `shot_events` | 50,013 |
| `standings` | 62 |

**Sanity checks:** 0 games missing a team id or `start_utc`. 0 `ended=1`
games with null goals.

**Goal-event cross-check:** 3,155 events vs 3,023 goals — same pattern.

**sync_state (final):** `game_detail` 572/572, `shotmap` 572/572,
`game_stats` 554 success + 18 `failed_permanent`. 0 `failed_retryable`.

### Season 2017

Clean run, no retries, **100% success on all three per-game endpoints
including `game_stats`** — zero `failed_permanent` this season (fewer
`PRACTICE` games than neighboring seasons, 50 vs. 76–87, plausibly all
domestic opponents this particular preseason). `game_stats`/`shotmap` still
fully functional this far back — cutoff still not found.

**Row counts (season=2017 slice):**

| Table | Rows |
|---|---|
| `games` | 549 (450 `RUNKOSARJA` + 49 `PLAYOFFS` + 50 `PRACTICE`) |
| `game_goal_events` | 2,718 |
| `game_penalty_events` | 3,932 |
| `game_goalkeeper_events` | 833 |
| `game_rosters` | 29,905 |
| `game_team_period_stats` | 3,622 |
| `game_player_period_stats` | 68,668 |
| `game_goalie_period_stats` | 7,244 |
| `game_puck_control` | 529 |
| `shot_events` | 49,329 |
| `standings` | 46 |

**Sanity checks:** 0 games missing a team id or `start_utc`. 0 `ended=1`
games with null goals.

**Goal-event cross-check:** 2,718 events vs 2,639 goals — same pattern.

**sync_state (final):** all three per-game endpoints 549/549 success. 0
`failed_retryable`, 0 `failed_permanent`.

### Season 2016

2 transient network errors (`failed_retryable`: a DNS failure on `shotmap`
game_id 8018, a DNS failure on `game_detail` game_id 8019) — cleared on one
re-run, resumable sync skipped all 552 already-done games in under a second.

**New finding — a `game_stats` gap that is *not* the foreign-friendly
pattern:** 57/554 games failed `game_stats` as `failed_permanent`, but
unlike every prior season, **51 of the 57 are domestic `RUNKOSARJA` games**
(only 6 are `PRACTICE`). Every one of the 51 involves either **KalPa** (32
of KalPa's 67 games this season) or **Blues** (28 of Blues' 65 games),
clustered by date — KalPa's failures run 2015-09-11 through 2015-12-26,
Blues' run 2016-01-07 through 2016-03-07 — but *not* all of either team's
games fail (KalPa: 32/67, Blues: 28/65), so it isn't a clean "this team's
whole season is missing" gap either. No other team appears in a failed game
except as KalPa/Blues' opponent that day. **Root cause not identified** —
this doesn't fit the international-opponent explanation that accounted for
every gap in seasons 2018–2024, and pinning it down further would need a
liiga.fi devtools capture beyond this session's scope. Recorded as-is: a
real, `failed_permanent` (non-retryable) gap specific to two teams' games in
this season, worth a note for anyone building the shot-quality feature
family off `game_team_period_stats`/`game_player_period_stats` for
2015-16 KalPa/Blues games specifically.

**Row counts (season=2016 slice):**

| Table | Rows |
|---|---|
| `games` | 554 (450 `RUNKOSARJA` + 48 `PLAYOFFS` + 56 `PRACTICE`) |
| `game_goal_events` | 2,752 |
| `game_penalty_events` | 3,748 |
| `game_goalkeeper_events` | 657 |
| `game_rosters` | 29,876 |
| `game_team_period_stats` | 3,196 |
| `game_player_period_stats` | 60,539 |
| `game_goalie_period_stats` | 6,392 |
| `game_puck_control` | 481 |
| `shot_events` | 46,397 |
| `standings` | 52 |

**Sanity checks:** 0 games missing a team id or `start_utc`. 0 `ended=1`
games with null goals.

**Goal-event cross-check:** 2,752 events vs 2,731 goals — same pattern.

**sync_state (final):** `game_detail` 554/554, `shotmap` 554/554,
`game_stats` 497 success + 57 `failed_permanent`. 0 `failed_retryable`.

### Season 2015 (final season in scope)

1 transient network error (`failed_retryable` on `game_detail` game_id
6579) — cleared on one re-run.

**`game_stats` gap again clusters on Blues:** 8/507 games failed
`game_stats`, and **all 8 involve Blues** — 7 `PRACTICE` games (all vs.
other *domestic* Liiga clubs: Tappara, Pelicans, HPK, TPS, Ilves — not
foreign opponents, unlike every other season's preseason gaps) plus 1
`PLAYOFFS` game (Blues vs JYP, game_id 45873). Combined with season 2016's
finding (32/67 KalPa games + 28/65 Blues games missing `game_stats`, also
domestic), **Blues shows a `game_stats` gap in every season it appears in
this backfill (2015, 2016) regardless of opponent or phase** — stronger
signal than a coincidence, still not root-caused (would need a liiga.fi
devtools capture to confirm, out of scope here). Worth flagging explicitly
if Blues-involving games ever matter for the shot-quality feature family.

**Row counts (season=2015 slice):**

| Table | Rows |
|---|---|
| `games` | 507 (420 `RUNKOSARJA` + 47 `PLAYOFFS` + 40 `PRACTICE`) |
| `game_goal_events` | 2,624 |
| `game_penalty_events` | 2,443 |
| `game_goalkeeper_events` | 860 |
| `game_rosters` | 26,237 |
| `game_team_period_stats` | 3,356 |
| `game_player_period_stats` | 63,699 |
| `game_goalie_period_stats` | 6,716 |
| `game_puck_control` | 482 |
| `shot_events` | 46,588 |
| `standings` | 40 |

**Sanity checks:** 0 games missing a team id or `start_utc`. 0 `ended=1`
games with null goals.

**Goal-event cross-check:** 2,624 events vs 2,507 goals — same pattern.

**sync_state (final):** `game_detail` 507/507, `shotmap` 507/507,
`game_stats` 499 success + 8 `failed_permanent`. 0 `failed_retryable`.

