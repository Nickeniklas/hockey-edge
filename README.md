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

Early scaffolding — no end-to-end pipeline yet, but two pieces are real and runnable.
Liiga endpoint discovery (build-order step 1) is done: 16 confirmed endpoints with real
fixtures in `fixtures/liiga/`, catalog in `src/hockey_edge/ingest/liiga/endpoints.py`.
Build-order step 2 (snapshot capture job) has a working skeleton in
`src/hockey_edge/snapshot/`: a swappable odds-provider interface, a working OddsPapi
implementation (billing confirmed per-HTTP-request, Liiga tournamentId=134), a stubbed
Veikkaus fallback, append-only SQLite storage, and a manually-runnable job
(`python -m hockey_edge.snapshot.job`) with failure alerting. Lineup capture and odds
parsing are still stubbed pending live Liiga data. See the Status section in
`CLAUDE.md` for exactly what's done vs. pending.

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
