# FEATURE_STORE_PLAN — Liiga feature store (build step 4)

Design chat, 2026-09-24; updated 2026-09-25 after the historical recovery.
Contract: `docs/DATA_PIPELINE.md` Layer 3 and `docs/MODEL.md`. Data rules:
`docs/DATA_PIPELINE.md` → "Known data gaps". Items marked **[DECISION: Niklas]** are
the user's call. Stop at every "STOP" line and report before going on.

Order: **F0 (audits) → F1 (Elo-ready slice) → F2 → F3.** NHL stays deferred.

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
  starter. Expect ~1 mismatch in 72.

**2. xG cutoff.** Corsi/shot-share features for all seasons as the primary family; xG
features next to them from 2021 as NULL before, plus `xg_available`. No imputation.
The Elo slice needs neither. **[DECISION: Niklas, later]** in-house location xG from
`shot_events` coordinates would remove the cutoff; after F2 if ever.

**3. Which games count.**
- **Evaluation / training targets:** RUNKOSARJA only for v1.
- **History feeding features and ratings:** RUNKOSARJA + PLAYOFFS +
  QUALIFICATIONS/PLAYOUT.
- **Excluded everywhere:** PRACTICE, PITSITURNAUS, and any game with a non-Liiga
  opponent.
- Format changes live in a small `season_format` table (teams, games per team, playoff
  format, relegation rule), used by motivation and shrinkage — not a generic feature
  column. Season 2020 has no playoffs.
- **[DECISION: Niklas]** PLAYOFFS as a separate evaluation slice later.

**4. Early-season shrinkage.** Prior = last season's team value pulled halfway
(tunable) toward the league mean. In-season weight `n / (n + k)`, `k` per family
(larger for special teams and goalie save%, measured in opportunities/shots). Tune `k`
and the pull-back inside training folds only. Elo gets between-season carry-over
toward the mean, also tuned.
Promoted/new teams: measure every team's first Liiga season in the data (the query
decides; candidates KooKoo, Jukurit, K-Espoo) against league mean. Jokerit's prior =
league mean minus that gap, small `k`. Three cases is thin — report the spread.

**5. Storage and rebuild.** Separate `data/features.db`, with `hockey.db` and
`snapshots.db` ATTACHed read-only. Built from the **curated** tables, never raw.
**Full rebuild** every time for v1, with a `build_meta` row (code version, source
fingerprint = max `fetched_at`/`captured_at`, row counts).
Point-in-time proof:
- By construction: all history comes through one helper,
  `history_before(conn, season, game_id)` → only `ended=1` games with
  `start_utc <` this game's `start_utc` (strict). Feature code never queries base
  tables directly; a test enforces it.
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

---

## Phase F0 — Read-only audits (no new tables)

Output: `docs/FEATURE_AUDIT.md`, one query plus a short finding per item.
1. **Regulation result.** Target is 1X2 in regulation. Find a reliable end-type field
   (OT/SO) on `games`. Do **not** derive it by counting goal events (known surplus).
   Verify on a handful of known OT and SO games.
2. **Season-2027 completeness.** Every ended game has detail, stats and shotmap rows.
   Glance at `2701323` (team-period goals 2 vs final 3 on first fetch) — should have
   self-corrected.
3. `games.phase` value inventory per season, including PITSITURNAUS.
4. Foreign-opponent detection rule (likely: team id not in that season's RUNKOSARJA
   set).
5. First-Liiga-season list per team (feeds the promoted-team prior).
6. **Strength state for "EV" features.** Does anything give EV splits directly? If not,
   choose between penalty timing and `shot_events` on-ice counts. RESYNC 4a saw on-ice
   counts corrected +1 on refetch in one game, so check a sample of stored values.
   **Updated 2026-09-25: do not plan a `shotmap` refetch** (no value guard for it, and
   liiga.fi degrades history). If stored on-ice counts are off, derive strength state
   from penalty timing.
7. **Power-play derivation.** Confirm minors (`penalty_minutes = 2`) or penalty timing
   reproduce sensible PP opportunities per game in seasons where team PP counts are
   trustworthy (2015–2024 July copy), so the same derivation can be used for
   2025/2026 where they are stripped.
8. xG availability per season on the current DB.

**STOP after F0. Findings may change F1/F2.**

---

## Phase F1 — Spine and Elo-ready slice

Package `src/hockey_edge/features/`:
- `db.py`: create `data/features.db`, ATTACH sources read-only, DDL, `build_meta`.
- `history.py`: `history_before(...)`, the only door to past games.
- `spine.py` → `game_spine` keyed `(game_id, season)`: `start_utc`, home/away team,
  phase, `is_liiga_competitive`, `in_eval_sample`, regulation result (H/D/A), end
  type, season-format reference. Goals from `games.home_goals`/`away_goals`, never
  `game_team_period_stats`.
- `season_format.py` → `season_format`, hand-curated and verified against `games`
  counts (15/60 historically, 16/60 for 2025–26, 17/64 from 2027; 2020 no playoffs;
  2027 direct relegation of bottom three).
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
- Spine exclusions (PRACTICE, PITSITURNAUS, foreign) and the regulation target on
  known OT/SO games from real fixtures.
- Benchmark: vig removal on a saved real odds row; "closing = last before start",
  including a row captured after start being ignored.
- Rebuild idempotence.

Hand-off: F1 is enough to start build step 5 (Elo + walk-forward harness).

---

## Phase F2 — Goalie and shot quality (for LightGBM, step 6)

- `starters.py`: shared actual-starter logic (`verify_starters.py` then imports it)
  plus `resolve_starter`.
- Family 1 `goalie.py`: rolling save% for the resolved starter (shots-based windows),
  shrunk toward league mean; team expected-goalie fallback. GSAx-style columns only if
  F0 shows per-goalie xG can be built.
- Family 2 `shot_quality.py`: rolling Corsi (and shots) for/against per 60, windows
  10/25/40, EV per F0 item 6. Player-level corsi is intact for all seasons; don't use
  team-level period stats for 2025/2026. xG columns from 2021, NULL before, plus
  `xg_available`.
- Tests: truncation/poison on both; resolver precedence; fallback path; skew report on
  real 2027 rows; starter logic unchanged vs `verify_starters.py`'s current output.

## Phase F3 — Remaining families

- Family 3 special teams: PP/PK rates and draws/takes from penalty events per F0 item
  7, hard shrinkage. The seven zero-penalty competitive games are skipped as missing.
- Family 6 roster availability: missing share of last-N ice time, from `game_rosters`
  history plus the pre-game snapshot lineup.
- Family 7 motivation: playoff-line and relegation-line distance via `season_format`.
  From 2027, relegation pressure is season-long.
- Same test pattern.

---

## Fixtures

- `scripts/make_feature_fixture.py` copies a slice of real rows from `hockey.db` and
  `snapshots.db` (e.g. two seasons, all teams, a few known OT/SO games, one PLAYOFFS
  series, one foreign friendly, one 2025/2026 game with stripped team stats, and the
  2027 games with snapshots) into `fixtures/features/mini.sql` (text dump,
  git-diffable). Tests load it into an in-memory DB.

## Doc wording to fix during F0/F1

- DATA_PIPELINE Layer 3 says "rebuilt from raw" — should be "from the curated layer".
- DATA_PIPELINE family 1 says "confirmed starter" — should be "resolved starter"
  (`line==1` inference, ~98.6%).
- DATA_PIPELINE family 4 tags the whole family "(NHL only)" — only travel/TZ is.
- SCHEMA_DRAFT still has the "xG pre-~2015" guess in places; BACKFILL_RESULTS has the
  real cutoff.

## Decisions for Niklas (collected)

- Thin odds benchmark: accept, or a historical-odds session.
- PLAYOFFS as a separate evaluation slice.
- In-house location xG: later or never.
