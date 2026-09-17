# hockey-edge

Personal-use-first hockey prediction & edge tool for **Liiga + NHL**: data ingest →
feature store → calibrated win probabilities → compared against bookmaker odds →
immutable prediction log. Liiga is the differentiator — nobody models it seriously,
and its odds are softer.

Full plan: [docs/PLAN.md](docs/PLAN.md). Data contract:
[docs/DATA_PIPELINE.md](docs/DATA_PIPELINE.md). Model contract:
[docs/MODEL.md](docs/MODEL.md). Operating brief (stack, hard rules, current status):
[CLAUDE.md](CLAUDE.md).

## Status

**Build-order step 1 (Liiga historical ingest) covers seasons 2015–2027.**
The original 10-season backfill (2015–2024, 5,517 games) ran first;
seasons 2025 and 2026 were added in a later pass (2026-08-23), bringing
`data/hockey.db` to 12 seasons and 6,736 games. Season 2027 (the live
2026-27 season) was ingested on 2026-09-01 — 595 fixtures, of which 58 had
ended at that point, all fetched clean with zero failures. Keeping it
current is now an ongoing job, not a one-off: see `scripts/nightly_sync.py`
below.

`docs/BACKFILL_RESULTS.md` has the detailed per-season row counts, sanity
checks, and known data gaps for the original 2015–2024 run; **read it before
trusting the DB for feature-store work**, particularly the notes on the 160
games (2.9%) missing `game_stats` and the xG-availability cutoff between
2019 and 2021. Seasons 2025/2026 aren't in that file yet — their equivalent
detail (verification results, plus two new findings: a `game_stats`
retroactive-enrichment behavior and a season-2025 `shot_events` gap) is in
`CLAUDE.md`'s 2026-08-23 Status entry and `docs/SCHEMA_DRAFT.md`'s
`shot_events` section.

`docs/SCHEMA_DRAFT.md` documents the SQLite schema (14 curated tables +
sync/raw metadata), derived from real fixtures in `fixtures/liiga/` and the
endpoint catalog in `src/hockey_edge/ingest/liiga/endpoints.py`. The ingest
machinery is DDL (`ingest/db.py`), rate-limited resumable fetch-and-cache
(`ingest/raw_cache.py`), parsers (`ingest/liiga/parsers.py`), and a backfill CLI
(`ingest/liiga/backfill.py`).

**One thing to know before touching the schema:** liiga.fi's `game_id` is not
unique across seasons — regular-season and preseason ids are small per-season
counters that get reused. `games` is keyed on the composite `(game_id, season)`
and so is every per-game table. Keying on `game_id` alone silently corrupts data
(it did, mid-backfill); the root-cause writeup is in `docs/BACKFILL_RESULTS.md`.

Still open on step 1: seasons before 2015 are untested, and the HC Blues
`game_stats` gap in 2015/2016 has no root cause yet. (The `PLAYOUT`/
`QUALIFICATIONS` `serie` strings, previously unconfirmed, were confirmed via
season 2025 — see `docs/SCHEMA_DRAFT.md` design principle 3. Season 2027
added another, `PITSITURNAUS`, a preseason tournament: treat the `serie`
vocabulary as open-ended, not a closed set.)

**Build-order step 2 (snapshot capture job) is deployed and capturing both
lineups and odds.** `src/hockey_edge/snapshot/` has live fixture discovery
(`fixtures.py`), a sleep-safe capture-window scheduler (a game's
opening/mid/closing windows are tracked per `(season, game_id, kind,
window)`, not inferred from timestamp proximity — verified against a
simulated missed-tick scenario), an `api_usage` table with a monthly
ceiling, and `job.py --dry-run` / `--once` modes. Odds and lineups carry
**separate** window status (`capture_windows.kind`): they succeed
independently, and a shared status once let a successful odds poll hide
windows from lineup capture. It is registered as a
Windows scheduled task (2026-09-02), running every 15 minutes 11:00–23:00
local under `pythonw.exe`, so a tick raises no console window and
`logs/snapshot_job.log` is the record of a run — see
`docs/snapshot_job_task_scheduler.xml`, which carries the working
registration command and why the window starts at 11:00.

**Lineup capture is live** (`snapshot/lineups.py`, previously stubbed).
liiga.fi publishes a confirmed 22-player lineup per team roughly half an
hour before puck drop — `line` is non-null for exactly 20 skaters + 2
goalies at T-30, versus null for everyone at T-9 days. The starting goalie
is inferred from `line == 1` (there is no literal "starter" field); that
inference scored 14/14 against real results on the 2026-09-01 slate, while
the competing array-order candidate scored 7/14. Every stored starter is
still marked as an inference (`starter_source`/`starter_confidence`) and
`scripts/verify_starters.py` re-scores it against completed games so drift
is monitored rather than assumed.

**Odds capture is live too, since 2026-09-17** (`OddsPapiProvider`, replacing
`NullOddsProvider`). The earlier blocker — a live call finding no price data
on `/odds-by-tournaments` — turned out to be the off-season board being too
early, not a limit of the endpoint: in-season, one request returns every
posted fixture with full prices for one bookmaker. The job polls **pinnacle
(primary) + bet365** per due tick, parses the 3-way regulation 1X2 and the
2-way moneyline incl. OT, and keeps the full payload either way so parsing
can be redone without refetching. A fixture is tied to its liiga.fi game via
the curated `snapshot/odds/oddspapi_teams.json` (OddsPapi team names are not
unique) plus an exact home/away/start match — never a guess; an unmatched
fixture is stored unresolved with a WARNING. An odds window counts as
captured only for a game the primary book actually priced. Plan, per-phase
record and the bookmaker comparison: **`docs/ODDS_PLAN.md`**.

```
python -m hockey_edge.snapshot.job --once       # normal pass
python -m hockey_edge.snapshot.job --dry-run    # zero HTTP requests, reports what's due + projected requests

python scripts/verify_starters.py               # read-only: score captured starters vs real results
python scripts/lineup_probe.py --date 2026-09-01 --label T-30   # read-only ad-hoc lineup probe
```

**Tests**: `python -m unittest discover -s tests` (stdlib unittest, no extra
dependency — there is no pytest in `requirements.txt`). They run against real
saved API responses in `fixtures/oddspapi/` rather than invented payloads, and
make zero HTTP requests. Add new tests as `tests/test_*.py`.

**A separate re-sync mechanism now exists for historical data**:
`data/hockey.db`'s completed-game data isn't immutable on liiga.fi's side —
it can change after original fetch, in either direction (not just
enrichment). `src/hockey_edge/ingest/liiga/resync.py` re-fetches recently-
completed games and reparses them only if doing so wouldn't leave any
curated table with fewer rows than it already has (the "no-shrink guard").
`backfill.py --force` also gained an `--endpoints` flag to scope a targeted
recovery to just the endpoint(s) that need it. See **`docs/RESYNC.md`**
(mechanism) and **`docs/RECOVERY_BACKLOG.md`** (two deferred recovery runs,
ready-to-execute commands, not run yet) for detail.

**Keeping the live season current — `scripts/nightly_sync.py`, needs to run
daily while the season is on.** Nothing else puts completed 2026-27 games
into `hockey.db`: the snapshot job writes only to `snapshots.db`, and
`backfill.py` was built as a one-time-per-historical-season pass. This
wrapper runs the season backfill (season-level endpoints force-refreshed,
per-game restricted to games that have ended) followed by `resync.py` over
the last 7 days, logging to `logs/nightly_sync.log`:

```
python scripts/nightly_sync.py              # season 2027, 7-day resync window
python scripts/nightly_sync.py --dry-run    # resync pass only, zero HTTP requests
```

It is **not** scheduled by this repo — registering it is a manual step. See
`docs/RESYNC.md`'s nightly-sync section for why both passes are required and
why the season-level force is load-bearing.

Run a season backfill directly (from repo root, with the venv active — see Setup):

```
python -m hockey_edge.ingest.liiga.backfill --season 2024
python -m hockey_edge.ingest.liiga.backfill --season 2027 --only-ended   # live season
```

Use `--only-ended` for a **live** season: without it, per-game endpoints are
fetched for games that haven't been played, and liiga.fi's pre-game shell
gets cached as `sync_state` `success` — which this backfill then skips
forever, so the real post-game data never arrives by that path.

Safe to re-run — already-fetched entities are skipped, not refetched, so
re-running a completed season is a no-op costing zero HTTP requests. Add
`--max-games N` to cap per-game endpoint fetches (useful for a quick check;
omit for a real backfill) or `--force` to refetch everything regardless of
cache. A full season from cold is 500–625 games (varies by season — playoff
length, preseason friendlies) x 3 endpoints x a 1.5s polite delay — budget
40–55 minutes. Add `--endpoints game_stats` (or a comma-separated subset of
`games_by_season,standings,game_detail,game_stats,shotmap`) to scope
`--force` to just the endpoint(s) that actually need refetching — see
`docs/RECOVERY_BACKLOG.md` before running a targeted `game_detail` recovery
this way; the guarded alternative (`resync.py`) is usually the safer choice
for that specific endpoint.

## Setup

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

`pip install -e .` (added 2026-08-23, `pyproject.toml`) installs
`src/hockey_edge` as an editable package, so `import hockey_edge` and
`python -m hockey_edge.<module>` work directly from an activated venv, from
any working directory — no `PYTHONPATH` needed. `requirements.txt` is still
the source of truth for runtime dependencies (`pyproject.toml` declares no
`[project.dependencies]` itself); run both `pip install` steps on setup.

Put `ODDSPAPI_KEY=...` in an untracked `.env` at the repo root (never commit it) —
**required**: `job.py` polls OddsPapi for real on every due tick, and
`OddsPapiProvider` raises without it. `scripts/oddspapi_probe.py` needs it too.
See the Status section above for the snapshot job commands.
