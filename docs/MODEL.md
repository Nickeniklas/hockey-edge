# MODEL — contract

## Targets

- **NHL v1:** binary P(home win), moneyline incl. OT/SO — matches how NHL books price it.
- **Liiga v1:** regulation 1X2 (home/draw/away in regulation), three-class — matches
  European book pricing. Alternative two-stage design (P(reaches OT) × P(home | OT))
  noted as an option; decide during implementation, but the market being priced
  three-way is the deciding constraint.
- **Second market (deferred):** totals (over/under goals) — often softer than moneyline.
- Skip puck line / handicaps initially.

## Models

1. **Baseline: Elo-style or ridge team rating.** Calibrated, stable, works on tiny
   data, handles early season gracefully. This ships first and is the reference every
   later model must beat.
2. **LightGBM/XGBoost** on the feature store (see DATA_PIPELINE.md feature families).
3. **Blend** of the two. Rationale: GBM alone tends to be overconfident and drifts
   early in seasons; the blend keeps calibration.

Compute is trivial (CPU, seconds–minutes). No deep learning in v1.

## Metrics — the non-negotiable part

- Primary: **log loss** and **calibration curves**. Accuracy is reported but never
  optimized for. A 58%-accurate well-calibrated model beats a 60% overconfident one,
  because the product is probabilities compared to odds and miscalibration produces
  fake edges.
- Benchmark: **log loss vs. odds-implied probabilities, vig removed**, from the last
  odds snapshot before puck drop (≈ closing line; Pinnacle preferred where available).
  - Beat the closing line → genuine betting edge.
  - Close to it on Liiga while beating **opening** lines → already a sellable
    prediction product, even without a strict betting edge.
  - Neither → the model isn't there yet; the tool is still useful personally.

## Validation rules (hard invariants)

- **Walk-forward only:** train on seasons 1..n, predict season n+1. Never shuffle
  games across time. No k-fold on pooled games.
- **Pre-puck-drop information only.** Every feature must be derivable from rows whose
  `captured_at` / game date precedes the predicted game. Starting-goalie and injury
  features are the classic leak path (post-game box scores dressed as pre-game info).
- Early-season handling: regress team stats to league means; Liiga (15 teams,
  60 games) needs harder regression and wider uncertainty than NHL.
- Every trained model version is tagged; predictions record the model version.

## Prediction log (the product's spine)

Immutable table: (game_id, model_version, predicted_at, probabilities, odds snapshot
used, edge if any). Never edited, never deleted. This is simultaneously the
validation harness and — if the project ever goes public — the entire marketing
(publish predictions timestamped before games, grade publicly, show ROI and
calibration; can't be faked retroactively). Plan calls for running the model
publicly-loggable for 2–3 months before ever charging anyone.
