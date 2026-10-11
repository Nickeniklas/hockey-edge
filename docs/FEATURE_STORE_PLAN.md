# FEATURE_STORE_PLAN — Liiga feature store (build step 4)

Design chat, 2026-09-24; updated 2026-09-25 after the historical recovery;
**updated 2026-10-11 after F0** (`docs/FEATURE_AUDIT.md`).
Contract: `docs/DATA_PIPELINE.md` Layer 3 and `docs/MODEL.md`. Data rules:
`docs/DATA_PIPELINE.md` → "Known data gaps". Items marked **[DECISION: Niklas]** are
the user's call. Stop at every "STOP" line and report before going on.

**Status: F0 done 2026-10-11. Next: F1.** For step 4, this file and
`docs/FEATURE_AUDIT.md` are newer than `docs/PROJECT_CONTEXT.md`.

Order: **F0 (audits, done) → F1 (Elo-ready slice) → F2 → F3.** NHL stays deferred.

---

## Decisions taken after F0 (2026-10-11)

**D1. Penalty team is corrected in `features.db`, not in `hockey.db`.**
liiga.fi lists penalties under the wrong team's array in most 2017–2022 and 2024
games, per game, not per season (FEATURE_AUDIT item 7). It also moves penalties
between the arrays on refetch (RESYNC 4a). So the stored `team_id` is not trusted
and the team is derived from the roster on every rebuild.
- Built by the feature build as a derived table, `penalty_events_resolved`. This is
  a derived layer, so hard rule 4 holds. **No change to ingest code, `parsers.py`,
  `db.py` DDL, backfill, resync or nightly sync.** `hockey.db` keeps storing exactly
  what liiga.fi sent.
- Built in F3, as its first item (spec below). Nothing before F3 reads penalty teams.

**D2. The 2025 Pelicans–Jokerit qualification series (5 games) does not feed
ratings or features.** Jokerit had no Liiga rating that season, so the results say
little about Pelicans. The foreign-opponent id rule stays one rule with no
exceptions. The 2025 Pelicans–Jukurit PLAYOUT series (both Liiga teams) stays in.

**D3. Shot features are built from `shot_events`, not from liiga.fi's corsi
totals.** Player-level corsi steps up ~10% in 2023 while `shot_events` attempts stay
flat (FEATURE_AUDIT item 6), so liiga.fi's counting changed. Counting attempts from
`shot_events` gives one rule for every season, and `strength_type` gives the
even-strength split directly. Built in F2.

---

## Design answers

**1. Goalie train/serve skew.** Separate *who starts* from *how good the goalie is*.
The quality number (rolling save% from games before puck drop) is leak-free either
way. Only the identity differs.
- One resolver, `resolve_starter(season, game_id)` → `(player_id, source)`, source in
  `snapshot_inferred` / `actual_postgame` / `unknown`. Use the pre-game snapshot when
  one exists (live, and every 2027 game the job captured). Otherwise the actual starter
  (historical training only).
- Move the actual-starter logic (period-1 shots > 0, early-sub flag) out of
  `scripts/verify_starters.py` into a library module both use.
- No snapshot for a live game (missed window): fall back to a team-level "expected
  goalie" value, the starts-weighted mix of the goalies used in the last N games.
  Compute the same fallback for every historical game so it can be scored.
- `starter_confidence` and `source` are **not model inputs** — constant on history and
  constant the other way on live, so the model would learn era. Keep them as audit
  columns and evaluation slices.
- Skew report: for 2027 games with both, goalie features with inferred vs actual
  starter. Expect ~1–2% mismatches.

**2. xG cutoff.** Shot-attempt features for all seasons are the primary family.
Team xG features from **game-level** xG (`games.*_expected_goals`) from 2021, NULL
before, plus `xg_available`. Player-level xG exists only from 2025 and is not used
for v1. No imputation. The Elo slice needs neither. **[DECISION: Niklas, later]**
in-house location xG from `shot_events` coordinates would remove the cutoff; after F2
if ever.

**3. Which games count.**
- `is_liiga_competitive` is decided by **phase alone**: RUNKOSARJA, PLAYOFFS,
  PLAYOUT, QUALIFICATIONS. PRACTICE and PITSITURNAUS are excluded everywhere.
- `has_non_liiga_team`: either team id is not in that season's RUNKOSARJA team set.
  Ids, never names (foreign club names are not stable).
- **Feeds ratings and features:** `is_liiga_competitive AND NOT has_non_liiga_team`.
  This drops friendlies and, per D2, the 2025 Jokerit qualification series.
- **Evaluation / training targets:** RUNKOSARJA only for v1.
- Format changes live in a small `season_format` table, used by motivation and
  shrinkage — not a generic feature column. Season 2020 has no playoffs.
- **[DECISION: Niklas]** PLAYOFFS as a separate evaluation slice later.

**4. Early-season shrinkage.** Prior = last season's team value pulled halfway
(tunable) toward the league mean. In-season weight `n / (n + k)`, `k` per family
(larger for special teams and goalie save%, measured in opportunities/shots). Tune `k`
and the pull-back inside training folds only. Elo gets between-season carry-over
toward the mean, also tuned.
Promoted/new teams (FEATURE_AUDIT item 5): KooKoo 2016, Jukurit 2017, K-Espoo 2025
averaged about −0.30 goals per game in their first season (spread −0.18 to −0.50).
Jokerit 2027's prior = league mean minus that gap, small `k`. Three cases is thin;
the spread is reported next to it. Thirteen teams are present from 2015, so their
first season is unknown and they are not first-season cases.

**5. Storage and rebuild.** Separate `data/features.db`, with `hockey.db` and
`snapshots.db` ATTACHed read-only. Built from the **curated** tables, never raw.
**Full rebuild** every time for v1, with a `build_meta` row (code version, source
fingerprint = max `fetched_at`/`captured_at`, row counts).
Point-in-time proof:
- By construction: all history comes through one helper,
  `history_before(conn, season, game_id)` → only `ended=1` games with
  `start_utc <` this game's `start_utc` (strict). Feature code never queries base
  tables directly; a test enforces it. Derived per-game tables in `features.db`
  (like `penalty_events_resolved`) are read through the same helper.
- Truncation test: features from the full fixture DB equal features from a copy with
  every game at or after that game's start deleted.
- Poison test: absurd post-`t` outcomes must not change features at `t`.
- Accepted approximation, stated in the docs: historical features use the stored
  (July 2026 or corrected) stats, not what liiga.fi showed at the time.

**6. Minimum first slice.** Elo needs only results, home/away, phase and season
boundaries. F1 = spine, target, home advantage, rest, odds benchmark. Goalie and shot
quality in F2 for LightGBM. **Odds stay benchmark-only**, never features.
**[DECISION: Niklas]** odds exist only from 2026-09-17, so the market comparison stays
thin for months. Elo can be walk-forward validated on 2018–2026 against simple
baselines right away.

**7. Known traps from F0** (details in `docs/FEATURE_AUDIT.md`):
- `game_penalty_events.team_id` is unreliable (D1).
- `shot_events.shooting_team_id` is the bare number (`933686567`); every other table
  uses `number:slug`. Normalise before any join, or it silently matches nothing.
- `shot_events` on-ice counts change convention in 2025 (skaters only → including
  the goalie). Do not use them.
- Player-level corsi is not comparable across 2023 (D3).
- Games per team is not constant in 2020–2022 (as low as 50 in 2021).

---

## Phase F0 — Read-only audits (done 2026-10-11)

Output: `docs/FEATURE_AUDIT.md`. Its findings are folded into the decisions above and
the phases below. The DATA_PIPELINE wording fixes listed in the old plan were made in
F0; the SCHEMA_DRAFT xG one no longer applied.

---

## Phase F1 — Spine and Elo-ready slice

Package `src/hockey_edge/features/`:
- `db.py`: create `data/features.db`, ATTACH sources read-only, DDL, `build_meta`.
- `history.py`: `history_before(...)`, the only door to past games.
- `spine.py` → `game_spine` keyed `(game_id, season)`: `start_utc`, home/away team,
  phase, `is_liiga_competitive`, `has_non_liiga_team`, `feeds_ratings`,
  `in_eval_sample`, regulation result (H/D/A), end type, season-format reference.
  Goals from `games.home_goals`/`away_goals`, never `game_team_period_stats`.
  - **Regulation result from `games.finished_type`:**
    `ENDED_DURING_REGULAR_GAME_TIME` → H or A from the final score;
    `ENDED_DURING_EXTENDED_GAME_TIME` or `ENDED_DURING_WINNING_SHOT_COMPETITION`
    → D. Never derived from goal-event counts.
  - A regular-time game with a tied final (only known case: 2022:49294 PLAYOFFS,
    HPK–Lukko 2–2) → D, with an `end_type_anomaly` flag. It is outside the
    evaluation sample but feeds ratings.
- `season_format.py` → `season_format`, hand-curated and verified against `games`
  counts (15/60 historically, 16/60 for 2025–26, 17/64 from 2027; 2020 no playoffs;
  2027 direct relegation of bottom three). **Games per team is counted per
  team-season from `games`, not assumed**, because 2020–2022 are uneven. Includes a
  per-team-season `is_first_liiga_season` (KooKoo 2016, Jukurit 2017, K-Espoo 2025,
  Jokerit 2027) for the promoted-team prior.
- Family 5 `home.py`: league home-advantage rate from prior games only, team offset
  with shrinkage.
- Family 4 (Liiga subset) `schedule.py`: rest days, games in last 6 days. No
  travel/TZ.
- `benchmark.py` → `benchmark_odds` (not a feature table): last parsed Pinnacle 1X2
  before `start_utc`, vig removed (method recorded), bet365 fallback with the book
  recorded. The model build never selects from it; a test enforces that.
- `build.py`: `python -m hockey_edge.features.build` does a full rebuild.

Tests (`tests/features/`, stdlib unittest):
- Truncation and poison tests on `home`, `schedule` and the spine target.
- Spine exclusions (PRACTICE, PITSITURNAUS, foreign friendlies, the 2025 Jokerit
  qualification series) and the PLAYOUT series kept.
- Regulation target on known OT and SO games from real fixtures, plus 2022:49294.
- Benchmark: vig removal on a saved real odds row; "closing = last before start",
  including a row captured after start being ignored.
- Rebuild idempotence.

**STOP after F1.** Hand-off: F1 is enough to start build step 5 (Elo + walk-forward
harness).

---

## Phase F2 — Goalie and shot quality (for LightGBM, step 6)

**First, a short check (report before building):** the shot result types in
`shot_events` per season. If blocked shots are included, the attempt features are
Corsi. If not, they are Fenwick (unblocked attempts) and are named that way.
Confirm attempts per game stay flat across 2023 by type.

- `starters.py`: shared actual-starter logic (`verify_starters.py` then imports it)
  plus `resolve_starter`.
- Family 1 `goalie.py`: rolling save% for the resolved starter (shots-based windows),
  shrunk toward league mean; team expected-goalie fallback. Per-goalie xG is not
  stored. An optional GSAx-style column uses the opponent's game xG, from 2021 only,
  and only for games where one goalie played the whole game.
- Family 2 `shot_quality.py`, **from `shot_events` only (D3)**:
  - Attempts for/against and attempt share, all situations and even strength
    (`strength_type = 'EvenStrengthShot'`), rolling windows 10/25/40.
  - Rates **per game**, not per 60 of even-strength time. Even-strength minutes would
    need penalty timing with corrected teams, which only exists after F3. Per-60 EV
    rates are a later option once `penalty_events_resolved` exists.
  - Normalise `shooting_team_id` to the `number:slug` form before joining.
  - Games without `shot_events` (the 79-game 2025 gap and five single games) are
    missing, not zero: they are skipped in rolling windows.
  - Team xG for/against from game-level xG, from 2021, NULL before, plus
    `xg_available`.
  - Do not use liiga.fi's corsi columns, team-level period stats, or on-ice counts.
- Tests: truncation/poison on both; resolver precedence; fallback path; skew report on
  real 2027 rows; starter logic unchanged vs `verify_starters.py`'s current output;
  team-id normalisation; a gap game treated as missing.

## Phase F3 — Remaining families

**First item: `penalty_events_resolved` (D1).** One row per competitive penalty
event, built from `game_penalty_events` + `game_rosters`:
- **Player penalties:** team = the penalised player's roster team in that game.
- **Team/bench penalties (`player_id = 0`):** take the game's direction. If that
  game's player penalties are stored on the opposite team, swap; if they are stored
  on the right team, keep.
- **`team_source`** column: `roster`, `game_direction` or `unresolved` (player not in
  the roster, or a game with only bench penalties).
- Also keep the stored `team_id` and a `was_swapped` flag, for auditing.
- **Build report:** swapped share per season (should reproduce FEATURE_AUDIT item 7's
  table), the number of `unresolved` rows, and games with mixed direction (the audit
  saw eleven competitive games with one stray row).
- Tests on real fixtures: 2018 game 1 (swapped) and 2023 game 1 (correct), including
  their bench penalties.

Then:
- Family 3 special teams: PP opportunities with the **coincident-adjusted count**
  (opponent's 2- and 5-minute penalties with non-zero duration, minus those starting
  at the same second as one of the team's own), on `penalty_events_resolved`. **Use
  the derived count for every season**, including 2015–2024 where a stored count
  exists, so there is one rule across seasons. PP/PK rates and draws/takes, hard
  shrinkage. The seven zero-penalty competitive games are skipped as missing.
- Family 6 roster availability: missing share of last-N ice time, from `game_rosters`
  history plus the pre-game snapshot lineup.
- Family 7 motivation: playoff-line and relegation-line distance via `season_format`.
  From 2027, relegation pressure is season-long.
- Same test pattern.

---

## Fixtures

- `scripts/make_feature_fixture.py` copies a slice of real rows from `hockey.db` and
  `snapshots.db` into `fixtures/features/mini.sql` (text dump, git-diffable). Tests
  load it into an in-memory DB. Include: two seasons, all teams, a few known OT/SO
  games, one PLAYOFFS series, 2022:49294, one foreign friendly, the 2025 Jokerit
  qualification series and the 2025 PLAYOUT series, one 2025/2026 game with stripped
  team stats, one game from the 2025 `shot_events` gap, 2018 game 1 and 2023 game 1
  (penalty direction), and the 2027 games with snapshots.

## Decisions for Niklas (collected)

- Thin odds benchmark: accept, or a historical-odds session.
- PLAYOFFS as a separate evaluation slice.
- In-house location xG: later or never.
