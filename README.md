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

**Build-order step 1 (Liiga historical ingest) is complete for seasons
2015–2024.** The full 10-season backfill has run — 5,517 games with all
per-game endpoints, no sampling — and `data/hockey.db` holds the result.
`docs/BACKFILL_RESULTS.md` has the per-season row counts, sanity checks, and
the known data gaps; **read it before trusting the DB for feature-store work**,
particularly the notes on the 160 games (2.9%) missing `game_stats` and the
xG-availability cutoff between 2019 and 2021.

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

Still open on step 1: seasons before 2015 are untested, the `PLAYOUT`/
`QUALIFICATIONS` phase strings are unconfirmed (zero games in either across all
ten seasons), and the HC Blues `game_stats` gap in 2015/2016 has no root cause
yet.

Build-order step 2 (snapshot capture job) has a working skeleton in
`src/hockey_edge/snapshot/`: a swappable odds-provider interface, a working OddsPapi
implementation (billing confirmed per-HTTP-request, Liiga tournamentId=134), a stubbed
Veikkaus fallback, append-only SQLite storage, and a manually-runnable job
(`python -m hockey_edge.snapshot.job`) with failure alerting. Lineup capture and odds
parsing are still stubbed pending live Liiga data. See the Status section in
`CLAUDE.md` for exactly what's done vs. pending.

Run a season backfill (from repo root, with `src` on `PYTHONPATH`):

```
PYTHONPATH=src python -m hockey_edge.ingest.liiga.backfill --season 2024
```

Safe to re-run — already-fetched entities are skipped, not refetched, so
re-running a completed season is a no-op costing zero HTTP requests. Add
`--max-games N` to cap per-game endpoint fetches (useful for a quick check;
omit for a real backfill) or `--force` to refetch everything regardless of
cache. A full season from cold is roughly 550 games x 3 endpoints x a 1.5s
polite delay — budget ~40 minutes.

## Setup

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Put `ODDSPAPI_KEY=...` in an untracked `.env` at the repo root (never commit it) to
run the OddsPapi probe/snapshot job. There's no packaging config yet, so
`src/hockey_edge` isn't installed as a package — run its modules with `src` on
`PYTHONPATH`, e.g. (from the repo root):

```
PYTHONPATH=src python -m hockey_edge.snapshot.job
```
