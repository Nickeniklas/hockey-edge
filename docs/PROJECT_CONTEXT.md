# PROJECT_CONTEXT — hockey-edge (Liiga + NHL prediction & edge tool)

Paste-ready summary for Claude project memory. Crystallized 2026-07-06; rewritten
2026-09-03; rewritten 2026-09-20.

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
not bare predictions. **Primary user is Niklas himself; the public/commercial angle is
effectively dropped** — he doesn't follow much sport, doesn't gamble, and the selling
motivation was always weak. Treat personal-use as the operating assumption and don't
let "someday public" quietly shape build choices. The only thing the public option
still buys is a reason to keep the prediction log clean, which the invariants already
require anyway.

## Decided stack (don't re-open)

Python, SQLite, local compute, `src/hockey_edge/` package layout, `pyproject.toml` +
`pip install -e .` into a `.venv`. No `PYTHONPATH=src` prefix anywhere. Backfill-style
commands run from the repo root.

Liiga data from liiga.fi's undocumented JSON API at `https://liiga.fi/api/v2`; 16
endpoints confirmed, catalog and `verified_seasons` in
`src/hockey_edge/ingest/liiga/endpoints.py` (source of truth). NHL from the official
free NHL API; NHL xG bootstrapped from MoneyPuck/Natural Stat Trick.

**Liiga odds: OddsPapi, polling `pinnacle` (primary/benchmark) + `bet365` (fallback).
Settled 2026-09-17, proven over two live game nights 2026-09-18/19 — do not re-open.**
NHL odds via The Odds API remain a separate, later session.

Models: Elo-style baseline + LightGBM, blended. Targets: NHL binary moneyline; Liiga
three-way regulation 1X2. Metrics: log loss + calibration, never accuracy; benchmark is
odds-implied probabilities (vig removed) from the last pre-game snapshot.

Tests: `tests/`, stdlib `unittest`, no pytest — `python -m unittest discover -s tests`.
They assert against real saved API responses and make zero HTTP requests.

## Hard invariants

Pre-puck-drop information only (append-only snapshots with `captured_at` UTC);
**immutable prediction log** (append-only, never edited or deleted — corrections go in
as new rows under a new `model_version`, because a log that can be rewritten can't
evidence what was predicted before a game); strict walk-forward validation; resumable
sync (`sync_state`, raw JSON cached as files under `data/raw/`, SQLite metadata only);
capture-before-parse (the raw payload is kept whether or not parsing succeeds); polite
scraping (1.5s, normal UA); no autonomous git commits; no affiliate integrations.

Composite `(season, game_id)` is the primary key everywhere. `game_id` is a per-season
counter on liiga.fi's side and gets reused; assuming global uniqueness already caused
one live corruption incident.

Databases, `data/raw/`, `logs/` and `.env` are gitignored. Code, docs, and small
verification fixtures are tracked — including `fixtures/liiga/lineup_probe/` and
`fixtures/oddspapi/`, which are evidence rather than cache and cannot be regenerated.

## Season numbering — CONFIRMED

liiga.fi uses the **ending-year convention**: `season=2024` is the 2023–24 season, and
`season=2027` is the live 2026–27 season.

## Where the pipeline actually stands (2026-09-20)

**Step 1 (Liiga ingest): done** for seasons 2015–2027. `data/hockey.db` holds 12
historical seasons (6,736 games) plus season 2027's 595 fixtures, ingested 2026-09-01
with zero failures. `scripts/nightly_sync.py` keeps the live season current, scheduled
daily at 23:30.

**Step 2 (snapshot capture job): done and proven.** Phase 4 — watching a full game
night end to end — is closed, and it was two nights, not one:

- **2026-09-18 and 2026-09-19: complete 11:00–23:00 coverage, every 15 minutes, zero
  gaps on both days.** No ERROR or CRITICAL in `logs/snapshot_job.log` since the
  2026-09-17 429, which remains the only one ever. Both scheduled tasks report
  `LastTaskResult 0`; nightly sync ran clean at 23:30 both nights.
- **Odds: 30 windows satisfied, 0 missed, 9 pending** (all for the 2026-09-22 slate).
  Every closing window landed roughly 10 minutes after due — tick granularity, i.e.
  ~T-15 before puck drop. 09-18: 3 closing windows, all `pinnacle,bet365`. 09-19: 7
  closing windows, 6 `pinnacle,bet365`, 1 (`2701316`, the noon HIFK–Lukko) bet365 only.
- **Prices are sane**: 26 parsed rows at the 09-19 close, overrounds 1.047–1.086, no
  NULL prices, every row resolved to a liiga.fi `game_id` — zero unresolved since
  09-18.
- **Budget: 26 lifetime OddsPapi requests** against the job's 200 ceiling, at 2 per
  poll. Not a constraint at current cadence.

**Step 3 (NHL ingest): not started.** **Step 4 (feature store): not started, and
unblocked on data.** The build order is genuinely free to resume at either.

Repo state: working tree clean, `origin/main` at `e19ed8e`, all commits pushed.
GitHub knowledge sync ran 2026-09-20 and is current with that HEAD.

## Two findings from the live nights — both worth carrying

**1. Pinnacle's inactive flag is distance from puck drop, not randomness.** The open
question from 09-18 (is `bookmakerIsActive: false` time-of-day, pre-lineup, or random?)
is answered. Bucketing every Pinnacle row since 09-18 by hours-to-start:

| Pinnacle | >12h | 4–12h | 1–4h | <1h |
|---|---|---|---|---|
| unparsed (inactive) | 15 | 1 | 2 | 2 |
| parsed | 20 | 9 | 23 | 9 |

43% inactive more than 12h out, ~8% in the 1–4h band, and the only two `<1h` cases are
the single bet365-only fixture above. bet365 had 8 unparsed too (4 suspended, 4
bookmaker inactive), never at `<1h`. **The closing capture — the one that matters — is
reliably live on both books.** Early-window captures should be treated as best-effort;
the closing one is the benchmark.

**2. First starter-inference disagreement: 58/59 (98.3%), and it isn't a code bug.**
`scripts/verify_starters.py` flags `season=2027 game_id=2701312`, away/TPS: the captured
lineup had Markus Ruusu (#30, `line=1`) at both the mid and closing windows, but
`game_goalie_period_stats` shows #36 (`30117507`) played all three periods and Ruusu
faced zero shots. Either liiga.fi's published `line=1` was itself wrong, or TPS changed
starter inside the last 15 minutes. **This is exactly what
`starter_confidence='inferred_structural'` exists for** — but it means the field is
~98% accurate, not 100%, and feature code must not treat it as ground truth. Keep
running `verify_starters.py` as games accumulate; one disagreement in 59 is a rate to
monitor, not yet a pattern.

**A bonus data point, not a finding:** on 09-19, game `2701317` (Ässät–K-Espoo) bet365
had the home side at 1.52 ML / 1.82 in the 1X2 against Pinnacle's 1.787 / 2.32. A
home/away swap in the parse was checked and ruled out — swapping makes the gap worse,
so the mapping is right and it's a genuine soft line. Ässät won 2–1. That spread
between a sharp and a soft book is the thing this project exists to detect, arriving
before there's a model to act on it.

## Open decisions (mine, not Claude Code's)

1. **Veikkaus as an additional source.** Not a blocker any more — OddsPapi covers the
   benchmark. The remaining case for Veikkaus is that it's the book actually bettable
   in Finland, so a Veikkaus edge is the actionable one, and liiga.fi's own games API
   exposes Veikkaus odds (already used in another of my projects), which may be a
   cheaper route than scraping veikkaus.fi. Veikkaus is **not** among OddsPapi's 213
   bookmakers. Still untested, still its own session, now genuinely optional.
2. **Whether to inject the 2026-09-01 probe fixtures into `lineup_snapshots`.**
   Still no, and now more firmly: the table's value is that every row came from the
   production capture path at a real window, and with two clean nights of real rows the
   probe data adds nothing worth the provenance damage. The fixtures stay on disk and
   cited in the docs.
3. **When to move scheduling to the i5 Linux box.** Wake timers have worked for two
   nights, so this is no longer urgent — but the current setup depends on the desktop
   sleeping rather than hibernating, which is a silent single point of failure.

## Immediate next steps, in order

1. **Record the two findings above in `docs/SNAPSHOT_FINDINGS.md` and a new CLAUDE.md
   Status entry** — Claude Code has offered and is waiting on the go-ahead. Do this
   before starting new work; it's the last piece of Phase 4.
2. **Pick the next build-order step: 3 (NHL ingest) or 4 (feature store).** Both are
   unblocked. NHL is mostly plumbing against a documented API and widens the dataset;
   the feature store is the step that turns the existing Liiga data into something a
   model can use, and it's where leakage discipline gets tested for real. Feature store
   first is the stronger case — it exercises the invariants while the capture path is
   fresh, and it can be built and validated on Liiga alone.
3. `docs/RECOVERY_BACKLOG.md`'s 1,137-game recovery — still unrun, still not blocking
   anything, worth doing before the feature store depends on penalty events.
4. NHL ingest, Elo baseline + walk-forward validation, LightGBM blend, prediction log.

## Deferred (explicitly, with reasons)

- **`game_detail` recovery of ~1,137 damaged 2015–2024 games** — commands ready in
  `docs/RECOVERY_BACKLOG.md`. Recommended path is `resync.py --days 4380 --endpoints
  game_detail` (~2.8h) rather than the faster unguarded `backfill.py` alternative; the
  no-shrink guard is what makes it safe. 27 games from 2025/2026 excluded as more
  likely genuine than broken.
- **`game_stats`/`shotmap` refetch for ~11,000 missing `game_puck_control` rows** — no
  feature family depends on puck control.
- **Goalkeeper-event recovery** — downgraded permanently. `game_goalkeeper_events`
  cannot establish who started (zero `begin_time=0` rows exist anywhere), so no feature
  family depends on it.
- `player_info`/`player_list`/`team_info`/`teams_stats`/`milestones` — cataloged, no
  parser.
- Totals market, news-article injury parsing, football, payments.
- Public site (see Open decisions).

## Gotchas that cost real time

- **`backfill --season <live season>` needs `--only-ended`.** Without it, per-game
  endpoints are fetched for all ~596 fixtures and each unplayed game's pre-game shell
  is cached as `sync_state` success — which backfill then skips forever, so the real
  post-game data never arrives.
- **`nightly_sync.py` must force the season-level endpoints.** Otherwise `sync_state`
  serves `games_by_season`/`standings` from cache indefinitely and the job never learns
  that new games have ended.
- **OddsPapi rate-limits bursts separately from the monthly quota.** Two back-to-back
  board calls got the second one 429'd (2026-09-17). `job.py` sleeps
  `ODDS_BOOK_DELAY_SECONDS` (20s) between books — don't remove that spacing, and don't
  add a blind retry, since a retry is another billed request. Every poll since has
  returned 200 on both books.
- **OddsPapi returns 404 `FIXTURE_NOT_FOUND` for a tournament with no posted fixtures**,
  not 200 with an empty array. The job treats it as "no odds yet"; don't reintroduce a
  bare `raise_for_status()`.
- **`bookmaker` is required and single-valued** — a poll costs one request per book, so
  cost scales with books, not fixtures. `/v4/odds` (per-fixture, all 213 bookmakers,
  11.6 MB) is not for polling.
- **A new team's OddsPapi id is never guessed.** Names aren't unique (2–3 ids per
  Finnish club, junior/women's sides among them); each of the 17 is mapped from the
  first board it actually appeared on. Any promoted team follows the same rule.
- **Task Scheduler XML import is encoding-fragile.** `schtasks /XML` failed on a
  UTF-16 declaration over UTF-8 bytes; `Register-ScheduledTask -Xml` then failed
  because PowerShell holds the string as UTF-16, making any declaration a
  contradiction. Working path strips the declaration:
  `$xml = (Get-Content -Raw ...) -replace '<\?xml[^>]*\?>', ''`.
- **`schtasks` inline can't set a working directory.** Matters for `load_dotenv()`'s
  `.env` discovery, which the job needs for `ODDSPAPI_KEY` on every due tick. Data and
  log paths are not cwd-dependent — they anchor to `Path(__file__).resolve().parents[3]`.
- **Editing the snapshot task needs an elevated PowerShell** (its XML carries an
  explicit `<Principal>`); the schtasks-registered nightly sync does not.
- **Both tasks run `pythonw.exe`**, so `sys.stdout`/`sys.stderr` are None and the log
  file is the only record. A failure before `_configure_logging()` finishes leaves no
  trace except a non-zero `LastTaskResult` — check with `Get-ScheduledTaskInfo`.
- **Snapshot window is 11:00–23:00, not 14:00.** Three 2026-27 fixtures start at or
  before 14:00 local, and their T-25min closing window would be permanently missed.
- **`capture_windows.kind` splits odds from lineups.** A shared status once let a
  successful odds poll hide windows from lineup capture.
- `game_preview` requires a full ISO datetime for `gameDate`; a bare date 500s.
- `games_by_date` 502s intermittently — retry, not fatal — and silently ignores the
  `season` param.
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
- The `2701312` starter disagreement: liiga.fi's published `line=1` being wrong versus
  a genuine late change is undistinguished, and there may be no way to tell from stored
  data alone.
- Whether season-2027 opening-night post-game data is fully ingested — an earlier
  `nightly_sync.py` issue was noted against the openers and never confirmed resolved.
  Worth one read-only check before the feature store trusts early-season rows.
- Failure alerting beyond CRITICAL log lines — not built. Two clean nights is not
  monitoring; nothing currently tells anyone the job stopped.
- Review liiga.fi (and, if used, Veikkaus) ToS before anything public.

## Meta — the pattern worth remembering

Every significant problem in this project has been an **unverified assumption that
failed silently rather than loudly**: `game_id` uniqueness, ending-year season
numbering, the NTFS colon bug, `sync_state` blindness to empty-but-200, the API key in
`raise_for_status()` text, OddsPapi's docs vs its live behaviour, the
enrichment-vs-mutation framing, array-order-vs-`line==1` for starting goalies, and
"`/odds-by-tournaments` has no prices" (which was the off-season board, not the
endpoint). None surfaced as an error. All surfaced because someone checked a claim that
read like a fact.

The 09-19 session adds two of the good kind: the Pinnacle inactive flag was bucketed
rather than guessed at, and the book disagreement on `2701317` was tested for a
home/away swap before being accepted as real. That's the habit — when a number looks
like a finding, try to break it first.
