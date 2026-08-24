# RECOVERY_BACKLOG — deferred archaeology, ready-to-execute (2026-08-25)

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
