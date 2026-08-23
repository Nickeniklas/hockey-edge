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

**Build-order step 1 (Liiga historical ingest) covers seasons 2015–2026.**
The original 10-season backfill (2015–2024, 5,517 games) ran first;
seasons 2025 and 2026 were added in a later pass (2026-08-23), bringing
`data/hockey.db` to 12 seasons and 6,736 games total, all with full
per-game endpoints, no sampling. Season 2027 (the in-progress 2026-27
season) is deliberately **not** backfilled — it belongs to the future
snapshot job, not historical ingest.

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
season 2025 — see `docs/SCHEMA_DRAFT.md` design principle 3.)

Build-order step 2 (snapshot capture job) has a working skeleton in
`src/hockey_edge/snapshot/`: a swappable odds-provider interface, a working OddsPapi
implementation (billing confirmed per-HTTP-request, Liiga tournamentId=134), a stubbed
Veikkaus fallback, append-only SQLite storage, and a manually-runnable job
(`python -m hockey_edge.snapshot.job`) with failure alerting. Lineup capture and odds
parsing are still stubbed pending live Liiga data. See the Status section in
`CLAUDE.md` for exactly what's done vs. pending.

Run a season backfill (from repo root, with the venv active — see Setup):

```
python -m hockey_edge.ingest.liiga.backfill --season 2024
```

Safe to re-run — already-fetched entities are skipped, not refetched, so
re-running a completed season is a no-op costing zero HTTP requests. Add
`--max-games N` to cap per-game endpoint fetches (useful for a quick check;
omit for a real backfill) or `--force` to refetch everything regardless of
cache. A full season from cold is 500–625 games (varies by season — playoff
length, preseason friendlies) x 3 endpoints x a 1.5s polite delay — budget
40–55 minutes.

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

Put `ODDSPAPI_KEY=...` in an untracked `.env` at the repo root (never commit it) to
run the OddsPapi probe/snapshot job, e.g. (from the repo root, venv active):

```
python -m hockey_edge.snapshot.job
```
