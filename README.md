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

Early scaffolding only — no runnable pipeline yet. Currently on build-order step 1
(Liiga endpoint discovery); see the Status section in `CLAUDE.md` for exactly what's
done vs. pending.

## Setup

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```
