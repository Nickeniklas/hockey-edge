# Elo baseline results

Build step 5, phase E2 (`docs/ELO_HARNESS_PLAN.md`). Walk-forward: each test season is
predicted with an Elo config, a link and base rates fitted only on the seasons before it.
Regular-season games only. Rebuild with:

```
python -m hockey_edge.features.build
python -m hockey_edge.validation.run --models elo,base_rate,gd_rating
python -m hockey_edge.validation.report --run latest
```

## Summary (run 2, 2026-10-13)

- **Elo beats base rates.** Pooled 2018–2026 log loss 1.0359 against 1.0675, a paired
  difference of −0.0316 ± 0.0039 over 4,075 games (about 8 SE). It is ahead in every
  season, by as little as −0.0017 ± 0.0103 in 2025.
- **Elo barely beats the goal-difference rating:** −0.0028 ± 0.0014 pooled, 2 SE. A naive
  form table gets most of the way there.
- **2027 says nothing yet.** 99 games; Elo is 0.0089 ± 0.0192 behind base rates.
- **The gain is smaller early in a season:** −0.0146 ± 0.0074 while a team has under 10
  games, −0.0352 ± 0.0045 after.
- **The cold-start rule shows no measurable gain.** Against a flat entry it is
  +0.0072 ± 0.0088 on K-Espoo's 60 games in 2025 and −0.0513 ± 0.0392 on Jokerit's 13 in
  2027. Games with a first-season team are the hardest slice for every model.
- **Folds chose** K 6–14, r 0.5 (0.7 for 2018), the margin multiplier on in every fold,
  and `s_ot` 0.15 in four folds, 0 in six.
- **HFA is fixed at 0 and is not in the grid (decided 2026-10-13).** The link's cutpoints
  carry the home edge, so HFA only reaches the loss through the size of the updates and
  tuning can't identify it. Run 1 had HFA 0, 20, 40, 60, 80 in the grid: every fold chose
  0, the lowest value, and along HFA the tuning loss fell by 0.0006–0.0008 from +80 to −80
  with no minimum (checked on the 2018, 2022 and 2027 folds). Run 2, without HFA, chose the
  same K, r, margin and `s_ot` in every fold and its 18,216 stored predictions are
  identical to run 1's.
- **r was widened once**, adding 0.85, after the 2018 fold chose 0.7 at the top of the
  plan's grid. It still chooses 0.7, now inside the grid. No K choice is on an edge.
- Calibration: ECE 0.020 for home wins, 0.019 for away wins, 0.006 for draws. The
  predicted draw probability varies little (bin means 0.18 to 0.25).
- Runtime: 8 s for 120 configs, ten folds and all models (84 s for run 1's 600).

<!-- Generated below by python -m hockey_edge.validation.report. Edits below this line are overwritten. -->

Run 2, 2026-10-13T19:02:32 UTC. Model version `elo-1-8a5b85cf`, harness `e2`, features `f1.1` (hockey.db as of 2026-10-12). 4174 regular-season games in 10 test seasons.

## Log loss by test season

Lower is better. A negative difference favours Elo.

|  | Games | Elo | Base rates | Goal-diff | Elo − base (± SE) | Elo − goal-diff (± SE) |
|---|---|---|---|---|---|---|
| 2018 | 450 | 1.0428 | 1.0664 | 1.0460 | -0.0236 ± 0.0105 | -0.0032 ± 0.0039 |
| 2019 | 450 | 1.0243 | 1.0649 | 1.0273 | -0.0406 ± 0.0114 | -0.0029 ± 0.0045 |
| 2020 | 443 | 1.0167 | 1.0764 | 1.0134 | -0.0597 ± 0.0137 | +0.0033 ± 0.0043 |
| 2021 | 424 | 1.0359 | 1.0664 | 1.0354 | -0.0305 ± 0.0145 | +0.0005 ± 0.0046 |
| 2022 | 448 | 1.0483 | 1.0731 | 1.0532 | -0.0249 ± 0.0134 | -0.0049 ± 0.0037 |
| 2023 | 450 | 1.0410 | 1.0623 | 1.0432 | -0.0212 ± 0.0109 | -0.0022 ± 0.0032 |
| 2024 | 450 | 1.0259 | 1.0747 | 1.0353 | -0.0489 ± 0.0114 | -0.0094 ± 0.0045 |
| 2025 | 480 | 1.0657 | 1.0674 | 1.0614 | -0.0017 ± 0.0103 | +0.0043 ± 0.0040 |
| 2026 | 480 | 1.0209 | 1.0564 | 1.0316 | -0.0355 ± 0.0097 | -0.0108 ± 0.0050 |
| **Pooled 2018–2026** | 4075 | 1.0359 | 1.0675 | 1.0387 | -0.0316 ± 0.0039 | -0.0028 ± 0.0014 |
| 2027 (in progress) | 99 | 1.0538 | 1.0450 | 1.0475 | +0.0089 ± 0.0192 | +0.0063 ± 0.0151 |

## Other metrics

Accuracy is reported, never optimised. ECE is the mean over the three classes.

| Games from | Model | Games | Log loss | Brier | RPS | Accuracy | ECE |
|---|---|---|---|---|---|---|---|
| Pooled 2018–2026 | Elo | 4075 | 1.0359 | 0.6226 | 0.2227 | 0.482 | 0.0150 |
| Pooled 2018–2026 | Base rates | 4075 | 1.0675 | 0.6463 | 0.2344 | 0.434 | 0.0037 |
| Pooled 2018–2026 | Goal-diff rating | 4075 | 1.0387 | 0.6246 | 0.2237 | 0.477 | 0.0131 |
| Pooled 2018–2026 | Elo, flat entry | 4075 | 1.0358 | 0.6226 | 0.2227 | 0.482 | 0.0155 |
| 2027 (in progress) | Elo | 99 | 1.0538 | 0.6378 | 0.2403 | 0.404 | 0.0390 |
| 2027 (in progress) | Base rates | 99 | 1.0450 | 0.6320 | 0.2376 | 0.465 | 0.0270 |
| 2027 (in progress) | Goal-diff rating | 99 | 1.0475 | 0.6330 | 0.2379 | 0.455 | 0.0512 |
| 2027 (in progress) | Elo, flat entry | 99 | 1.0606 | 0.6428 | 0.2427 | 0.414 | 0.0409 |

## What each fold chose

Fitted on the tuning seasons only. An edge is a chosen value at the end of its grid.

| Test season | Tuned on | K | HFA | r | Margin | s_ot | c1 | c2 | β | Tuning log loss | Edges |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2018 | 2016–2017 | 8 | 0.0 | 0.7 | on | 0.15 | -0.759 | 0.274 | 3.967 | 1.0455 | none |
| 2019 | 2016–2018 | 6 | 0.0 | 0.5 | on | 0.15 | -0.798 | 0.246 | 4.020 | 1.0438 | none |
| 2020 | 2016–2019 | 6 | 0.0 | 0.5 | on | 0.15 | -0.814 | 0.234 | 4.237 | 1.0388 | none |
| 2021 | 2016–2020 | 14 | 0.0 | 0.5 | on | 0.0 | -0.763 | 0.271 | 2.821 | 1.0335 | none |
| 2022 | 2016–2021 | 10 | 0.0 | 0.5 | on | 0.0 | -0.737 | 0.282 | 3.259 | 1.0338 | none |
| 2023 | 2016–2022 | 14 | 0.0 | 0.5 | on | 0.0 | -0.717 | 0.296 | 2.693 | 1.0356 | none |
| 2024 | 2016–2023 | 10 | 0.0 | 0.5 | on | 0.0 | -0.728 | 0.283 | 3.126 | 1.0362 | none |
| 2025 | 2016–2024 | 10 | 0.0 | 0.5 | on | 0.0 | -0.733 | 0.288 | 3.196 | 1.0350 | none |
| 2026 | 2016–2025 | 10 | 0.0 | 0.5 | on | 0.0 | -0.729 | 0.285 | 3.082 | 1.0382 | none |
| 2027 | 2016–2026 | 10 | 0.0 | 0.5 | on | 0.15 | -0.734 | 0.274 | 3.080 | 1.0365 | none |

Grid: k [4, 6, 8, 10, 14, 18]; margin [False, True]; r [0.2, 0.35, 0.5, 0.7, 0.85]; s_ot [0.0, 0.15]. Not tuned: hfa 0.0.

Baselines, same folds:

| Test season | Base rates H / D / A | Goal-diff c1 | c2 | β (per goal/game) | Tuning log loss |
|---|---|---|---|---|---|
| 2018 | 0.431 / 0.240 / 0.329 | -0.752 | 0.275 | 0.613 | 1.0491 |
| 2019 | 0.439 / 0.241 / 0.320 | -0.793 | 0.244 | 0.568 | 1.0477 |
| 2020 | 0.441 / 0.241 / 0.318 | -0.808 | 0.233 | 0.602 | 1.0425 |
| 2021 | 0.432 / 0.236 / 0.331 | -0.755 | 0.274 | 0.637 | 1.0363 |
| 2022 | 0.431 / 0.234 / 0.336 | -0.732 | 0.284 | 0.602 | 1.0360 |
| 2023 | 0.428 / 0.233 / 0.339 | -0.711 | 0.298 | 0.587 | 1.0384 |
| 2024 | 0.431 / 0.233 / 0.336 | -0.723 | 0.284 | 0.576 | 1.0390 |
| 2025 | 0.431 / 0.234 / 0.335 | -0.727 | 0.288 | 0.584 | 1.0386 |
| 2026 | 0.431 / 0.234 / 0.335 | -0.725 | 0.285 | 0.567 | 1.0410 |
| 2027 | 0.433 / 0.232 / 0.334 | -0.728 | 0.274 | 0.566 | 1.0401 |

## Calibration, Elo, pooled 2018–2026

Bins of width 0.05, merged until each holds 30 games.

**Home win** (ECE 0.0202)

| Predicted | Games | Mean predicted | Observed |
|---|---|---|---|
| 0.00–0.20 | 53 | 0.173 | 0.132 |
| 0.20–0.25 | 137 | 0.229 | 0.190 |
| 0.25–0.30 | 306 | 0.276 | 0.284 |
| 0.30–0.35 | 501 | 0.326 | 0.339 |
| 0.35–0.40 | 626 | 0.376 | 0.396 |
| 0.40–0.45 | 696 | 0.425 | 0.431 |
| 0.45–0.50 | 600 | 0.474 | 0.448 |
| 0.50–0.55 | 496 | 0.522 | 0.530 |
| 0.55–0.60 | 311 | 0.575 | 0.598 |
| 0.60–0.65 | 213 | 0.621 | 0.545 |
| 0.65–0.70 | 88 | 0.671 | 0.648 |
| 0.70–1.00 | 48 | 0.734 | 0.812 |

**Draw** (ECE 0.0056)

| Predicted | Games | Mean predicted | Observed |
|---|---|---|---|
| 0.00–0.20 | 268 | 0.181 | 0.187 |
| 0.20–0.25 | 3197 | 0.237 | 0.231 |
| 0.25–1.00 | 610 | 0.253 | 0.251 |

**Away win** (ECE 0.0190)

| Predicted | Games | Mean predicted | Observed |
|---|---|---|---|
| 0.00–0.15 | 95 | 0.128 | 0.116 |
| 0.15–0.20 | 325 | 0.180 | 0.231 |
| 0.20–0.25 | 506 | 0.227 | 0.213 |
| 0.25–0.30 | 766 | 0.275 | 0.268 |
| 0.30–0.35 | 761 | 0.325 | 0.348 |
| 0.35–0.40 | 630 | 0.374 | 0.354 |
| 0.40–0.45 | 457 | 0.424 | 0.435 |
| 0.45–0.50 | 267 | 0.474 | 0.472 |
| 0.50–0.55 | 159 | 0.521 | 0.560 |
| 0.55–0.60 | 68 | 0.572 | 0.618 |
| 0.60–1.00 | 41 | 0.643 | 0.585 |

Mean predicted against observed rate, per season:

| Season | Games | Home pred / obs | Draw pred / obs | Away pred / obs |
|---|---|---|---|---|
| 2018 | 450 | 0.433 / 0.453 | 0.239 / 0.244 | 0.328 / 0.302 |
| 2019 | 450 | 0.441 / 0.449 | 0.239 / 0.238 | 0.320 / 0.313 |
| 2020 | 443 | 0.445 / 0.397 | 0.234 / 0.219 | 0.321 / 0.384 |
| 2021 | 424 | 0.435 / 0.422 | 0.233 / 0.219 | 0.332 / 0.358 |
| 2022 | 448 | 0.434 / 0.408 | 0.233 / 0.230 | 0.333 / 0.362 |
| 2023 | 450 | 0.427 / 0.456 | 0.235 / 0.231 | 0.338 / 0.313 |
| 2024 | 450 | 0.433 / 0.427 | 0.232 / 0.247 | 0.335 / 0.327 |
| 2025 | 480 | 0.429 / 0.433 | 0.238 / 0.231 | 0.333 / 0.335 |
| 2026 | 480 | 0.429 / 0.456 | 0.237 / 0.217 | 0.335 / 0.327 |
| 2027 | 99 | 0.422 / 0.465 | 0.239 / 0.192 | 0.340 / 0.343 |

## Slices

|  | Games | Elo | Base rates | Goal-diff | Elo − base (± SE) | Elo − goal-diff (± SE) |
|---|---|---|---|---|---|---|
| Early season, pooled (a team under 10 games) | 715 | 1.0496 | 1.0642 | 1.0522 | -0.0146 ± 0.0074 | -0.0026 ± 0.0037 |
| Rest of season, pooled | 3360 | 1.0329 | 1.0682 | 1.0358 | -0.0352 ± 0.0045 | -0.0029 ± 0.0015 |
| Games with a first-Liiga-season team, all test seasons | 73 | 1.1328 | 1.1285 | 1.1293 | +0.0044 ± 0.0250 | +0.0035 ± 0.0100 |
| 2027 (in progress) | 99 | 1.0538 | 1.0450 | 1.0475 | +0.0089 ± 0.0192 | +0.0063 ± 0.0151 |

First-season teams, and the same games with a flat entry (enter at the returning-team mean, normal K). A negative difference favours the cold-start rule.

|  | Games | Elo | Flat entry | Base rates | Elo − flat entry (± SE) | Elo − base (± SE) |
|---|---|---|---|---|---|---|
| K-Espoo 2025, its games | 60 | 1.1749 | 1.1676 | 1.1433 | +0.0072 ± 0.0088 | +0.0315 ± 0.0261 |
| 2025, all games | 480 | 1.0657 | 1.0649 | 1.0674 | +0.0008 ± 0.0011 | -0.0017 ± 0.0103 |
| Jokerit 2027, its games | 13 | 0.9388 | 0.9901 | 1.0599 | -0.0513 ± 0.0392 | -0.1211 ± 0.0635 |
| 2027, all games | 99 | 1.0538 | 1.0606 | 1.0450 | -0.0068 ± 0.0053 | +0.0089 ± 0.0192 |
