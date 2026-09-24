# RECOVERY_BACKLOG — historical recovery (planned 2026-08-25, section 1 run 2026-09-24)

**Status: section 1 (`game_detail`) is DONE, including the R5 salvage.
Section 2 (`game_stats`) is still deferred.** The results come first below.
The original plan follows unchanged from "Both recoveries below…" onwards as
history. Where it conflicts with the results, the results win: the target
was 1,135, not 1,137, and the command used was `--targets`, not
`--days 4380`.

## Results — section 1, run 2026-09-24

### How it was run
- Targets: `python scripts/recovery_targets.py` →
  `data/recovery/game_detail_targets.csv`, **1,135 games** (seasons
  2015–2024, ended, zero `game_penalty_events`, any phase). That is 2 fewer
  than the 1,137 below, because the count here didn't filter on `ended`.
  2015:6570 and 2017:6763 are PRACTICE friendlies that never ended.
- Fetch: `python -m hockey_edge.ingest.liiga.resync --targets
  data/recovery/game_detail_targets.csv --endpoints game_detail
  --min-hours-since-fetch 168 --outcomes data/recovery/game_detail_outcomes.csv`.
  That was 1,135 requests through the no-shrink guard: **1,086 reparsed,
  49 refused, 0 fetch failures.** Backup: `data/backups/hockey_pre_recovery.db`.
- **The damage covered all 10 seasons, 2015–2024**, not the 8 an earlier note
  listed. 2018 had 35 games and 2022 had 72.
- Before/after: `scripts/recovery_report.py --out` (before.json,
  after_detail.json, after_salvage.json) and `--diff`. All files are in
  `data/recovery/`, which is gitignored.

### The guard is all-or-nothing per game
The guard reverts **every** guarded table for a game when any one of them
would shrink. So a refetch that recovers penalties 0→9 but carries one fewer
`game_rosters` row loses the penalty recovery too. That happened to all 49
refused games:
- 48 were refused over `game_rosters`. 2015/2016 competitive games lost 1–5
  rows; foreign friendlies lost 2–26 (Sibir 31→8 and 30→4, Amur 34→24).
- 1 was refused over `game_goalkeeper_events` (2021:9947, a friendly, 4→0).
- 32 of the 49 saved new responses contained penalties: 29 competitive
  games (2015 RUNKOSARJA 9, 2015 PLAYOFFS 3, 2016 RUNKOSARJA 17) and 3
  friendlies (2020:1050, 2024:1395, 2024:1549). The other 17 were friendlies
  with no penalties in either version, so there was nothing to salvage.

### R5 salvage: grow-from-zero (approved 2026-09-24)
This is a narrow exception to the uniform guard ruling of 2026-08-24:
`resync.py --salvage-from-raw` (`salvage_grow_from_zero`). It makes **no
HTTP requests**. It reparses each game's saved raw response and keeps a
guarded table's new rows **only if that table had zero rows for the game
before**. Every other guarded table is restored row for row.
- Targets: `python scripts/recovery_targets.py --salvage-from
  data/recovery/game_detail_outcomes.csv` → `salvage_targets.csv` (32 games).
- Run: `python -m hockey_edge.ingest.liiga.resync --targets
  data/recovery/salvage_targets.csv --endpoints game_detail --salvage-from-raw
  --outcomes data/recovery/game_detail_outcomes.csv`. Backup:
  `data/backups/hockey_pre_salvage.db`.
- Result: **32/32 salvaged, adding 304 penalty events.** 21 of the 32 also
  had zero goalkeeper events, and those 21 gained 37 goalkeeper events. No
  roster row changed.
- Roster check: every salvaged penalty's `player_id` is in that game's
  `game_rosters`, with zero exceptions. `player_id = 0` is liiga.fi's marker
  for a penalty with no individual player (mostly *Joukkuerangaistus*, the
  team penalty; 2,152 rows in the DB) and is not checked. Before the
  salvage, the DB already had 3 penalties whose player was missing from the
  roster: 2016:7943 and 2016:7968 (player 25491335) and 2018:8235 (a
  friendly). None of them came from this recovery.
- Test: `tests/test_salvage.py`, against the real 2024:1 payloads in
  `fixtures/liiga/game_detail/` (original, and the refetch whose goalkeeper
  events fell 7→1).

### Outcome per targeted game (final, after salvage)
| season | recovered | recovered by salvage | changed, no gain | refused |
|---|---|---|---|---|
| 2015 | 184 | 12 | 2 | 0 |
| 2016 | 16 | 17 | 36 | 0 |
| 2017 | 2 | 0 | 2 | 8 |
| 2018 | 0 | 0 | 35 | 0 |
| 2019 | 12 | 0 | 43 | 0 |
| 2020 | 37 | 1 | 55 | 1 |
| 2021 | 10 | 0 | 6 | 1 |
| 2022 | 0 | 0 | 72 | 0 |
| 2023 | 459 | 0 | 51 | 7 |
| 2024 | 52 | 2 | 12 | 0 |

331 targets still have zero penalties: the 314 "changed, no gain" games and
the 17 still refused. 324 of them are PRACTICE friendlies. The other 7 are
the known gaps below: 6 RUNKOSARJA and 1 PLAYOFFS.

### Acceptance (R4)
1. Each targeted game has exactly one outcome (table above).
2. No guarded table's total shrank for any season.
3. `game_goal_events` counts are identical before and after for every season.
4. **Zero-penalty RUNKOSARJA games in 2015–2024: 665 → 6.** The clean
   2025/2026 reference is 0 of 960, so the 6 are listed below as known gaps.
5. Penalties per game are in line with 2025/2026 (7.0–7.5) for 2016–2024,
   at 7.0–8.6. **2015 is not: 8.99.** See the open finding below.
6. Goalkeeper-event coverage, report only: 2015 RUNKOSARJA 41%→65% and
   2023 0%→63%; other seasons moved by a few points.

### Known gaps: the 6 zero-penalty RUNKOSARJA games left
All six were reparsed. liiga.fi itself now returns zero penalties for them,
so a refetch won't help.

| game | teams | final | note |
|---|---|---|---|
| 2016:7862 | Sport–HIFK | 1–2 | goal events match score |
| 2017:4409 | JYP–Tappara | 2–3 | goal events match score |
| 2020:252 | Jukurit–KooKoo | 1–3 | goal events match score |
| 2021:480 | HIFK–Kärpät | 0–1 | **damaged at source**: final 0–1 but zero goal events and zero goalkeeper events |
| 2022:317 | Ässät–SaiPa | 5–6 | goal events match score |
| 2022:332 | SaiPa–Kärpät | 0–5 | goal events match score |

One more competitive game in the same state, outside the RUNKOSARJA
count: **2022:49298, PLAYOFFS, KooKoo–Pelicans 1–0**. It was reparsed,
liiga.fi still returns zero penalties, and its goal events match the score.
These 7 are the only zero-penalty competitive games in seasons 2015–2026.

**For the feature store: a competitive game with zero penalty events counts
as missing data, not as zero penalties.** Special-teams features must skip
it, not count it as a clean game. No zero-penalty RUNKOSARJA game exists in
960 clean 2025/2026 games.

### Partial-damage check, 2015/2016 (run 2026-09-24): no damage; the gap comes from the source
**The question.** 2015 RUNKOSARJA games recovered above average **10.5
penalties/game (median 10)**. The 259 that were never at zero average **8.0
(median 8)**, and the gap holds in every month (+1.4 to +4.1 per game). All
259 were fetched inside the bad window, so partial loss was the suspicion.
2016 showed a weaker version (9.1 vs 7.7).

**The check.** Every ended 2015 and 2016 game not in the section 1 targets
was refetched, any phase: 793 games (`data/recovery/partial_2015_2016_targets.csv`).
The command was the same as R3: `resync.py --targets … --endpoints game_detail
--min-hours-since-fetch 168 --outcomes data/recovery/partial_2015_2016_outcomes.csv`.
Backup: `data/backups/hockey_pre_partial_2015_2016.db`. A PC restart
interrupted the run after 82 games, cleanly: no shrink, and no game fetched
without an outcome row. Rerunning the same command resumed it, skipping the
82 already done. Reports: `before_partial.json`, `after_partial.json`,
`diff_partial.txt`.

**Result: 699 reparsed, 94 refused, 0 fetch failures. Penalties changed for
none of them.**

| season × phase | games | penalties/game before → after |
|---|---|---|
| 2015 RUNKOSARJA | 259 | 8.03 → 8.03 |
| 2015 PLAYOFFS | 31 | 7.19 → 7.19 |
| 2015 PRACTICE | 18 | 7.72 → 7.72 |
| 2016 RUNKOSARJA | 422 | 7.70 → 7.70 |
| 2016 PLAYOFFS | 47 | 8.00 → 8.00 |
| 2016 PRACTICE | 16 | 7.62 → 7.62 |

- **Refusals.** 83 were over `game_goalkeeper_events`: liiga.fi now returns
  fewer goalkeeper events than the July copy, e.g. 8→0 or 7→1, the same
  pattern as the 2024:1 regression. The other 11 were over `game_rosters`
  (−1 to −4). By group: 2015 RUNKOSARJA 54, 2015 PLAYOFFS 26, 2016
  RUNKOSARJA 13, 2016 PLAYOFFS 1. **None cost a penalty.** The refetch's
  penalty count equals the DB's in all 94, so there is nothing to salvage.
  The guard kept the richer July goalkeeper events, which is the right call.
- Invariants hold: no shrink anywhere, and `game_goal_events` is identical.
  `game_rosters` grew by +30 in 2015 and +38 in 2016 through ordinary
  reparses.
- **The 2015 gap did not close (still 10.5 vs 8.0).** The never-zero games
  are not partially damaged. liiga.fi returns the same penalties for them
  today as in July.

**Where the gap comes from: two recording styles in the source.** The
recovered 2015 games list penalty types that the never-zero games almost
never have:

| 2015 RUNKOSARJA | penalties/game | PIM/game | share that are 2-min | top extra types |
|---|---|---|---|---|
| recovered (161) | 10.5 | 36.3 | 83% | `VKV` 1.28/game, `fault_type` NULL 1.42/game |
| never-zero (259) | 8.0 | 15.8 | 98% | — |

Counting only 2-minute penalties shrinks the gap from +2.5 to about +0.9 per
game (8.8 vs 7.9). Which recording style a game got looks like a source-side
data-entry difference, not anything in this pipeline. It holds in every
month. Team mix hasn't been checked beyond per-team game counts, which are
spread across all teams in both groups.

**For the feature store:** don't use raw `game_penalty_events` counts as
power-play opportunities for 2015 (or anywhere). Count minors
(`penalty_minutes = 2`) or derive power plays from penalty timing, so
misconduct-style entries and untyped entries don't inflate special-teams
rates. 2015 is otherwise no longer suspect.

### Section 2 (`game_stats`), still deferred
"Fewer than 3 `game_puck_control` rows" turned out not to narrow anything:
it selects 5,512 of the 5,515 ended 2015–2024 games
(`data/recovery/game_stats_targets.csv`). So the pass is effectively the full
sweep, ≈2.3 h. When it runs, use `resync.py --targets
data/recovery/game_stats_targets.csv --endpoints game_stats
--min-hours-since-fetch 168 --outcomes …`, split by season if needed, not
`backfill.py --force`.

---

Both recoveries below are **not run this session** — this doc captures exact
commands and cost estimates so they can be run later without re-deriving
anything. No code changes, nothing executed beyond the read-only recon that
confirms each recovery is real.

## 1. `game_detail` recovery — `game_penalty_events` / `game_goalkeeper_events`

### Confirmed recoverable

Season=2023 hosts a "missing `game` key entirely" anomaly (symptom class 1,
see `docs/RESYNC.md` 4a) already confirmed recoverable via the season=2015
probe. **Untested until now was symptom class 2** — `game` key present,
`penaltyEvents` empty for both teams. Refetched two season=2023 RUNKOSARJA
games live (recon only, nothing parsed into curated tables):

| game | before | after |
|---|---|---|
| 2023:1 (Tappara–TPS) | home 0, away 0 penalties; away 0 goalkeeper events | home 1, away 1 penalties; away 2 goalkeeper events |
| 2023:2 (HIFK–HPK) | home 0, away 0 penalties | home 3, away 5 penalties; goalkeeper events still 0/0 |

**`penaltyEvents` recovered cleanly in both (2/2). `goalKeeperEvents` recovered partially in one, not at all in the other** — goalkeeper-event data may genuinely not exist for some of these games rather than being purely a fetch artifact; don't assume a full recovery run will backfill 100% of goalkeeper data even though it clearly helps penalties.

### Scope: the ~1,164 zero-`game_penalty_events` games, by symptom class and season

| season | class 1: missing `game` key | class 2: `game` present, empty | total | fetched |
|---|---|---|---|---|
| 2015 | 197 | 2 | 199 | 07-19/07-21 window |
| 2016 | 36 | 33 | 69 | 07-20 |
| 2017 | 2 | 11 | 13 | 07-20 |
| 2018 | 0 | 35 | 35 | 07-20 |
| 2019 | 12 | 43 | 55 | 07-20 |
| 2020 | 93 | 1 | 94 | 07-20 |
| 2021 | 10 | 7 | 17 | 07-20 |
| 2022 | 0 | 72 | 72 | 07-19/07-20 |
| 2023 | 67 | 450 | 517 | 07-19 |
| 2024 | 66 | 0 | 66 | 07-19 |
| 2025 | 0 | 16 | 16 | **08-22 — outside the bad window** |
| 2026 | 0 | 11 | 11 | **08-22 — outside the bad window** |
| **total** | **483** | **681** | **1,164** | |

**Recommended recovery scope excludes 2025/2026's 27 games.** Every other
season's zero-penalty games were fetched inside the confirmed 2026-07-19
19:55 – 2026-07-21 05:53 UTC bad-fetch window (see `docs/RESYNC.md` 4a); 2025
and 2026 were fetched 2026-08-22, in a run CLAUDE.md's own status notes
describe as 100%-clean, with no missing-`game`-key files at all. Those 27 are
more likely genuine low/zero-penalty games than a fetch artifact — recovering
them isn't justified by anything found so far. **Real recovery target: 1,137
games across seasons 2015–2024.**

### The exact command — and why it isn't `backfill.py`

**The no-shrink guard is what makes this recovery safe, and the guard only
lives in `resync.py` — `backfill.py --force` has no guard at all** (see the
corrected warning in `docs/RESYNC.md`). For these specific 1,137 games,
`game_penalty_events`/`game_goalkeeper_events` are already at zero, so they
can't shrink further either way — but the same `game_detail` upsert also
rewrites `game_rosters` for each game in one blind delete-and-reinsert, and
`game_rosters` is currently good, populated data with no such floor. Running
this through `backfill.py` risks that table exactly the way 4a's original
finding warned about; running it through `resync.py` doesn't, because the
guard protects `game_rosters` too.

**Recommended — guarded, via `resync.py`:**

```
python -m hockey_edge.ingest.liiga.resync --days 4380 --endpoints game_detail
```

Caveat, stated plainly rather than glossed over: `resync.py`'s candidate
selection is "ended games within the last N days," a single one-sided
window — it cannot express "2015–2024 but not 2025–2026" (2025/2026 are more
recent, so any `--days` reaching back to 2015 necessarily sweeps them in
too). This command will therefore touch **all 6,733 ended games**, not just
the 1,137 targeted ones. That's not unsafe — the guard protects every game
it touches, and an unchanged-hash game costs one wasted request and no
reparse — just less efficient than a precisely-scoped run would be.
Estimated runtime: 6,733 × 1.5s ≈ **2.8 hours**. Run `--dry-run` first to
confirm the candidate count before spending the traffic for real.

**Unguarded alternative, for comparison only — not recommended without
accepting the `game_rosters` risk above:**

```
python -m hockey_edge.ingest.liiga.backfill --season <2015..2024> --force --endpoints game_detail
```

Precisely scoped to the 10 affected seasons: 5,517 games total, ≈ **2.3
hours** at 1.5s (10 separate per-season invocations). Faster and more
precisely targeted than the `resync.py` sweep, but every one of those 5,517
games' `game_rosters` gets blindly replaced with whatever the live fetch
returns, un-reviewed. Given `resync.py` reaches the same outcome safely for
only ~0.5h more traffic, there's no real reason to prefer this path — it's
documented here so the trade-off is visible, not as a suggestion.

## 2. `game_stats`/`shotmap` puck-control recovery

Already fully documented in `docs/RESYNC.md`'s 4b section — cross-referenced
here, not duplicated. Headline: ~11,000 missing `game_puck_control` rows
across seasons 2015–2024 (~0.95 rows/game vs. ~2.9 in 2025/2026), recoverable
via:

```
python -m hockey_edge.ingest.liiga.backfill --season <2015..2024> --force --endpoints game_stats
```

(`--endpoints game_stats,shotmap` if bundling the shotmap correction too —
`docs/RESYNC.md` 4a confirmed `shotmap` only ever corrects a stat in place,
never loses shot events, so it's safe unguarded.) ~5,517 games ≈ 2.3h at
1.5s. Unlike the `game_detail` case above, `game_stats`'s own tables
(`game_team_period_stats`, `game_player_period_stats`,
`game_goalie_period_stats`, `game_puck_control`) are the *only* tables its
upsert touches — there's no adjacent `game_rosters`-style table at risk, so
`backfill.py`'s unguarded path doesn't carry the same caveat as `game_detail`
does. Still, `resync.py --endpoints game_stats` is the safer default if ever
run alongside the `game_detail` recovery in one pass.

## Open question — not investigated, no mechanism proposed

**45 PLAYOFFS games were fetched in the exact same 2026-07-19T20:04–21:22
UTC window that broke 450 RUNKOSARJA games, and came through completely
clean (0/45 affected).** If the bad-fetch window were pure random
server-side flakiness independent of what was being fetched, some PLAYOFFS
games landing in that same 78-minute span would be expected to show the same
damage by chance. They don't — 100% clean. Something about the phase, or
about however liiga.fi serves that specific data, appears to matter, not
just timing. No mechanism is proposed here and none should be assumed until
someone actually looks — flagging it as open, not guessing at it.
