# PROJECT_CONTEXT — hockey-edge (Liiga + NHL prediction & edge tool)

Paste-ready summary for Claude project memory. Crystallized 2026-07-06; rewritten
2026-09-03, 2026-09-20; rewritten 2026-09-25.

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
effectively dropped.** Treat personal-use as the operating assumption and don't let
"someday public" quietly shape build choices. The only thing the public option still
buys is a reason to keep the prediction log clean, which the invariants already
require anyway.

## Decided stack (don't re-open)

Python, SQLite, local compute, `src/hockey_edge/` package layout, `pyproject.toml` +
`pip install -e .` into a `.venv`. No `PYTHONPATH=src` prefix anywhere. Commands run
from the repo root.

Liiga data from liiga.fi's undocumented JSON API at `https://liiga.fi/api/v2`; 16
endpoints confirmed, catalog and `verified_seasons` in
`src/hockey_edge/ingest/liiga/endpoints.py` (source of truth). NHL from the official
free NHL API; NHL xG bootstrapped from MoneyPuck/Natural Stat Trick.

**Liiga odds: OddsPapi, polling `pinnacle` (primary/benchmark) + `bet365` (fallback).
Settled 2026-09-17, proven live since — do not re-open.** NHL odds via The Odds API
remain a separate, later session.

Models: Elo-style baseline + LightGBM, blended. Targets: NHL binary moneyline; Liiga
three-way regulation 1X2. Metrics: log loss + calibration, never accuracy; benchmark is
odds-implied probabilities (vig removed) from the last pre-game snapshot.

Tests: `tests/`, stdlib `unittest`, no pytest — `python -m unittest discover -s tests`
(67 as of 2026-09-25). They assert against real saved API responses and make zero HTTP
requests.

## Hard invariants

Pre-puck-drop information only (append-only snapshots with `captured_at` UTC);
**immutable prediction log** (corrections go in as new rows under a new
`model_version`); strict walk-forward validation; resumable sync (`sync_state`, raw
JSON cached as files under `data/raw/`, SQLite metadata only); capture-before-parse;
polite scraping (1.5s, normal UA); no autonomous git commits; no affiliate
integrations.

Composite `(season, game_id)` is the primary key everywhere. `game_id` is a per-season
counter on liiga.fi's side and gets reused.

Databases, `data/raw/`, `data/backups/`, `logs/` and `.env` are gitignored. Code, docs,
and small verification fixtures are tracked — including `fixtures/liiga/lineup_probe/`,
`fixtures/oddspapi/` and the regression pairs in `fixtures/liiga/game_detail/` and
`fixtures/liiga/game_stats/`, which are evidence rather than cache and cannot be
regenerated.

## Season numbering — CONFIRMED

liiga.fi uses the **ending-year convention**: `season=2024` is the 2023–24 season, and
`season=2027` is the live 2026–27 season.

## Where the pipeline actually stands (2026-09-25)

**Build order, as reordered 2026-09-24:** 1 Liiga ingest → 2 snapshot capture →
historical recovery → **4 Liiga feature store (next)** → 5 Elo + walk-forward harness →
6 LightGBM + blend → 7 prediction log + dashboard. **NHL ingest (step 3) is deferred**
behind the Liiga feature store — Liiga is the differentiator and its data is in hand.

**Step 1 (Liiga ingest): done** for 2015–2027. `scripts/nightly_sync.py` keeps the live
season current, scheduled daily at **23:00** (moved from 23:30 on 2026-09-25 so it
finishes before the desktop is shut down ~23:30).

**Step 2 (snapshot capture): done and proven.** No odds or lineup window missed since
09-17. 38 lifetime OddsPapi requests. Starter inference (`line==1`) is **71/72 = 98.6%**
against actual starters; the one miss was liiga.fi's own published lineup being wrong
at T-15. Pinnacle's inactive flag depends on distance to puck drop; the closing poll is
reliably live on both books (`docs/SNAPSHOT_FINDINGS.md`).

**Historical recovery: done (2026-09-24/25).** Full record in
`docs/RECOVERY_BACKLOG.md`. In short:
- **`game_detail`:** 1,135 damaged 2015–2024 games refetched via the new
  `resync.py --targets`. Zero-penalty regular-season games **665 → 6**, after a
  grow-from-zero salvage (`--salvage-from-raw`, no HTTP) of 32 games the guard had
  refused over 1–5 lost roster rows. Goal events untouched everywhere.
- A follow-up refetch of 793 never-zero 2015/2016 games changed **nothing**: 2015's
  higher penalty count is a liiga.fi recording style (misconducts and untyped entries
  listed separately), not damage.
- **`game_stats`:** the refetch *damaged* data. liiga.fi now serves 2015–2024 period
  stats stripped (TOI, corsi, faceoffs, PP/SH zeroed; some goals missing) with the
  same row counts, so the row-count guard let 3,953 reparses through. Repaired exactly
  from backup (`scripts/repair_game_stats.py`). Net gain: 1,934 puck-control rows in
  2023/2024 only; 2015–2022 puck control is null at the source.
- **Value guard built** in `resync.py` for `game_stats`: refuse a reparse if a key
  per-game total above zero falls below 50%, or a goal sum moves further from the final
  score. Backtest: refuses 3,767/3,953 stripped reparses (the rest had nothing to lose)
  and 0/44 real live corrections. The nightly sync is now protected.
- **2025/2026 team-level period stats were already stripped when first fetched**
  (2026-08-22): 0 PP/SH instances in every game, team-period goals short of the final
  score in 893/1,192. Player/goalie stats are fine. No clean copy exists.

**Step 4 (feature store): not started.** The plan is in `docs/FEATURE_STORE_PLAN.md`
(design chat, 2026-09-24, updated 2026-09-25). Next session starts at its F0 audits.

Repo state: working tree clean, `origin/main` at `11f262e`, all commits pushed. GitHub
knowledge sync ran 2026-09-25 and is current with that HEAD.

## The finding that changes how to treat liiga.fi

**liiga.fi degrades its own historical data over time.** `game_detail` loses
goalkeeper events and roster rows on refetch; `game_stats` now serves 2015–2024 with
period stats stripped; 2025/2026 team stats were stripped before we ever fetched them.
The API feeds liiga.fi's website, not an archive. Consequences:
- **The local copy (`data/hockey.db` + `data/raw/`) is now better than the source**, and
  for 2015–2024 period stats it is the only good copy. It is not on GitHub. An
  off-machine backup is the most important open task.
- **Never bulk-refetch history without a backup and a value-level diff**
  (`scripts/recovery_report.py --diff` section 8). A row count proves nothing.
- The value guard covers `game_stats` only. `game_detail` and `shotmap` still rely on
  the row-count guard, so don't bulk-refetch `shotmap` history.

## Data rules the feature store must respect

Full list in `docs/DATA_PIPELINE.md` → "Known data gaps". The ones that shape design:
- **A competitive game with zero penalty events is missing data, not a clean game.**
  Seven remain (2016:7862, 2017:4409, 2020:252, 2021:480, 2022:317, 2022:332,
  PLAYOFFS 2022:49298). 2021:480 is damaged at source (0–1 final, no goal events).
- **Count power plays from minors (`penalty_minutes = 2`) or penalty timing**, never raw
  penalty-event counts.
- **For 2025/2026 and the seven 2027 opening-night games, goals come from the final
  score and `game_goal_events`, power plays from `game_penalty_events`** — never from
  `game_team_period_stats`.
- Puck control: values only from 2023; cumulative seconds in 2023–2026, per-period in
  2027. Not a planned feature input.
- xG: absent 2018–2019, ~85% 2021, ~100% from 2023.
- Penalty `player_id = 0` is a team/bench penalty, not a player.

## Open decisions (mine, not Claude Code's)

1. **Off-machine backup of `data/`** — how and where (external drive vs cloud). Urgent
   in practice, simple to decide.
2. **Thin odds benchmark.** Odds exist only from 2026-09-17, so odds-implied log loss
   covers a few hundred games by late November at best. Accept that, or open a
   historical Liiga odds session.
3. **PLAYOFFS as a separate evaluation slice** (v1 evaluates RUNKOSARJA only).
4. **In-house location xG** from `shot_events` coordinates (removes the 2019 xG
   cutoff) — later or never.
5. **Veikkaus as an additional odds source.** Genuinely optional; its own session.
6. **When to move scheduling to the i5 Linux box.** Not urgent; the desktop
   sleep-vs-hibernate dependency remains a silent single point of failure.

Settled and dropped from this list: injecting the 2026-09-01 probe fixtures into
`lineup_snapshots` — **no**, for provenance.

## Immediate next steps, in order

1. **Off-machine backup** of `data/hockey.db`, `data/snapshots.db` and `data/raw/`.
2. Copy this file to `docs/PROJECT_CONTEXT.md` and `FEATURE_STORE_PLAN.md` to
   `docs/FEATURE_STORE_PLAN.md`; commit, push, sync.
3. **Feature store F0** (read-only audits, output `docs/FEATURE_AUDIT.md`), then F1
   (spine + Elo-ready slice), per `docs/FEATURE_STORE_PLAN.md`.
4. Build-order step 5 (Elo + walk-forward harness) as soon as F1 lands.

## Deferred (explicitly, with reasons)

- **NHL ingest** — after the Liiga feature store.
- **`shotmap` historical refetch** — no value guard for it, and liiga.fi degrades
  history. Derive even-strength state from penalty timing instead if F0 finds the
  stored on-ice counts unreliable.
- **Goalkeeper-event recovery** — permanently downgraded; `game_goalkeeper_events`
  cannot identify starters. Starters come from `game_goalie_period_stats`.
- `player_info`/`player_list`/`team_info`/`teams_stats`/`milestones` — cataloged, no
  parser.
- Totals market, news-article injury parsing, football, payments. Public site.

## Gotchas that cost real time

- **The no-shrink guard counts rows; it cannot see stripped values.** Now backed by the
  value guard for `game_stats` only. It is also **all-or-nothing per game**: one
  shrinking table reverts every guarded table for that game, even ones that recovered.
  `--salvage-from-raw` (grow-from-zero, no HTTP) is the one approved exception.
- **A plain "no total may drop" rule is wrong.** Real live corrections drop TOI by up to
  5.3%, corsi by up to 12%, player goals by 1 toward the final score.
- **`backfill.py --force` has no guard at all.** Use `resync.py` for any refetch of
  data already in `hockey.db`.
- **`backfill --season <live season>` needs `--only-ended`.** Otherwise unplayed games'
  pre-game shells are cached as `success` and never refetched.
- **`nightly_sync.py` must force the season-level endpoints**, or the `games` table
  freezes.
- **OddsPapi rate-limits bursts separately from the monthly quota.** `job.py` sleeps
  20 s between books — don't remove it, and don't add a blind retry (a retry is
  another billed request).
- **OddsPapi returns 404 `FIXTURE_NOT_FOUND`** for a tournament with no posted
  fixtures; don't reintroduce a bare `raise_for_status()`.
- **`bookmaker` is required and single-valued** — cost scales with books, not
  fixtures. `/v4/odds` (all 213 bookmakers, 11.6 MB) is not for polling.
- **A new team's OddsPapi id is never guessed.** Names aren't unique (2–3 ids per
  Finnish club); each team is mapped from the first board it actually appeared on.
- **Task Scheduler XML import is encoding-fragile.** Strip the XML declaration before
  `Register-ScheduledTask -Xml`.
- **`schtasks` inline can't set a working directory**, which matters for `.env`
  discovery. Data and log paths anchor to the package, not cwd.
- **Editing the snapshot task needs an elevated PowerShell**; the nightly sync task
  does not.
- **Both tasks run `pythonw.exe`**, so the log file is the only record. A failure
  before logging is configured shows only as a non-zero `LastTaskResult`
  (`Get-ScheduledTaskInfo`).
- **Snapshot window is 11:00–23:00**, because some fixtures start at or before 14:00.
- **`capture_windows.kind` splits odds from lineups.**
- `game_preview` needs a full ISO datetime for `gameDate`. `games_by_date` 502s
  intermittently and ignores `season`. `tournament=playoffs` returns a superset
  including PLAYOUT and QUALIFICATIONS.
- `serie` and `roleCode` vocabularies are open-ended (`PITSITURNAUS` appeared in
  2027). Don't hardcode either.
- Season 2025 `shot_events` gap (79 RUNKOSARJA games, coordinates only) and `sync_state`
  blindness to empty-but-200 responses both still stand.
- **Jokerit** has 18 rows, mostly friendlies, plus a 2025 qualification series it lost
  4–1. The cold-start problem stands.

## Open items

- `homePreviousGames`/`awayPreviousGames` absent from `game_preview` — unresolved.
- Goal-event surplus variance across seasons — unexplained; the target comes from the
  final score, so it doesn't block anything.
- The `2701312` starter disagreement: wrong published `line=1` vs genuine late change
  can't be told apart from stored data.
- Game `2701323` (2026-09-23) had team-period goals 2 vs a final of 3 on first fetch;
  expected to self-correct via nightly resync. Worth one glance in F0.
- Failure alerting beyond CRITICAL log lines — not built.
- Review liiga.fi (and, if used, Veikkaus) ToS before anything public.

Closed since the last version: the PLAYOFFS-in-the-bad-window question (parked for
good — no partial damage found anywhere); the 2027 opening-night ingest question (data
is there; their team-level PP counts are zero and handled by the data rules above).

## Meta — the pattern worth remembering

Every significant problem in this project has been an **unverified assumption that
failed silently rather than loudly**: `game_id` uniqueness, season numbering, the NTFS
colon bug, `sync_state` blindness to empty-but-200, the API key in
`raise_for_status()` text, OddsPapi's docs vs its live behaviour, enrichment vs
mutation, array order vs `line==1`, and the off-season odds board.

The 2026-09-24/25 sessions add the most expensive one yet: **a safety check that
measures the wrong thing passes silently.** The row-count guard reported "no shrink"
on 3,953 games whose values had been gutted. It was caught only because the result was
checked by value, not by count. And two habits worth keeping: the value guard was
**backtested against real corrections before it was trusted**, which is how the naive
"no drop" rule was rejected; and the 2015 penalty gap was **tested by refetch before
being called damage**, which showed it wasn't. When a number looks like a finding, try
to break it first.
