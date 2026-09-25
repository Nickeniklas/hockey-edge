# hockey-edge

A personal hockey prediction tool for Liiga and the NHL that compares its own
win probabilities with bookmaker odds.

Liiga is the point: almost nobody models it, and its odds are softer. The hard
part is the data. liiga.fi's API is undocumented, reuses game ids across
seasons, and changes completed games after the fact, sometimes by removing
data. Lineups and odds only exist shortly before puck drop, so if they are not
captured then, they are gone for good.

## What it does

1. Ingests Liiga history from liiga.fi's JSON API into SQLite, caching every
   raw response so parsing can be redone without refetching.
2. Keeps the live season current with a nightly sync and a guarded re-sync of
   recently completed games.
3. Captures confirmed lineups and bookmaker odds (Pinnacle, bet365 via
   OddsPapi) before each game, on a 15-minute schedule.
4. Planned, not built yet: a feature store, an Elo + LightGBM model, and an
   immutable prediction log compared against the odds. NHL ingest comes after
   those.

## Requirements

- Python 3.11+
- An [OddsPapi](https://oddspapi.io) API key for odds capture (free tier)
- Windows Task Scheduler if you want the capture job to run on its own

## Install

```
git clone <repo-url> hockey-edge
cd hockey-edge
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

`requirements.txt` holds the dependencies; `pip install -e .` makes
`hockey_edge` importable from anywhere in the venv.

Create an untracked `.env` in the repo root:

```
ODDSPAPI_KEY=your-key
```

The snapshot job fails without it. Ingest and re-sync don't need it.

## Usage

Run from the repo root with the venv active.

```
python -m hockey_edge.ingest.liiga.backfill --season 2024                 # ingest one historical season
python -m hockey_edge.ingest.liiga.backfill --season 2027 --only-ended    # ingest the live season, played games only
python scripts/nightly_sync.py                                            # bring the live season up to date
python scripts/nightly_sync.py --dry-run                                  # show what the re-sync would check, no HTTP
python -m hockey_edge.ingest.liiga.resync --days 7                        # re-fetch games completed in the last 7 days
python -m hockey_edge.snapshot.job --once                                 # one lineup/odds capture pass
python -m hockey_edge.snapshot.job --dry-run                              # show due capture windows, no HTTP
python scripts/verify_starters.py                                         # score inferred starting goalies against results
python -m unittest discover -s tests                                      # tests, no HTTP
```

Backfill is resumable: a finished season re-runs as a no-op. Always use
`--only-ended` on a live season; without it, an unplayed game's pre-game shell
gets cached as final.

The capture job and the nightly sync are meant to run as Windows scheduled
tasks. The task definition and registration command are in
[docs/snapshot_job_task_scheduler.xml](docs/snapshot_job_task_scheduler.xml),
and the nightly sync's in [docs/RESYNC.md](docs/RESYNC.md).

## How it works

**Ingest.** `ingest/liiga/endpoints.py` is the only place liiga.fi endpoints
are defined. `ingest/raw_cache.py` fetches politely (rate-limited, normal user
agent), writes each response to `data/raw/`, and records progress in
`sync_state` so a run can resume. `ingest/liiga/parsers.py` turns the cached
JSON into curated tables in `data/hockey.db` (schema in `ingest/db.py`), keyed
on `(game_id, season)` because liiga.fi reuses game ids.
`ingest/liiga/backfill.py` drives a season end to end.

**Re-sync.** liiga.fi's data for a finished game can change later, in either
direction. `ingest/liiga/resync.py` re-fetches finished games and only
reparses a response when no curated table would lose rows (the no-shrink
guard). The raw response is always kept. `scripts/nightly_sync.py` wraps a
live-season backfill and a 7-day re-sync.

**Snapshot capture.** `snapshot/job.py` discovers upcoming games
(`snapshot/fixtures.py`), tracks opening/mid/closing capture windows per game
in `data/snapshots.db` (`snapshot/storage.py`), and fires whichever are due,
including ones missed while the machine slept. `snapshot/lineups.py` stores
the lineup liiga.fi confirms about 30 minutes before puck drop.
`snapshot/odds/` polls OddsPapi behind a swappable provider interface and
matches each fixture to its liiga.fi game through a curated team map. All
snapshot rows are append-only and timestamped, so features can only use what
was known before puck drop.

## Project structure

```
src/hockey_edge/ingest/     liiga.fi ingest, raw cache, re-sync
src/hockey_edge/snapshot/   pre-game lineup and odds capture
scripts/                    nightly sync, checks, and one-off recovery tools
fixtures/                   real saved API responses, used by the tests
docs/                       plan, data and model contracts, run records
data/                       SQLite databases and the raw cache (gitignored)
```

Start with [CLAUDE.md](CLAUDE.md) (stack, rules, current status),
[docs/PLAN.md](docs/PLAN.md), [docs/DATA_PIPELINE.md](docs/DATA_PIPELINE.md)
and [docs/MODEL.md](docs/MODEL.md).

## Limitations

- Only Liiga is ingested; NHL is deferred.
- No model or predictions yet. The data layer and capture job are what exist.
- liiga.fi's historical data has real gaps: some games miss penalties, puck
  control has values only from 2023, and xG is absent before 2020 and
  incomplete until 2023. See [docs/DATA_PIPELINE.md](docs/DATA_PIPELINE.md)
  and [docs/BACKFILL_RESULTS.md](docs/BACKFILL_RESULTS.md).
- The capture job only runs while the desktop is awake or wakeable; a missed
  closing window can't be recovered.
