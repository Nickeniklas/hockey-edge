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

Liiga historical ingest (build-order step 1) is now runnable end to end for one
season at a time. `docs/SCHEMA_DRAFT.md` documents the SQLite schema (14 curated
tables + sync/raw metadata), derived from real fixtures in `fixtures/liiga/` and
the endpoint catalog in `src/hockey_edge/ingest/liiga/endpoints.py`. The ingest
machinery — DDL (`ingest/db.py`), rate-limited resumable fetch-and-cache
(`ingest/raw_cache.py`), parsers (`ingest/liiga/parsers.py`), and a backfill CLI
(`ingest/liiga/backfill.py`) — is smoke-tested against real season=2024 data (see
`docs/SCHEMA_DRAFT.md`'s "Smoke test results" for row counts and two bugs found
and fixed along the way). The full ~10-season backfill hasn't run yet — that's
next, after schema review.

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

Safe to re-run — already-fetched entities are skipped, not refetched. Add
`--max-games N` to cap per-game endpoint fetches (useful for a quick check;
omit for a real backfill) or `--force` to refetch everything regardless of cache.

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
