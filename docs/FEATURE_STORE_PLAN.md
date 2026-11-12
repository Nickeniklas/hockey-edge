# FEATURE_STORE_PLAN — Liiga feature store (build step 4)

Design chat, 2026-09-24; updated 2026-09-25 after the historical recovery;
**updated 2026-10-11 after F0** (`docs/FEATURE_AUDIT.md`); **updated 2026-10-12
after F1**; F2 pre-check updated 2026-11-06 (Corsi/Fenwick rule, blocked-shot team
check); F3 split into F3a–F3d 2026-11-07.
Contract: `docs/DATA_PIPELINE.md` Layer 3 and `docs/MODEL.md`. Data rules:
`docs/DATA_PIPELINE.md` → "Known data gaps". Items marked **[DECISION: Niklas]** are
the user's call. Stop at every "STOP" line and report before going on.

**Status: F0 done 2026-10-11, F1 done 2026-10-12 (commits `c47137c`, `bec8b87`).
Build step 5 (Elo + walk-forward harness) is done 2026-10-13: plan
`docs/ELO_HARNESS_PLAN.md`, results `docs/ELO_RESULTS.md`. Pipeline safety (backup,
alerts) done 2026-10-16. F2 pre-check done 2026-11-06 (D4). F2 done 2026-11-07: F2a
(starters, goalie) and F2b (shot quality). F3a (`penalty_events_resolved`) done
2026-11-07 (bench fix 2026-11-09). F3b (special teams) done 2026-11-09. F3c (roster
availability) done 2026-11-09. F3d (motivation) done 2026-11-09: build step 4 is
done.**

Order: **F0 (done) → F1 (done) → step 5 (done) → F2a (done) → F2b (done) → F3a (done) → F3b (done) → F3c (done) → F3d (done).** NHL stays
deferred.

---

## F1 result and accepted departures (2026-10-12)

`python -m hockey_edge.features.build` rebuilds `data/features.db` in ~6 s (~23 s at F2a, ~45 s since F2b); 99 tests
pass (67 existing, 32 new). Tables: `game_spine` (7,331), `season_format` (13),
`team_season` (198), `features_home` (6,469), `features_schedule` (6,469),
`benchmark_odds` (57).

Accepted departures from the plan as written:
- **History is cut by UTC date, not by start time.** A game that started earlier the
  same day may still be in play at puck drop, so "started earlier" leaks. For Liiga
  this is safe: every game starts and ends within one UTC day, and no team plays
  twice a day. It costs only same-day afternoon games that had already finished.
  **Revisit before NHL**: NHL evening games cross UTC midnight, so a UTC-date cut
  would let an in-progress game through. Use the league's local date or start time
  plus a buffer there. The rule is one line in `features/history.py`.
- **The fixture covers only what F1 reads** (seasons 2022, 2025, 2027, odds rows for
  four games, `raw_payload` blanked). F2 and F3 extend it with the penalty, shot and
  lineup rows listed under Fixtures.
- Home-advantage defaults (league rate over the current and two previous seasons,
  team offset shrunk with `k = 60` games) are Claude Code's choices. Raw values and
  game counts are stored, so `k` is tuned inside training folds.
- Rest days are NULL at each team's first competitive game of a season, not ~170
  days carried over from spring.

Findings after F1:
- **2022 first round was a two-game aggregate series** (inferred from KooKoo–Pelicans
  and Lukko–HPK; nothing stored states it). So 2022:49294 HPK–Lukko 2–2 is probably a
  real regulation draw that was allowed to stand. Its D target is right; the
  `end_type_anomaly` flag stays as a marker.
- **2022:49298 KooKoo–Pelicans 1–0 is a sudden-death tiebreak** (2,101 s, played 17
  minutes after 49296 ended, same crowd and rosters), not a 60-minute game. **Decision:
  drop it from `feeds_ratings`**, with a recorded reason. It is also not "missing
  penalty data": zero penalties in 35 minutes is plausible. Small F1 follow-up, done
  as the first task of step 5.
- `benchmark_odds`: 8 of 57 rows were captured more than an hour before start (five of
  them 24–49 h out), so they are not closing lines. **Step 5 compares only rows with
  `minutes_before_start` ≤ 60.**
- The snapshot job captured nothing 2026-10-08 to 10-10 (PC shut down); all 50 odds
  and lineup windows due then were missed. Those games have no lineup snapshot or
  closing line. Historical starter resolution falls back to `actual_postgame` for them.
- ~~The 2026 relegation format is NULL in `season_format`.~~ **Closed 2026-11-09:**
  2025–26 had no relegation from Liiga (Niklas); `season_format` says `none`, 0
  spots.

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
  `history_before(conn, season, game_id)` → only `ended=1` games on an earlier UTC
  date than this game's `start_utc` (see "F1 result"). Feature code never queries base
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
    evaluation sample but feeds ratings. (After F1: probably a real draw in a
    two-game aggregate series.)
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

**First, a short read-only check (report, then STOP before building):**
- Count `shot_events` per season by result type (`GOAL`, `GOALIE_BLOCKED`, `MISSED`,
  `PLAYER_BLOCKED`, anything else) and by `strength_type`, as attempts per game.
  Flag any season where a type is absent or its per-game rate moves sharply,
  especially across the 2023 boundary.
- For `PLAYER_BLOCKED` rows, check that `shooting_team_id` is the shooter's team
  (shooter's roster team, after normalising the id), not the blocking team, per
  season. Same for the other types on a sample.
**Pre-check done 2026-11-06 → D4 (below).** Attribution is clean: `shooting_team_id`
is the shooter's team in 98.4–100% of `PLAYER_BLOCKED` rows every season. Blocked
shots per game jump from 5–8 to ~17 at 2023 while missed shots fall by about the same
amount, so liiga.fi relabelled attempts from missed to blocked. Total attempts stay at
88–96 per game in every season; unblocked attempts drop ~8% at 2023 and drift down
before it.

**D4. Corsi only (all attempts), no Fenwick (decided 2026-11-06).** The original rule
("Fenwick only if blocked shots are unsteady") assumed blocked shots were
under-recorded. They were relabelled instead, which breaks Fenwick and leaves Corsi
comparable across 2023. Corsi = `GOAL` + `GOALIE_BLOCKED` + `MISSED` +
`PLAYER_BLOCKED`. Shots on goal (`GOAL` + `GOALIE_BLOCKED`) are unaffected and stay
available where a family needs them. No missed/blocked split appears in any feature.

### F2a — Starters and goalie

- `starters.py`: shared actual-starter logic (`verify_starters.py` then imports it)
  plus `resolve_starter`.
- Family 1 `goalie.py`: rolling save% for the resolved starter (shots-based windows),
  shrunk toward league mean; team expected-goalie fallback. Per-goalie xG is not
  stored. An optional GSAx-style column uses the opponent's game xG, from 2021 only,
  and only for games where one goalie played the whole game.
- Tests: truncation/poison on `goalie`; resolver precedence; fallback path; skew
  report on real 2027 rows; starter logic unchanged vs `verify_starters.py`'s current
  output.

**STOP after F2a.** Report: tables and row counts, the skew report, how often the
fallback is used per season, test count.

### F2b — Shot quality

- Family 2 `shot_quality.py`, **from `shot_events` only (D3), Corsi only (D4)**:
  - Corsi for/against and Corsi share, all situations and even strength
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
- Tests: truncation/poison on `shot_quality`; team-id normalisation; a gap game
  treated as missing; Corsi counts all four result types.

**STOP after F2b.** Report: tables and row counts, league-average Corsi per game and
mean Corsi share per season (share should sit at 0.5), test count, the new
`FEATURE_VERSION`.

## Phase F3 — Remaining families

Split 2026-11-07 into F3a–F3d, each with a STOP. Conventions carried over from F2:
feature tables are **one row per team per game**; derived per-game tables are read
through `history_before(..., per_game=...)`; anything that depends on ice time is
checked per season first (goalie ice time is 0 for all of 2026).

### F3a — `penalty_events_resolved` (D1)

One row per competitive penalty event, built from `game_penalty_events` +
`game_rosters`:
- **Player penalties:** team = the penalised player's roster team in that game.
- **Team/bench penalties (`player_id = 0`):** take the roster team of
  `sufferer_player_id`, the player who served the penalty (`sufferer_roster`).
  **Changed 2026-11-09** from "take the game's direction": found in the F3b check,
  bench penalties do not follow the game's player penalties. In 2016, 2023, 2025
  and much of 2026 liiga.fi stores most of them under the other team (about 650
  rows). A two-minute penalty that ends early at the second of a regulation goal
  was taken by the team that did not score; on those, the sufferer rule is right
  for all 335 bench penalties, the game's direction for 224.
- **Game direction is the fallback:** a bench penalty without a sufferer on exactly
  one roster, and a **player on both rosters of the game** (3 competitive
  penalties: 2016:7661, 2016:7665, 2025:399, accepted 2026-11-07), take the game's
  direction: if that game's roster-resolved player penalties are mostly stored on
  the opposite team, swap; if mostly on the right team, keep.
- **`team_source`** column: `roster`, `sufferer_roster`, `game_direction` or
  `unresolved` (player not in the roster, or a fallback in a game with no
  direction).
- Also keep the stored `team_id` and a `was_swapped` flag, for auditing.
- **Build report:** swapped share per season (should reproduce FEATURE_AUDIT item 7's
  table), the number of `unresolved` rows, and games with mixed direction (the audit
  saw eleven competitive games with one stray row).
- **Goal-shortened check** (`penalty_report.goal_shortened`, also a test): every
  two-minute penalty ended early by a regulation goal resolves to the team that did
  not score. Overtime goals are left out: they end the game and every penalty.
- Tests on real fixtures: the five penalty-direction games listed under "Fixtures"
  (2022:152 swapped, 2027:2701274 right, 2025:231 mixed, each with a bench penalty;
  2025:399 a player on both rosters; 2022:35 bench-only), plus the goal-shortened
  check on the fixture's 2025 and 2027 games. 2018 game 1 and 2023 game 1 have no
  bench penalty; the swapped share on the real build covers their seasons.

**F3a done 2026-11-07.** 46,687 rows. The swapped share reproduces FEATURE_AUDIT
item 7 except 2016 (3,221 / 97 vs 3,223 / 99) and 2025 (3,464 / 4 vs 3,465 / 5):
the audit's roster join counted each both-rosters penalty once kept and once
swapped. 9 games with mixed direction, each with one stray row; the audit's
method finds 12 today, the three both-rosters games included. Report:
`python -m hockey_edge.features.penalty_report`.

**F3a bench fix 2026-11-09** (`FEATURE_VERSION` `f3a.1`): bench penalties by
sufferer roster, as above. Unresolved rows 5 → 2 (both by one player in neither
roster, 2016:7943 and 2016:7968; the three bench penalties of 2016:37752 and
2022:35 now resolve by sufferer). By source: 46,685 resolved, of which 1,962 by
sufferer roster and 85 by game direction. The per-season bench "wrong team" counts
in the first F3a report (e.g. 6 of 194 in 2016, 0 of 159 in 2025) came from the old
rule; under this one they are 180 of 194 in 2016, 161 of 164 in 2023, 157 of 159 in
2025, 139 of 198 in 2026, and near all in the seasons whose player penalties are
swapped. Goal-shortened check on the full data: 6,947 / 6,947 player, 326 / 326
sufferer, 9 / 9 game direction.

**STOP after F3a.** Report the build report above, row counts, test count.

### F3b — Special teams (Family 3)

- PP opportunities with the **coincident-adjusted count** (opponent's 2- and 5-minute
  penalties with non-zero duration, minus those starting at the same second as one of
  the team's own), on `penalty_events_resolved`. **Use the derived count for every
  season**, including 2015–2024 where a stored count exists, so there is one rule
  across seasons.
- PP goals: goals scored while the team had the man advantage, from goal events
  and resolved penalty timing (or the goal event's own strength label, if one exists
  and agrees; check and report which). PP% and PK% over rolling windows, plus
  penalties drawn and taken per game. Hard shrinkage toward the league rate, `k` in
  opportunities, raw counts stored.
- The six zero-penalty competitive games listed in PROJECT_CONTEXT are skipped as
  missing (2022:49298 is not one of them; it is a short tiebreak).
- Report: opportunities per team-game per season, league PP% per season, and any
  season that breaks the pattern. Compare derived with derived: the coincident-
  adjusted count runs about 0.2 below stored in every season (FEATURE_AUDIT item 7),
  so the expectation is about 2.9–3.1 derived for 2024–2027 (built: 2024 2.91,
  2025 3.13, 2026 2.90, 2027 2.85), not the stored 3.13 (2024) and 2.96 (2027).
  **[Corrected 2026-11-09: this line first said "~3.1 in 2024–2025, ~2.9–3.0 in
  2026–2027", mixing stored figures for 2024/2027 with derived ones for 2025/2026.]**
- **Built 2026-11-09:** PP goals use liiga.fi's own `YV`/`YV2` label. It matches the
  stored `powerplay_goals` wherever one exists and agrees with the advantage derived
  from penalty timing on 99.2–99.6% of goals per season. Goals exclude `VT0`/`RL0`
  events and shootout attempts. k = 150 opportunities. Tables
  `game_team_special_teams` and `features_special_teams`; report
  `python -m hockey_edge.features.special_teams_report`.
- Same test pattern (truncation, poison, a gap game treated as missing).

**STOP after F3b.**

### F3c — Roster availability (Family 6)

**Pre-check first (report, then STOP):** player ice time per season in
`game_player_period_stats` (share of skater rows with ice time > 0, per season,
competitive games). If any season is zero or partial, as goalie ice time is, propose
a fallback weight (games played, or line position from `game_rosters`) before
building.

- Missing share of the team's recent ice time (or the agreed fallback weight): players
  who carried ice time over the last N games and are absent from this game's lineup.
- Lineup source mirrors the starter resolver: pre-game snapshot lineup when one
  exists, else the actual roster (ended games, training only), else `unknown` with a
  fallback (the team's last known lineup). Source is an audit column, never a model
  input. Skew report on 2027 games that have both.

**Pre-check result (2026-11-09).** Skater ice time in `game_player_period_stats`
exists in every season: 97.2–99.5% of skater rows (competitive ended games) have
ice time > 0 (2015 98.3%, 2016 98.0%, 2025 97.5%, 2026 99.5%, 2027 97.2%; the
rest 98.4–99.2%). Team-games whose skaters total 0: 2016 4 (all on 2016-01-05),
2021 1 (51700 HPK), 2025 20 (the five PLAYOUT games and five QUALIFICATIONS
games). Games with no stats rows at all: 2016 51, 2015 1 (45873), 2021 1 (480).
Goalies are not in the table.

**Decisions (Niklas, 2026-11-09):**
- Weight = skater ice time, every season. No fallback weight.
- `game_rosters` is the squad list, not the lineup; never use it for "who
  played". The historical lineup is the players with stats rows. Position
  comes from that game's `game_rosters.role` (defenceman → D, other skater →
  F), never the snapshot's `roleCode` (the `game_rosters.role_code` fallback
  for a NULL role came in the follow-up below).
- 2016's 51 no-stats games stay without a lineup: no `line` parse, no reparse.
  They resolve to `unknown`.
- A gap team-game (total 0) keeps its rows with `toi_s` NULL (they still say who
  dressed); windows skip it, and it is featured like any other game when it is
  the game being predicted. Partial team-games are reported, no rule.
- Window: the team's last N rating-feeding games with ice time, this season
  only, N = 5 and 10; a traded player's minutes for another team don't count.
- Lineup resolver mirrors the starter's, with the snapshot chosen the same way
  (now the shared `pregame.latest_pregame_snapshot`); unknown falls back to the
  team's lineup in its most recent earlier game this season with one.

**Built 2026-11-09** (`FEATURE_VERSION` `f3c`). `game_skater_toi` (228,790 rows;
`features/lineups.py`, also `resolve_lineup`) and `features_availability`
(12,938; `features/availability.py`); `history_before` accepts
`game_skater_toi`. `game_starters` and `features_goalie` came out row-for-row
identical after the snapshot choice moved to `features/pregame.py`. Rebuild
44–74 s on 2026-11-09, varying with machine load (56 s before F3c). Report:
`python -m hockey_edge.features.availability_report`.
- **Skew, 2027:** 254 team-games have both a snapshot and an actual lineup; the
  sets match in all 254, so every feature value is identical. Missing a lineup
  window costs a mean |missing_share_10 − fallback| of 0.029 (max 0.166) on the
  snapshot rows, 0.022 on the actual ones.
- **Lineup source:** `actual` for every ended game with stats; `unknown`
  2015 2, 2016 102, 2021 2 (no stats); 2027 `snapshot` 254, `actual` 68 (team-games
  of 2026-09-01, before capture began; missed windows on 09-04 and 09-05; the
  10-08–10 and 10-17 capture gaps), `unknown` 766
  (unplayed). `missing_share_10` is NULL only for an empty window (15–28 per
  season: first games, plus Jokerit's 2025 qualification games); mean
  0.12–0.14 per season (2027 so far 0.09), p90 about 0.20–0.24.
- **Partial ice time** (total > 0 but < 0.8 × 5 × game time): 15 team-games in
  the first build. Six were the other team-games of 2016-01-05, the gap day, at
  2–17% of a full game; the rest are 2015:6226, 2015:6241, 2016:7875,
  2016:7935, 2017:4342, 2019:431, 2020:94, 2021:55522, 2025:435 (25–78%), kept
  in windows, report only.
- **Positions:** no stats row lacks a roster row. 629 skater rows (2015–2019)
  have a roster row with `role` NULL, 623 of them `role_code` "13. H". Every
  snapshot role maps.

**F3c follow-up 2026-11-09 (Niklas's decisions on the two open questions;
still `f3c`):**
- **2016-01-05 is a gap day, by name** (`lineups.GAP_DATES`, not a threshold):
  all ten team-games there get `toi_s` NULL. 114 rows of the six partial
  team-games changed (the four at 0 were already NULL); 2016 now has 10 gap
  team-games. The nine other partial team-games stay as they are.
- **Position fallback:** when `game_rosters.role` is NULL, `pos` comes from that
  row's `role_code` by its letter after any "N. " prefix (H or KH → F, P → D);
  `role` stays first. Only `game_rosters.role_code`: the snapshot's `roleCode` is
  never used. 623 rows gained `pos` F (2015 25, 2016 2, 2017 220, 2018 239,
  2019 137). **Still without a position: 6 rows**, `role` and `role_code` both
  NULL (2015:6399 KalPa 25461491; 2015:45882, 45883, 45901 Tappara 21924709;
  2016:7691 Blues 28576383; 2016:7747 Pelicans 24244606). Each of these players
  has a position in 35–72 other games that season, so a window usually gets it
  from another game.
- **Effect on `features_availability`, 2015–2019** (vs. the first F3c build):
  rows with any changed value 2015 3, 2016 80, 2017 145, 2018 112, 2019 73.
  `n_window_games_5` unchanged; `n_window_games_10` down by 1 on 3 rows of 2016.
  `missing_share_10` changed only in 2016 (78 rows, mean |shift| 0.009, max
  0.023; season mean 0.1334 → 0.1341); 2015 and 2017–2019 unchanged, as the
  position rule moves only the F/D split. `missing_share_f_10` shifts by a mean
  0.002–0.011 per season (max 0.035), `missing_share_d_10` by 0.006–0.027 (max
  0.041; a player whose most recent window game had no position used to take an
  older one).
- **Resync guard (read-only question):** the `game_stats` value guard does check
  skater ice time: `player_toi` = SUM of `game_player_period_stats.
  time_on_ice_seconds` over the game. Its other value totals: `player_corsi_for`,
  `player_faceoffs` (`faceoffs_total`), `goalie_toi`, `team_face_off_wins`,
  `team_powerplay_instances`, `team_shorthanded_instances` (each refused below
  50% of its old value), plus the goal totals `team_goals` and `player_goals`
  (refused when they move away from the final score). The totals are per game,
  both teams together: one team's ice time stripped to 0 leaves about half the
  total, right at the 50% floor, so such a reparse can pass.
- Fixture: F3c slices appended to `mini.sql` with
  `scripts/make_feature_fixture.py --append-f3c` (skater stats for 2027 and
  Pelicans', Jukurit's and Jokerit's 2025 games from 2025-02-01; their roster
  rows with role; 2027 snapshot rosters cut to id, role, line).

**STOP after F3c.**

### F3d — Motivation (Family 7)

- Playoff-line and relegation-line distance via `season_format`. From 2027, relegation
  pressure is season-long.
- ~~**Needs the 2026 relegation format filled in `season_format` first.**~~ **Closed
  2026-11-09:** 2025–26 had no relegation from Liiga (Niklas).

**Built 2026-11-09** (`FEATURE_VERSION` `f3d`). `features_motivation` (12,938 rows,
`features/motivation.py`); `season_format` gains `playoff_spots` and
`relegation_spots`. Rebuild about 45 s. Report:
`python -m hockey_edge.features.motivation_report`.
- **season_format (hand-curated):** playoff spots 10 in 2015–2024, 12 in 2025–2027;
  relegation zone none before 2025, 2025 bottom 2 (places 15–16 to the playout),
  2026 none, 2027 bottom 3 (15–17 directly). 2020's playoffs were cancelled; its
  planned 10 spots are kept as the line teams played for. 2027's format (top 12 of
  17 to the playoffs, bottom three relegated directly) is confirmed by Niklas from
  MTV Uutiset's season preview, 2026-08-31; the data can check it only once the
  2027 playoffs are listed.
  The build now stops if a season has more PLAYOFFS teams than curated spots, or,
  once a later season exists, a different number (playoff games are listed round
  by round, so the live season may fall short). Checked against the data: in every season with playoffs the
  PLAYOFFS teams are exactly the top N of the derived final table, and 2025's
  PLAYOUT teams exactly its bottom 2. No season failed to fit.
- **Standings:** the season's RUNKOSARJA games on earlier UTC dates, through
  `history_before`; teams from `team_season`. Points 3/2/1/0 (regulation win, OT/SO
  win, OT/SO loss, regulation loss); order points, then goal difference.
- **Points check:** the derived final table matches liiga.fi's own standings
  (`hockey.standings`, phase `season`) in points and games for every team of every
  season 2015–2027 (198 team-seasons). In 2015 and 2016 liiga.fi files Espoo
  Blues' full record under K-Espoo's id and adds a stray partial Blues row; the
  report matches the record by games and points.
- **Order check:** the derived order differs from liiga.fi's in 2016 (2 places),
  2017 (2), 2023 (4), 2025 (2) and live 2027 (4), always between teams level on
  points: liiga.fi breaks ties its own way, not modelled. No difference crosses a
  playoff or relegation line in a finished season.
- **games_remaining** uses the nominal schedule (`games_per_team` − games
  played), known before the season. It equals the listed schedule in every
  complete season; counting listed games would leak the 2020 cut and the
  2021–2022 mid-season cancellations.
- **Flags** are conservative (3 points a game left, ties not settled). A test
  checks that every clinched, eliminated, safe or doomed team-game in 2022 and 2025
  ends on the right side of its line. In each team's last 10 games, playoff
  clinched / eliminated team-games: 2015 53/20, 2016 62/13, 2017 65/13, 2018 59/18,
  2019 58/19, 2020 75/22, 2021 41/10, 2022 24/30, 2023 55/17, 2024 51/19,
  2025 54/8 (relegation safe 69, doomed 8), 2026 81/12. 2021–2022 count fewer
  because the nominal schedule overstates games left after cancellations.

**STOP after F3d**, which ends build step 4.

---

## Fixtures

Regenerated whole on 2026-11-12 (before step 6); 2027 is in it to 2026-11-11.

- `scripts/make_feature_fixture.py` writes `fixtures/features/mini.sql` (text dump,
  git-diffable) in one pass from `hockey.db` and `snapshots.db`, both opened
  read-only. Each slice keeps only the rows and the columns that the feature build,
  its reports and `scripts/verify_starters.py` read, plus NOT NULL columns; no
  hockey.db row id is written (SQLite numbers rows on load; resync renumbers them
  anyway). snapshots.db ids are kept: append-only, and they break ties. Season 2027
  is live, so a regeneration re-pins the tests that count 2027 games or snapshots.
- Rows: seasons 2022, 2025 and 2027 of `games` in full (the build accepts only
  whole seasons): all teams, known OT/SO games, PLAYOFFS series, 2022:49294 and the
  49298 tiebreak, foreign friendlies, the 2025 Jokerit qualification series and the
  2025 PLAYOUT series, K-Espoo's and Jokerit's first seasons, unplayed games.
  - Odds: every `odds_snapshots` row of four 2027 games, `raw_payload` blanked.
  - F2a: goalie period stats and goalkeeper events for the competitive games of
    2025 and 2027; the parsed 2027 lineup snapshots that name a starter (the only
    ones read), `raw_payload` blanked, `roster_json` cut to each player's id and
    role.
  - F2b: `shot_events` for the competitive games of 2027 and of 2025 from
    2025-01-22 to 2025-02-23, which runs into the 2025 `shot_events` gap.
  - F3a/F3b: penalty and goal events for the ended competitive games of 2027 and
    of the same 2025 stretch, and for five penalty-direction games:
    - 2022:152: stored swapped, with a bench penalty;
    - 2027:2701274: stored right, with a bench penalty;
    - 2025:231: mixed (one stray row among seven player penalties), with a bench
      penalty;
    - 2025:399: a penalised player listed on both rosters;
    - 2022:35: a single bench penalty and nothing else (resolves by sufferer since
      the 2026-11-09 fix).

    2018 game 1 and 2023 game 1 are not in the fixture: neither has a bench
    penalty, and the build accepts only whole seasons. The per-season swapped share
    on the real build checks 2018 and 2023 against FEATURE_AUDIT item 7.
  - F3c: skater period stats (keys and ice time) for the ended competitive games of
    2027 and Pelicans', Jukurit's and Jokerit's 2025 games from 2025-02-01 (run-in,
    then the ice-time gap of the PLAYOUT and QUALIFICATIONS series).
  - `game_rosters`: the rows of every player a penalty names (penalised player or
    sufferer; all the penalty-team rule reads) and of every skater with a stats
    row, with `role` and `role_code` (they give `pos`).
  - F3d reads `games` only.
- Tests (`tests/features/support.py`): the fixture is loaded into temp source
  databases and built once per test process (`shared()`); the two source files and
  the full `features.db` are made read-only and every `FeatureTest` class opens its
  own query-only connection, so no test can change what another reads. Tests that
  edit sources work on writable copies in their class's temp directory
  (`edited_hockey`, `edited_snapshots`). Before 2026-11-12 every class loaded and
  built it again (31 loads, 81 builds per run; now 1 and 51).

## Decisions for Niklas (collected)

- Thin odds benchmark: accept, or a historical-odds session.
- PLAYOFFS as a separate evaluation slice.
- In-house location xG: later or never.
