# PROJECT_CONTEXT — hockey-edge (Liiga + NHL prediction & edge tool)

Paste-ready summary for Claude project memory. Crystallized 2026-07-06; rewritten
2026-09-03.

Owned by the design chat. Emitted whole and replaced wholesale at session close. Not
edited by Human or by Claude Code sessions.

## Project

Niklas (IT student, Finland; Python/data/ML skills; Windows RTX 3060 Ti desktop + M1
Mac, plus an unused 5th-gen i5 desktop earmarked as a future Linux host) is building a
**personal-use-first hockey prediction tool for Liiga and NHL**. Core loop: ingest
historical data → capture pre-game info (confirmed lineups, starting goalies,
bookmaker odds) timestamped before puck drop → rolling features → calibrated win
probabilities → compare vs odds to surface edges → immutable, timestamped prediction
log.

It's an **edge finder** (model probability vs bookmaker odds), not a stats site and
not bare predictions. **Primary user is Niklas himself, and the public/commercial
angle is leaning toward being dropped entirely** — he doesn't follow much sport,
doesn't gamble, and the selling motivation was always weak. That decision has its own
session (see Open decisions); until it's made, don't let "someday public" quietly
shape build choices.

## Decided stack (don't re-open)

Python, SQLite, local compute, `src/hockey_edge/` package layout, `pyproject.toml` +
`pip install -e .` into a `.venv`. No `PYTHONPATH=src` prefix anywhere. Backfill-style
commands run from the repo root.

Liiga data from liiga.fi's undocumented JSON API at `https://liiga.fi/api/v2`; 16
endpoints confirmed, catalog and `verified_seasons` in
`src/hockey_edge/ingest/liiga/endpoints.py` (source of truth). NHL from the official
free NHL API; NHL xG bootstrapped from MoneyPuck/Natural Stat Trick.

Models: Elo-style baseline + LightGBM, blended. Targets: NHL binary moneyline; Liiga
three-way regulation 1X2. Metrics: log loss + calibration, never accuracy; benchmark is
odds-implied probabilities (vig removed) from the last pre-game snapshot.

**Odds sourcing is NOT settled — see Open decisions.**

## Hard invariants

Pre-puck-drop information only (append-only snapshots with `captured_at` UTC);
**immutable prediction log** (append-only, never edited or deleted — corrections go in
as new rows under a new `model_version`, because a log that can be rewritten can't
evidence what was predicted before a game); strict walk-forward validation; resumable
sync (`sync_state`, raw JSON cached as files under `data/raw/`, SQLite metadata only);
polite scraping (1.5s, normal UA); no autonomous git commits; no affiliate
integrations.

Databases, `data/raw/`, `logs/` and `.env` are gitignored. Code, docs, and small
verification fixtures are tracked — including `fixtures/liiga/lineup_probe/`, which is
evidence rather than cache and cannot be regenerated.

## Season numbering — CONFIRMED

liiga.fi uses the **ending-year convention**: `season=2024` is the 2023–24 season. This
was the source of a real gap — the original backfill omitted two completed seasons.

## Status (2026-09-03)

**Step 1 (Liiga ingest): COMPLETE, seasons 2015–2027.** `data/hockey.db`, composite
`(season, game_id)` primary key — `game_id` is a per-season counter that resets
annually and is NOT globally unique.

Season 2027 ingested 2026-09-02 with zero failures: 595 games (544 RUNKOSARJA, 41
PRACTICE, 10 PITSITURNAUS), 58 ended. All 7 completed openers came through with full
per-game data — rosters, goal/penalty/goalkeeper events, team/player/goalie period
stats, shot events, xG. `game_puck_control` thin at 1–3 rows/game, which is the
mutation gap the nightly resync exists to close.

**Step 2 (snapshot capture): DEPLOYED AND RUNNING.**

Lineup capture is live. `snapshot/lineups.py` fetches `game_detail` (+`game_preview`,
best-effort) and returns one `LineupSnapshot` per team-side: the 22 confirmed
line-assigned players, goalie count, and the inferred starter. Append-only into
`snapshots.db` keyed `(league, season, game_id, team_role)`, full raw payload retained
regardless of parse outcome. Wired into `job.py`, which marks windows satisfied
independently of the dormant odds path.

Odds capture is **not** live — `NullOddsProvider` is wired, zero HTTP. The mechanism
is complete; only the provider is missing.

Scheduling is deployed on the Windows desktop:
- **Snapshot job** — registered task, every 15 min, 11:00–23:00 local, `WakeToRun=true`
  (wake timers confirmed enabled in the power plan), `StartWhenAvailable=true`,
  `IgnoreNew` overlap policy, no execution time limit. Definition committed at
  `docs/snapshot_job_task_scheduler.xml`.
- **Nightly sync** — `scripts/nightly_sync.py` daily at 23:30, registered inline, so
  recovery commands are documented in `docs/RESYNC.md` rather than an XML file. No
  wake timer, so a sleeping machine skips the night — harmless, since both passes are
  resumable and `--days 7` bounds the recovery window.

`lineup_snapshots` is still empty. First rows arrive when the job hits a real slate.
**Next games are Friday 2026-09-04** — the openers were 09-01, then a three-day gap.

**Step 3 (NHL ingest): not started.**

## The starting-goalie finding — and the near-miss

Resolved 2026-09-01 by probing all 7 openers at T-30: `line` was populated for exactly
22 players on all 14 team-sides, so liiga.fi does publish confirmed lineups pre-game.
At T-4d the same probe showed zero line assignments and up to 8 goalies per squad, so
the signal genuinely appears close to puck drop.

Starter is identified by **`line==1`**, recorded with
`starter_source='goalie_line_value'` and `starter_confidence='inferred_structural'`.
Ambiguous cases (0 or 2+ goalies at `line==1`) store `starter_player_id=None` rather
than guessing.

**The near-miss worth remembering:** the working hypothesis had been *array order* —
manual cross-referencing against Flashscore matched the first-listed goalie in 7 of 7
games. Scored properly against ground truth, **`line==1` got 14/14 and array order got
7/14, exactly chance.** The manual check had landed on the subset where the two
coincide. Shipping array order would have made half of all goalie features describe
backups, silently, all season. Same shape as the `game_id` collision and the
enrichment framing: an apparent confirmation on a sample that couldn't discriminate.

**`game_goalkeeper_events` cannot establish who started** — zero `begin_time=0` rows
exist anywhere, and 36% of season-2026 games have no rows at all. Ground truth is
`game_goalie_period_stats` period-1 `shots_on_goal > 0`, cross-checked against
`game_goalkeeper_events` to flag early-substitution edge cases rather than silently
resolve them. This also downgrades the goalkeeper-event recovery in the backlog: no
feature family depends on that table.

`scripts/verify_starters.py` (read-only, both DBs opened `mode=ro`) scores captured
starters against that ground truth. Run it periodically once games accumulate — it
converts a structural inference into a monitored one.

## CRITICAL FINDING — liiga.fi responses mutate in BOTH directions

**Corrects the earlier "retroactive enrichment" framing, which was wrong and
misleading.** The first observed case (a `game_stats` response gaining `puckStats`
periods) was enrichment, and naming the phenomenon after one instance baked a
directional assumption into every downstream decision.

Refetching `game_detail` for season 2024 `game_id=1` returned **fewer** goalkeeper
events than the stored response (7 rows → 1). Across four samples: shrink, grow,
no-change, no-change. The right term is **non-monotonic mutation**.

**The fix, in `resync.py`: a universal no-shrink guard.** A refetched response is
reparsed into curated tables only if no curated table it feeds would end up with fewer
rows. If any would shrink: skip the reparse, keep existing curated rows, still write
the new raw response to disk (append-only), log WARNING with per-table before/after
counts. Comparison is per-table totals, not per-team — a penalty event legitimately
reassigned between home and away arrays must not trip it. Applied to all three
endpoints.

**Variation is temporal, not per-request.** Three back-to-back fetches returned
identical hashes, so refetching isn't a coin flip.

**Rate limiting ruled out.** Across 20,280 `sync_state` and 20,122 `raw_responses`
rows: zero non-200 statuses, zero nulls, zero `failed_retryable`. Never throttled or
blocked. The 160 `failed_permanent` rows are all HTTP 200 with an error-placeholder
body.

## The 2026-07-19/21 bad-response window — ~1,164 damaged games

**1,164 of 6,736 games have zero `game_penalty_events` despite a `success` sync_state
row.** Hockey games always have penalties, so this is source damage. Season 2023 is 92%
affected (517/562); baseline elsewhere is 2–39%.

All damage clusters in one ~34-hour window, **2026-07-19T19:55Z to 2026-07-21T05:54Z**
— the original 10-season backfill. Zero damaged files outside it. Two symptom classes:

1. **483 files lack a top-level `game` key entirely** — rosters present, everything
   else missing.
2. **`game` key present but `penaltyEvents` empty for both teams** — season 2023's 450
   RUNKOSARJA games are all this type.

**Both classes are recoverable**, confirmed by live refetch. `goalKeeperEvents`
recovery is unreliable (1 of 2 test games), but that now matters less given the
`game_goalie_period_stats` finding above.

**Unexplained, logged, not theorised about:** 45 PLAYOFFS games fetched in the exact
same 78-minute window that broke 450 RUNKOSARJA games came through 100% clean.

Recovery is scoped and ready but **not run** — see `docs/RECOVERY_BACKLOG.md`.

## OddsPapi — Shape B CONFIRMED, budget maths broken

`/v4/odds-by-tournaments` is a **fixture-board listing, not a prices endpoint**. Live
responses carry `fixtureId`, participant ids, `startTime`, and a `hasOdds` flag — no
`bookmakerOdds`, no markets, no prices, despite `hasOdds: true`. OddsPapi's own
documentation describes the full-markets shape; the live API disagrees with its docs.
Confirmed twice.

- Real pricing needs one `/v4/odds` call **per fixture**. At ~100 Liiga games/month
  across three windows that's ~225–300 requests against a 250/month free tier. The
  "one poll covers the whole board" premise the design rested on is dead.
- `bookmaker` is **required**, not a filter (400 without it).
- `/v4/participants?sportId=15` returns the whole sport's id→name map in one cacheable
  call, so the join is a one-time lookup.
- OddsPapi gives bare numeric participant ids with no names, and `startTime` alone
  can't disambiguate — five liiga.fi games share one 2026-09-05T14:00:00Z kickoff. Join
  must be participant-id → name → liiga.fi team, `startTime` as tie-breaker only.

## Open decisions (mine, not Claude Code's)

1. **Odds provider — the only thing blocking a complete pipeline.** Options: drop to
   closing-only OddsPapi/Pinnacle as a sharp benchmark (~100 req/month, fits the tier),
   or make **Veikkaus** primary via scraping. Veikkaus is the book actually bettable in
   Finland, so a Veikkaus edge is the actionable one. **Nothing about Veikkaus has been
   tested** — unknown: server-rendered vs JSON-behind-the-page, bot protection, market
   shape, how far ahead lines post. Their ToS almost certainly prohibits automated
   access: low-stakes for a personal tool at low rate, a real blocker for anything
   public. **Its own session.**
2. **Public vs personal.** Own session, leaning toward dropping. A public version
   wouldn't need to republish anyone's odds — publishing the model's own timestamped
   probabilities is the track record; the edge calculation stays private. Dropping
   public removes roughly half the deferred list.
3. **Whether to inject the 2026-09-01 probe fixtures into `lineup_snapshots`.**
   Currently no — the table's value is that every row came from the production capture
   path at a real window, and a row claiming provenance it doesn't have corrupts that.
   The fixtures are on disk and cited in the docs regardless.

## Immediate next steps, in order

1. **Verify the first real capture.** Friday 2026-09-04 is the next slate. Check
   `logs/snapshot_job.log` for `lineup capture ok`, then confirm `lineup_snapshots` has
   rows, then run `scripts/verify_starters.py` once those games finish.
2. **Odds provider decision** (Open decision 1) → then Phase 3's odds parsing.
3. NHL ingest, feature store, Elo baseline + walk-forward validation, LightGBM blend.
4. Migrate scheduling to the i5 Linux box, removing the wake-timer dependency.

## Deferred (explicitly, with reasons)

- **`game_detail` recovery of ~1,137 damaged 2015–2024 games** — commands ready in
  `docs/RECOVERY_BACKLOG.md`. Recommended path is `resync.py --days 4380 --endpoints
  game_detail` (~2.8h) rather than the faster unguarded `backfill.py` alternative; the
  no-shrink guard is what makes it safe. 27 games from 2025/2026 excluded as more
  likely genuine than broken.
- **`game_stats`/`shotmap` refetch for ~11,000 missing `game_puck_control` rows** — no
  feature family depends on puck control.
- `player_info`/`player_list`/`team_info`/`teams_stats`/`milestones` — cataloged, no
  parser.
- Totals market, news-article injury parsing, football, payments.
- Public site (pending Open decision 2).

## Gotchas that cost real time

- **`backfill --season <live season>` needs `--only-ended`.** Without it, per-game
  endpoints are fetched for all ~596 fixtures and each unplayed game's pre-game shell
  is cached as `sync_state` success — which backfill then skips forever, so the real
  post-game data never arrives.
- **`nightly_sync.py` must force the season-level endpoints.** Otherwise `sync_state`
  serves `games_by_season`/`standings` from cache indefinitely and the job never learns
  that new games have ended.
- **Task Scheduler XML import is encoding-fragile.** `schtasks /XML` failed on a
  UTF-16 declaration over UTF-8 bytes; `Register-ScheduledTask -Xml` then failed
  because PowerShell holds the string as UTF-16, making any declaration a
  contradiction. Working path is to strip the declaration:
  `$xml = (Get-Content -Raw ...) -replace '<\?xml[^>]*\?>', ''`.
- **`schtasks` inline can't set a working directory.** Matters for `load_dotenv()`'s
  `.env` discovery once a real odds provider needs `ODDSPAPI_KEY`. Data and log paths
  are not cwd-dependent — they anchor to `Path(__file__).resolve().parents[3]`.
- **Snapshot window is 11:00–23:00, not 14:00.** Three 2026-27 fixtures start at or
  before 14:00 local, and their T-25min closing window would be permanently missed.
- `game_preview` requires a full ISO datetime for `gameDate`; a bare date 500s.
- `games_by_date` 502s intermittently — retry, not fatal.
- `games_by_date` silently ignores the `season` param.
- `tournament=playoffs` returns a superset including PLAYOUT and QUALIFICATIONS.
- `serie` vocabulary is **open-ended** — `PITSITURNAUS` appeared in 2027 and was in no
  prior catalog. `roleCode` vocabulary also varies (`P/H/MV/KP` pre-game vs
  `KH/VP/OL/VL/MV/OP/H` in older completed-game payloads). Don't hardcode either.
- Season 2025 `shot_events` gap (79 RUNKOSARJA games, coordinates only) and
  `sync_state` blindness to empty-but-200 responses both still stand.
- **Jokerit is not a blank slate, but also didn't earn promotion where it looks.**
  `hockey.db` has 18 Jokerit rows across 5 seasons, mostly PRACTICE friendlies, plus a
  5-game season-2025 QUALIFICATIONS series vs Pelicans — **which Pelicans won 4–1**.
  The Elo cold-start problem stands: five competitive games from 16 months ago, all
  losses.

## Open items

- 45 clean PLAYOFFS games inside the window that broke 450 RUNKOSARJA games —
  unexplained.
- `homePreviousGames`/`awayPreviousGames` absent from `game_preview` despite being
  noted in `endpoints.py` — unresolved.
- Goal-event surplus variance (+2 on the 2027 openers, in line with the known pattern)
  — unexplained.
- Failure alerting beyond CRITICAL log lines — not built.
- Review liiga.fi (and, if used, Veikkaus) ToS before anything public.

## Meta — the pattern worth remembering

Every significant problem in this project has been an **unverified assumption that
failed silently rather than loudly**: `game_id` uniqueness, ending-year season
numbering, the NTFS colon bug, `sync_state` blindness to empty-but-200, the API key in
`raise_for_status()` text, OddsPapi's docs vs its live behaviour, the
enrichment-vs-mutation framing, and array-order-vs-`line==1` for starting goalies.
None surfaced as an error. All surfaced because someone checked a claim that read like
a fact. The stop-and-report gates in Claude Code prompts exist for exactly this reason
and have caught something every time they've fired — including twice this session,
where running the instruction literally would have caused damage.
