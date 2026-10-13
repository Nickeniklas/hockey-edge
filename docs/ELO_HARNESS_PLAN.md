# ELO_HARNESS_PLAN — Elo baseline and walk-forward harness (build step 5)

Design chat, 2026-10-12.
Contract: `docs/MODEL.md` (targets, metrics, validation rules). Input: `data/features.db`
from feature store phase F1 (`docs/FEATURE_STORE_PLAN.md`). Items marked
**[DECISION: Niklas]** are the user's call. Stop at every "STOP" line and report before
going on.

**Status: planned, not started.**

Order: **E0 → E1 → E2 → E3**, then back to the design chat before F2.

---

## What step 5 is for

Two deliverables:
- **The Elo baseline.** The reference every later model must beat (MODEL.md).
- **The walk-forward harness.** Folds, metrics, storage and report. This is the
  reusable part: step 6 (LightGBM and the blend) plugs into the same folds and the same
  report, so the comparison is like for like.

Elo uses only final results, so its 2027 predictions can be recomputed at any later
date with nothing lost. Step 5 depends on no data that disappears. The live, immutable
prediction log stays in step 7.

### In scope
- Elo rating engine for Liiga, regulation 1X2 target.
- Ordered-logit link from rating gap to three probabilities.
- Two simple baselines through the same folds.
- Walk-forward tuning, test seasons 2018–2026, plus 2027 as the live out-of-sample fold.
- Metrics, calibration tables, slices.
- Comparison against Pinnacle closing lines on 2027 games.

### Out of scope
- LightGBM, the blend, F2/F3 (step 6).
- Live predictions, the prediction log, edges, ROI or any staking simulation (step 7).
- Opening-line comparison (needs an "earliest row" benchmark `benchmark_odds` doesn't
  hold).
- PLAYOFFS evaluation (open decision 3). Predictions are stored, not reported.
- NHL.
- Any change to `hockey.db`, `snapshots.db`, ingest or the snapshot job.

---

## Design answers

**1. Target and probability mapping.** Target is the spine's regulation result (H/D/A).
Elo produces one number per game, the pre-game gap

    x = (R_home + HFA − R_away) / 400

A separate **ordered-logit link** turns `x` into three probabilities:
- `P(A) = σ(c1 − βx)`
- `P(A or D) = σ(c2 − βx)`, so `P(D) = σ(c2 − βx) − σ(c1 − βx)` and
  `P(H) = 1 − σ(c2 − βx)`
- `c1 < c2`, `β > 0`. Fitted on tuning seasons only, frozen for the test season.

Why: draws are most likely when teams are even, and the ordered link gives that shape
without extra machinery. Calibration becomes its own fitted, testable piece, and the
rating engine never has to model draws beyond its update score.
Alternatives: Davidson-style draw-Elo (draw parameter inside the rating model, which
ties calibration to rating dynamics); the two-stage `P(reaches OT) × P(H | decided in
regulation)` from MODEL.md (same information for 1X2, more parameters).
**[DECISION: Niklas]** ordered logit (recommended).

**2. Rating updates.**
- Expected score `E = 1 / (1 + 10^(−(R_home + HFA − R_away) / 400))`.
- Actual score `S`: regulation H = 1, regulation A = 0. A D game (OT or shootout) scores
  `0.5 ± s_ot` toward the OT/SO winner. `s_ot` is tuned over {0, 0.15}; 0 means the
  rating learns only from regulation. 2022:49294 (real regulation draw) scores 0.5.
- `Δ = K × M × (S − E)`. Home gets `+Δ`, away `−Δ`.
- `M` is the margin multiplier, a tuned on/off switch. Off: `M = 1`. On:
  `M = ln(m + 1) × 2.2 / (2.2 + 0.001 × gap_w)`, with `m = min(|final margin|, 3)` and
  `gap_w` the winner's pre-game Elo advantage (the usual autocorrelation correction).
  The cap is there because empty-net goals inflate margins. D games use `M = 1`.
- Same K for every `feeds_ratings` game, regular season and playoffs alike. Only
  RUNKOSARJA (`in_eval_sample`) games are scored.

**3. Point-in-time rule: predict a whole UTC date, then update.** Elo is online, and it
must see exactly what `history_before` would let a feature see (UTC-date cut, see
FEATURE_STORE_PLAN "F1 result"). So the engine works one UTC date at a time:
- Predict every game on date D from ratings as of the end of D−1.
- Then apply all of D's updates. No team plays twice on one date (checked in E0), so
  same-day updates are independent and their order doesn't matter.
- The engine reads `game_spine` once, in order. It does not call `history_before` per
  game. A test proves the two agree: for every game, the set of games already applied
  before its prediction equals `history_before(game)` restricted to `feeds_ratings`.

**4. Season boundaries.**
- 2015: every team starts at 1500. 2015 is burn-in only, never tuned on or scored.
- At each new season, returning teams move a fraction `r` of the way toward the mean of
  returning teams' ratings. `r` is tuned.
- Teams that left (Blues after 2016) drop out. The mean is over returning teams only, so
  a departure doesn't shift anyone.
- New teams enter by the cold-start rule below.

**5. Promoted-team cold start** (MODEL.md asks for an explicit rule, not just a wider
prior). For a team-season with `is_first_liiga_season = 1`:
- **Entry rating = mean of returning teams − Δ.** Δ comes only from earlier
  first-season cases in the history the pass has already seen: their mean first-season
  goal difference per game, converted to Elo points with the slope from regressing final
  goal margin on pre-game Elo gap over all earlier rating games. (Earlier cases: KooKoo
  2016, Jukurit 2017, K-Espoo 2025. FEATURE_STORE_PLAN design answer 4 puts their gap at
  about −0.30 goals per game, spread −0.18 to −0.50.)
- **No earlier case** (KooKoo 2016): enter at the lowest returning team's rating. This
  is an a-priori rule, so no look-ahead.
- **Faster learning:** the new team's K is `K × m` for its first `N` games. Defaults
  `m = 2`, `N = 15`, not tuned, because three cases can't support tuning. Its opponents
  update with normal K, so these games aren't zero-sum. Accepted.
- Report Jokerit 2027 as its own slice, plus a sensitivity run of the same games with
  `Δ = 0, m = 1`.

**6. Walk-forward folds.**
- Seasons 2015–2027. Burn-in 2015. Test seasons 2018–2026, then 2027 (in progress, ended
  games only).
- Fold for test season `n`: tuning seasons are 2016 … n−1.
- For each grid config, run Elo once over all history. Because predictions are online,
  one pass gives every game's pre-game gap. For each fold:
  1. Fit the link on tuning-season eval games for that config. Score log loss on the
     same games. Three link parameters on 1,000+ games makes the in-sample fit
     negligible.
  2. Pick the config with the lowest tuning log loss.
  3. Predict test season `n` from that config's pass with the frozen link.
- Results of season `n` update ratings within season `n` as games are played. That is
  allowed: it is exactly what live use does.
- **Fold isolation:** nothing from seasons ≥ n may change fold n's chosen config or
  link. A poison test enforces it (E2).

Starting grid. Widen any dimension where a fold's choice lands on an edge, and record
edge hits per fold.
- K: 4, 6, 8, 10, 14, 18
- HFA: 0, 20, 40, 60, 80 **[removed from the grid 2026-10-13, fixed at 0: see Decisions]**
- r: 0.2, 0.35, 0.5, 0.7 **[0.85 added in E2 after the 2018 fold chose 0.7]**
- margin multiplier: off, on
- s_ot: 0, 0.15

That is 480 configs. A pass is about 7,000 games, so the whole grid should take minutes
in plain Python. If it takes much longer, report before optimising.

**7. Baselines.** Same folds, same storage, same report.
- **B0 base rates:** H/D/A frequencies of tuning-season eval games. The floor.
- **B1 goal-difference rating:** per team, season-to-date goal difference per game,
  shrunk toward last season's value pulled halfway to 0, weight `n / (n + 10)`. Fixed,
  not tuned. The home-minus-away gap goes through the same ordered link, fitted per
  fold. It answers whether Elo beats a naive form table. Built with the same date-batch
  rule.

**8. Metrics and slices** (`validation/metrics.py`, pure functions).
- **Primary: log loss** (natural log, mean per game).
- **Calibration:** per class (H, D, A), bins of width 0.05 merged until each holds ≥ 30
  games; observed rate vs mean predicted, game count, and ECE. Calibration-in-the-large
  per season: mean predicted vs observed rate, per class. Tables, not plots (plots come
  with the dashboard in step 7).
- Secondary: multi-class Brier, RPS (H > D > A is ordered), accuracy. Accuracy is
  reported, never optimised.
- **Comparisons are always paired:** per-game log-loss difference, mean ± standard
  error, per season and pooled. No model comparison is stated without its SE.
- Slices: per season; pooled 2018–2026; early season (either team has played fewer than
  10 RUNKOSARJA games that season); first-Liiga-season teams; 2027.

**9. Market benchmark** (`validation/market.py`, the only harness module that reads
odds).
- Rows: `benchmark_odds` with `minutes_before_start ≤ 60` (FEATURE_STORE_PLAN F1
  findings). Pinnacle is the benchmark. bet365-fallback rows are reported separately,
  never pooled with Pinnacle.
- Same games for all: Elo (2027 fold), B0, market (vig removed as stored). Paired
  difference ± SE.
- Also report the per-game SD of the Elo − market difference, and the games needed for a
  0.01 difference to reach 2 SE: `n ≈ (2 × SD / 0.01)²`. This puts a number on how long
  the thin benchmark (open decision 2) stays inconclusive.
- No edge, ROI or staking simulation. About 50 games can't support it.

Rough expectation to read the results against: base-rate log loss for regulation 1X2 is
likely near 1.07. Hockey outcomes are noisy, so Elo's gain over B0 will probably be in
the hundredths, and the closing line will usually beat a results-only Elo. A small gain
is the expected result, not a failure.

**10. Code layout and storage.**
- `src/hockey_edge/models/elo.py`: params, date-batched pass, carry-over, cold start.
  Pure. No DB writes.
- `src/hockey_edge/models/link.py`: ordered-logit fit and predict.
- `src/hockey_edge/models/baselines.py`: B0, B1.
- `src/hockey_edge/validation/`: `folds.py`, `metrics.py`, `harness.py`, `market.py`,
  `report.py`, `run.py` (CLI).
- Inputs: `game_spine`, `season_format`, `team_season` from `features.db`.
  `benchmark_odds` only in `market.py`.
- Not used by Elo v1: `features_home` (Elo has its own HFA; team offsets are a LightGBM
  input) and `features_schedule`. A rest-days adjustment to Elo is a later option.
- Output: `data/backtest.db`, gitignored and rebuildable, like `features.db`.
  **This is not the prediction log.** Backtest rows are reproducible derived output. The
  immutable log in step 7 holds predictions made before puck drop in real time. Separate
  file, separate table names.
- Runs are append-only: each run gets a new `run_id`, and no row is updated.
- Tables:
  - `backtest_run`: `run_id`, `created_at`, `models`, `model_version`, `code_version`,
    `features_build` (the `build_meta` code version and source fingerprint),
    `config_json`.
  - `backtest_fold`: `run_id`, `model`, `test_season`, `tuning_seasons`,
    `chosen_params_json`, `link_params_json`, `grid_edges_json`.
  - `backtest_prediction`: `run_id`, `model`, `season`, `game_id`, `test_season`,
    `p_home`, `p_draw`, `p_away`, `gap`, `rating_home_pre`, `rating_away_pre`
    (Elo only), `result`. Key `(run_id, model, season, game_id)`.
- The report computes metrics from stored predictions. No metrics table in v1.
- `model_version`: `elo-1` plus a short hash of code version and params. The final
  config (tuned on 2016–2026) is the one step 7 logs under.

**11. Dependencies.** Tests stay stdlib `unittest`, zero HTTP.
**Decided 2026-10-12:** add `numpy` and `scipy` to `requirements.txt` for the link fit
(step 6 needs numpy anyway).

**12. Known traps.**
- Draws come only from the spine target (`finished_type`). Never rederive them.
- 2021:480 is damaged at source, but its 0–1 final is what Elo and the margin use. Fine.
- 2022:49294 is a real draw and feeds ratings. 2022:49298 is dropped in E0.
- 2020 has no playoffs, and 2020–2022 have uneven games per team. Elo needs no change.
  Slices must not assume 60 games.
- Unended games (postponements) neither update nor get scored.
- `game_id` is reused across seasons. Key everything `(season, game_id)`.

---

## Phase E0 — F1 follow-up and read-only checks

**1. Drop 2022:49298 from `feeds_ratings`** (FEATURE_STORE_PLAN F1 findings).
- One hard-coded exclusion list in `features/spine.py`, each entry with its reason text:
  `(2022, 49298)`: sudden-death tiebreak after a two-game aggregate series, 35 minutes.
- New `game_spine` column `ratings_exclusion_reason`, non-NULL only for listed games.
- Check that 49298 is in `fixtures/features/mini.sql`. Add it through
  `scripts/make_feature_fixture.py` if not.
- Tests: 49298 excluded with its reason; 49294 still feeds ratings; rebuild row counts
  unchanged apart from the flag.

**2. Read-only checks** (report only, no tables):
- a. **Team id continuity**, 2015–2027: any club with more than one id, or one id whose
  name changes. A split id would silently restart a team's rating.
- b. **No team plays twice on one UTC date** in `feeds_ratings` games. The date-batch
  rule depends on it.
- c. Every ended `feeds_ratings` game has a result and a final score. List unended games
  with a past start.
- d. Per season: eval games, rating games, H/D/A rates of eval games. Flag any season
  whose draw rate is more than 3 percentage points from the pooled rate.
- e. 2027: ended RUNKOSARJA games in the spine against the schedule, with the
  opening-night games checked specifically.
- f. `benchmark_odds` rows with `minutes_before_start ≤ 60`, by book.
- g. Add `numpy` and `scipy` to `requirements.txt` and install them (decided).

**STOP after E0.** Report the checks. If (a) or (b) fails, the plan changes before E1.

---

## Phase E1 — Elo engine, link, metrics

- `models/elo.py` per design answers 2–5. Params as a dataclass (`K`, `HFA`, `r`,
  `margin`, `s_ot`, cold-start `m`, `N`).
- `models/link.py` per design answer 1.
- `validation/metrics.py` per design answer 8.
- Smoke run (sanity only, not stored as a result): defaults `K = 8, HFA = 40, r = 0.35`,
  margin off, `s_ot = 0`, over 2015–2027; link fitted on 2016–2017 and frozen; per-season
  log loss against B0.

Tests (`tests/models/`, `tests/validation/`, on the mini fixture):
- Update math: a hand-computed two-game example; zero-sum without cold start; `S` for H,
  A, and an OT/SO winner; margin cap.
- Date batching: two games on one date don't see each other's results. The equivalence
  test against `history_before` (design answer 3).
- Truncation and poison: a game's prediction is identical when every game on or after
  its UTC date is deleted, or given absurd results.
- Carry-over: returning-team mean preserved; a leaving team doesn't move it.
- Cold start: the fixture starts in 2022, so K-Espoo 2025 has no earlier case and takes
  the lowest-returning-rating path, and Jokerit 2027 has one earlier case and takes the Δ
  path. K multiplier applies for `N` games, then stops.
- Link: probabilities sum to 1 and stay in (0, 1); P(H) rises and P(A) falls with the
  gap; recovers known parameters from synthetic data.
- Metrics: log loss, Brier and RPS on hand-computed cases; paired SE on a tiny example;
  the calibration bin-merge rule.

**STOP after E1.** Report: the smoke table; Spearman correlation between season-end Elo
and final RUNKOSARJA points per season (points from the spine: 3 regulation win, 2 OT/SO
win, 1 OT/SO loss); Jokerit's 2027 rating path so far.

---

## Phase E2 — Walk-forward harness

- `validation/folds.py`: folds from a config (burn-in, first test season, available
  seasons), so tests can run on the fixture's 2022/2025/2027 with a two-config grid.
- `validation/harness.py`: one pass per config, per-fold tuning and link fit, test-season
  predictions; B0 and B1 through the same folds; writes `backtest.db`.
- `validation/report.py`: the tables from design answer 8, to stdout and to
  `docs/ELO_RESULTS.md` (generated tables, plus a short written summary at the top).
- `validation/run.py`: the CLI.

Commands:

```
python -m hockey_edge.features.build
python -m hockey_edge.validation.run --models elo,base_rate,gd_rating
python -m hockey_edge.validation.report --run latest
```

Tests:
- **Fold isolation:** on the fixture, fold n's chosen params and link are identical when
  every game in seasons ≥ n gets absurd results.
- Each stored prediction's `test_season` matches its season, and comes from that fold's
  config.
- Every ended eval game in a test season is predicted exactly once.
- Idempotence: same config and same features build give identical predictions under a
  new `run_id`.
- Static odds isolation: `elo.py`, `link.py`, `baselines.py`, `folds.py` and
  `harness.py` contain no reference to `benchmark_odds`, `odds_snapshots` or
  `snapshots.`.
- The report runs on fixture output.

**STOP after E2.** Report: per-season and pooled table (Elo vs B0 vs B1, paired
difference ± SE); chosen params per fold and any grid-edge hits; pooled calibration
tables; early-season and first-season slices; Jokerit sensitivity; runtime.
**If Elo doesn't beat B0 on pooled 2018–2026 log loss by more than 2 SE, stop there and
say so.** No further tuning without the design chat.

---

## Phase E3 — Market benchmark, version tag, docs

- `validation/market.py` per design answer 9. Its section is appended to
  `docs/ELO_RESULTS.md`.
- Tag the final config as `elo-1` (tuned on 2016–2026). Params go into
  `docs/ELO_RESULTS.md` and into a config constant in `models/` that step 7 can import.
- Docs: a new `CLAUDE.md` status entry (what was built, commands, test count, results
  headline). Point the FEATURE_STORE_PLAN status line at this doc.
  **Don't touch `docs/PROJECT_CONTEXT.md`** (the design chat owns it).

Tests:
- Benchmark filter: a row at 60 minutes is in, a row at 61 is out.
- Pinnacle and bet365 rows are never pooled.
- Market log loss on a saved real row, by hand.

**STOP after E3.** Hand-off: report back to the design chat with
`docs/ELO_RESULTS.md`. Next is F2 for step 6, with the harness ready to score LightGBM
and the blend.

---

## Decisions for Niklas (collected)

- Link: ordered logit (recommended), Davidson draw-Elo, or two-stage.
- ~~`numpy` + `scipy` in `requirements.txt`~~ (decided: yes).
- **HFA is fixed at 0 and removed from the grid (decided 2026-10-13, after E2).** The
  link's cutpoints carry the home edge, so tuning can't identify HFA: every fold chose
  the grid's lowest value, and the tuning loss fell by under 0.001 from +80 to −80 with
  no minimum. The rerun without HFA (120 configs) gave identical predictions in every
  fold. Details in `docs/ELO_RESULTS.md`.
- Still open from PROJECT_CONTEXT, not decided here: the thin odds benchmark (E3 gives
  the number of games needed) and PLAYOFFS as an evaluation slice.
