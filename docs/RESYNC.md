# RESYNC — Phase 4 findings and the re-sync mechanism (2026-08-24)

## 4a. Scope check: does only `game_stats` enrich retroactively?

**No. All three per-game endpoints (`game_detail`, `game_stats`, `shotmap`)
return different content on refetch for the same completed historical game —
and not all in the direction of "more data."** Refetched season=2024,
game_id=1 (Lukko vs. HPK, 2023-09-12) across all three, diffing each fresh
response's content hash against the stored `raw_responses` row (5 liiga.fi
requests total for this investigation, no OddsPapi budget touched):

| endpoint | changed? | nature of the change |
|---|---|---|
| `game_stats` | No (already matched the known 2026-08-22 enrichment) | — |
| `shotmap` | Yes | All 88 shots present both times, matched 1:1 by (period, gameTime, shooterId). The **only** fields that ever differ across all 88: `ownTeamPlayersOnIce`/`otherTeamPlayersOnIce`, +1 in every case. A clean, systematic correction — nothing lost, nothing added, one stat corrected. |
| `game_detail` | Yes, and this is the one that matters | See below. |

**`game_detail`'s goalkeeper-event data got smaller on refetch, not richer.**
Home team's `goalKeeperEvents` went from 4 real entries (a full
period-by-period goalie assignment) down to 1 (just the final pulled-goalie/
empty-net entry); `goalKeeperChanges` went from 3 entries to **0**. Away
team: `goalKeeperEvents` 3→**0**, `goalKeeperChanges` 2→**0**. This directly
contradicts the "liiga.fi retroactively enriches completed games" framing
that CLAUDE.md's CRITICAL FINDING (2026-08-23) and this project's whole
Phase 4 problem statement were built on — that framing came from a single
observation on `game_stats` and turned out not to generalize. `game_detail`
also showed `penaltyEvents` entries reassigned between the home/away team
arrays (looks like a genuine upstream correction of which team an event
belonged to, not corruption — a player's own penalty moved to the correct
side), and several players' `alternateCaptain` flags flipped. The actual
`goalEvents` arrays and final score were verified identical across every
fetch — goals, the target variable, were never in question here.

`parsers.py` ingests `goalEvents`, `penaltyEvents`, `goalKeeperEvents`, and
`homeTeamPlayers`/`awayTeamPlayers` into curated tables
(`game_goalkeeper_events`, `game_penalty_events`, `game_rosters`). A re-sync
that treated "content changed → take the new version as ground truth" would,
for this exact game, have **deleted 7 correct goalkeeper-event rows and
replaced them with 1** — a regression, not a fix.

**Ruling (2026-08-24, from the user): implement a universal no-shrink guard
in the re-sync mechanism itself, not a per-endpoint policy** — since one
game isn't enough to know which endpoints can regress, the guard has to
catch shrinkage wherever it happens, for all three endpoints alike.

## 4b. Endpoint-scoped `--force`

`backfill.py --force` used to force-refetch all five endpoints
(`games_by_season`, `standings`, `game_detail`, `game_stats`, `shotmap`)
unconditionally — fine for a full backfill, wasteful (triples the traffic)
for a targeted recovery. `--endpoints` now scopes which endpoints `--force`
applies to; anything not named still uses normal cache-first fetching:

```
python -m hockey_edge.ingest.liiga.backfill --season 2024 --force --endpoints game_stats
```

Omitting `--endpoints` preserves the exact original behavior (force
everything) — no behavior change for any existing command someone might
already be running.

### The deferred 2015–2024 `game_puck_control` recovery — exact command, and a hard warning

**[Superseded 2026-09-25. It ran through `resync.py --targets`, not the
`backfill.py` command below. Only 2023/2024 had puck-control values to
gain, and the refetch stripped period stats, which had to be restored from
backup. Do not run the command below: it would apply the stripped values
with no guard at all. See `docs/RECOVERY_BACKLOG.md` section 2 results.]**

CLAUDE.md's CRITICAL FINDING already identified ~11,000 missing
`game_puck_control` rows across seasons 2015–2024 (average ~0.95 rows/game
vs. ~2.9 in 2025/2026), recoverable via a `game_stats`-scoped `--force`
refetch of ~5,517 games. That work is **not run this session** — it's
~2.3h of traffic and this is explicitly "build the mechanism, don't run the
archaeology." The command, once someone decides to run it, per season:

```
python -m hockey_edge.ingest.liiga.backfill --season <2015..2024> --force --endpoints game_stats
```

`puckStats` (→ `game_puck_control`) lives under `game_stats` only. `shotmap`
is the other endpoint confirmed safe to bulk-refetch historically (4a found
it only ever corrects a stat in place, never loses shot events) —
`--endpoints game_stats,shotmap` is the safe combination if a shotmap
recovery is ever bundled with it.

**Correction (2026-08-25, from the user): the earlier ruling here —
"`game_detail` must never be bulk-refetched against historical seasons" —
predated the no-shrink guard and is superseded by it.** The guard (4c,
below) is exactly what makes a *targeted* refetch of *identified-broken*
`game_detail` games safe: it blocks any reparse that would leave a guarded
table (`game_rosters`, `game_penalty_events`, `game_goalkeeper_events`) with
fewer rows than it already has, so there is no shrink risk left to guard
against for games selected this way. The real constraint was never
"`game_detail` is unsafe," it was "`backfill.py --force`'s blind
delete-and-reinsert is unsafe" — those are different things, and the guard
only protects the latter's replacement path, not `backfill.py` itself.

**Practical consequence: recovering `game_detail` for known-broken games
should go through the guarded path (`resync.py`), not through
`backfill.py --force --endpoints game_detail`.** `backfill.py`'s upsert has
no guard, and while the specific rows known to be broken (empty
`game_penalty_events`/`game_goalkeeper_events`) can only improve or stay
flat — they can't shrink below zero — the same blind upsert *also* rewrites
`game_rosters` for that game in the same call, which currently holds good
data and has no such floor. `resync.py`'s guard protects all three tables
uniformly; `backfill.py`'s does not protect any of them. See
`docs/RECOVERY_BACKLOG.md` for the concrete `game_detail` recovery command
and this exact trade-off spelled out.

## 4c. Time-windowed re-sync — `src/hockey_edge/ingest/liiga/resync.py`

New module, separate from `backfill.py` (season-scoped, one-time-per-season)
and separate from `hockey_edge.snapshot.job` (pre-puck-drop capture against
`snapshots.db`, different database, different failure modes, different
leakage rules — deliberately kept apart in code and in logs, per the
original brief). Runs against `data/hockey.db` only, against games that have
already ended.

```
python -m hockey_edge.ingest.liiga.resync --days 7                          # default: all 3 endpoints
python -m hockey_edge.ingest.liiga.resync --days 7 --dry-run                # zero HTTP requests
python -m hockey_edge.ingest.liiga.resync --days 7 --endpoints game_stats   # scope to one endpoint
```

**Candidate selection**: games where `ended=1` and `start_utc` falls within
the last `--days` days (default 7). **Staleness within that candidate set**
is derived from `sync_state.fetched_at` vs. now — columns that already
exist — rather than a new `last_verified_at` column, so this needed **no
DDL change**. A `(game, endpoint)` is due once its last fetch is older than
`--min-hours-since-fetch` (default 12h); this is what keeps a 15-minute
scheduler cadence from refetching the same completed game dozens of times
across the `--days` window — the outer schedule can be dumb (fire every 15
min, same as the snapshot job) because this check, not the schedule, decides
what's actually stale.

**Per `(game, endpoint)` flow**: force-refetch (bypassing `sync_state`'s
normal "already success, skip" shortcut — that shortcut is exactly the bug
this module exists to route around). The raw file + `sync_state` +
`raw_responses` are written unconditionally on any real fetch, same as
always — "still write the new raw response to disk, append-only, no loss"
is satisfied by `raw_cache.fetch` itself, nothing extra needed. Content hash
decides whether to reparse: **unchanged content → no curated write at all.**
Changed content → parse, then:

**No-shrink guard** (`reparse_with_shrink_guard`): captures each guarded
table's current row count *and* a full row-for-row backup for this
`(game_id, season)` before applying the upsert (which deletes-and-reinserts,
same as `backfill.py`'s existing pattern). After the upsert, compares
per-table counts. If any guarded table's new count is lower than its old
count, every guarded table for this game is restored to its exact pre-upsert
state (original rows, including original ids) and a WARNING is logged with
before/after counts per table — verified end-to-end against a copy of
`hockey.db` (season=2024 game_id=1): `game_detail`'s reparse was correctly
blocked (`game_goalkeeper_events` 7→1 detected, reverted, final state
unchanged at 7), `game_stats` correctly recognized as unchanged (matches the
known 2026-08-22 hash, zero curated writes), `shotmap` correctly applied (88
shots both before and after, content updated in place) — all three raw-layer
writes (`sync_state`/`raw_responses`) landed regardless of what the guard
did to curated tables. **Compared per-table, not per-team** — an event
moving between the home/away arrays (the `penaltyEvents` finding in 4a) is a
legitimate correction and does not trip the guard.

**The guard counts rows; it cannot see stripped values** (found
2026-09-25). liiga.fi now serves 2015–2024 `game_stats` with the same
number of period rows but time on ice, corsi, faceoffs, power-play lists
and some goals zeroed or null. 3,953 such reparses passed the guard and had
to be restored from backup (`scripts/repair_game_stats.py`,
`docs/RECOVERY_BACKLOG.md`). After any bulk reparse, check
`scripts/recovery_report.py --diff` section 8 (value sums), not just the
row counts. **Open:** `--days 7` in the nightly sync reparses live games'
`game_stats` through the same guard, and a value-level check for it is not
built.

**The guard is all-or-nothing per game** (noted 2026-09-24). One shrinking
table reverts *every* guarded table for that game. So a refetch that
recovers `game_penalty_events` 0→9 while carrying one fewer `game_rosters`
row throws the penalty recovery away. The raw response is still saved. The
one approved exception is `--salvage-from-raw` (`salvage_grow_from_zero`):
no HTTP, reparse the saved raw, accept a table only if it had zero rows for
that game before, and restore everything else. It exists for the 2015–2024
recovery (49 refusals, 32 salvaged). Results are in
`docs/RECOVERY_BACKLOG.md`.

Guarded tables per endpoint (all `(game_id, season)`-scoped
delete-and-reinsert tables — see `resync.py`'s `GUARDED_TABLES`):
`game_detail` → `game_rosters`, `game_penalty_events`,
`game_goalkeeper_events`. `game_stats` → `game_team_period_stats`,
`game_player_period_stats`, `game_goalie_period_stats`,
`game_puck_control`. `shotmap` → `shot_events`. **`players` is deliberately
excluded** — it's a global upsert-by-`player_id` table, not a per-game
delete-and-reinsert, so it can't "shrink" in the sense this guard checks;
letting a player's bio fields update from a corrected fetch is fine
regardless of what happens to this game's event tables.

**Not yet true of `backfill.py`'s own `--force` path** — see the 4b warning
above. The guard only protects `resync.py`'s targeted per-game re-sync.

## Correction to CLAUDE.md's framing

CLAUDE.md's CRITICAL FINDING (2026-08-23) says liiga.fi "retroactively
enriches completed games' responses" — true for `game_stats` and `shotmap`,
**false as a general claim**: `game_detail` can retroactively *lose* data.
Treat "liiga.fi's data for a completed game can still change after original
fetch, in either direction" as the accurate framing going forward.
**[2026-09-25: `game_stats` loses data too. 2015–2024 responses now come
back with period stats stripped; see the guard note in 4c.]**

---

# Nightly live-season sync (added 2026-09-01)

**`scripts/nightly_sync.py` needs to run daily for as long as the season is
underway.** It is not scheduled by this repo — scheduling it is a manual
step, alongside the snapshot job's Windows Task Scheduler entry
(`docs/snapshot_job_task_scheduler.xml`). Unlike the snapshot job, missing a
night is recoverable (the data is still on liiga.fi tomorrow), so it does not
need the same 15-minute cadence or wake-from-sleep care — once a day, after
the night's games have finished, is enough. The `--days 7` resync window
means several consecutive missed nights still get caught up.

    python scripts/nightly_sync.py

Why it's needed at all: **nothing else puts completed live-season games into
`hockey.db`.** `hockey_edge.snapshot.job` writes only to `snapshots.db` (it
reads liiga.fi live and deliberately never touches `hockey.db`), and
`backfill.py` was built as a one-time-per-historical-season pass. Without
this running, `scripts/verify_starters.py` has nothing to score and the
feature store has no current-season rows.

Two passes, both required, for different reasons:

1. **backfill**, with `--force` scoped to `{games_by_season, standings}` and
   `--only-ended` set. The scoped force is **load-bearing, not an
   optimization**: `sync_state` marks those endpoints `success` on first
   fetch and then skips the network forever, so without forcing them the
   pass would never learn that last night's games now have `ended=1` — the
   `games` table would stay frozen at whatever the schedule looked like the
   first time it ran. Verified 2026-09-01: a second unforced run left
   `games_by_season`'s `fetched_at` unchanged, confirming the freeze. Costs
   6 requests/night (5 tournament phases + standings).
2. **resync `--days 7`**, the guarded path from 4c above, which keeps
   correcting a game for a week after it's played.

**`--only-ended` (new flag on `backfill.py`, 2026-09-01) is the live-season
counterpart to that same trap**, in the other direction: a not-yet-played
game returns a pre-game shell (roster, no events), `raw_cache` records it
`success`, and `sync_state` then skips that game forever — so its real
post-game data would never arrive through the backfill path at all, and only
a `resync.py` run happening to fall inside its `--days` window could repair
it. Restricting per-game fetches to `ended=1` games avoids creating that
debt. It also avoids ~45 minutes of pointless traffic per run against 537
fixtures that haven't happened. Default is off, so historical-season
backfills are unaffected.

Per-game endpoints are deliberately **not** force-refetched by pass 1 — a
blind bulk `game_detail` refetch is exactly the unsafe operation the 4b
warning above describes. Pass 2 is the guarded route for re-reading a
completed game.

## How the nightly sync is scheduled — recovery note

**This task has no committed definition.** Unlike the snapshot job (which has
`docs/snapshot_job_task_scheduler.xml`), the nightly sync was registered
inline on 2026-09-02 and exists only in Windows Task Scheduler. These are the
exact commands, recorded so it can be recreated on a rebuild — or translated
to a cron entry / systemd timer on a future Linux host, where only the
schedule and the two paths need substituting.

Register (runs daily at 23:30 local):

```
schtasks /Create /TN "hockey-edge nightly sync" /TR "C:\Users\Nikla_000\Documents\local-repo\hockey-edge\.venv\Scripts\pythonw.exe C:\Users\Nikla_000\Documents\local-repo\hockey-edge\scripts\nightly_sync.py" /SC DAILY /ST 23:30
```

Then set the working directory, which the above cannot do:

```
$t = Get-ScheduledTask -TaskName "hockey-edge nightly sync"
$t.Actions[0].WorkingDirectory = "C:\Users\Nikla_000\Documents\local-repo\hockey-edge"
Set-ScheduledTask -TaskName "hockey-edge nightly sync" -Action $t.Actions
```

Both scheduled tasks run `pythonw.exe` rather than `python.exe` (changed
2026-09-03) so a run does not pop a console window and steal focus. Under
pythonw `sys.stdout`/`sys.stderr` are None, which makes `logs/nightly_sync.log`
the only record of a run; `nightly_sync.py` accounts for that by installing its
console handler only when a stream exists and by logging any unhandled
exception before exiting non-zero.

**Editing the two tasks needs different privileges.** Changing the snapshot
job with `Set-ScheduledTask` requires an elevated PowerShell: its XML
registration carries an explicit `<Principal>` block, and re-registering a task
that specifies a principal is a privileged operation. The nightly sync, created
inline by `schtasks` with no principal of its own, was modifiable from an
ordinary non-elevated prompt.

**The second block is not optional.** `schtasks` has no inline way to set a
working directory, and it matters: `job.py` calls python-dotenv's
`load_dotenv()`, whose `.env` discovery depends on where the process starts.
That mattered from the moment `OddsPapiProvider` replaced `NullOddsProvider`
(2026-09-17): the job now needs `ODDSPAPI_KEY` on every due tick, and a
working directory that hides `.env` would break odds capture outright.
Setting it beforehand meant that swap didn't come with a scheduling bug
attached. Note the data/log paths themselves are *not*
cwd-dependent (they anchor to `Path(__file__).resolve().parents[3]`); `.env`
discovery is the one thing that is.

**Sleep behaviour: a sleeping machine skips that night entirely.** No wake
timer is set (`WakeToRun=False`), and unlike the snapshot job this task also
has `StartWhenAvailable=False`, so a missed 23:30 does not run late on the
next wake — it simply waits for the following night. That is deliberate and
harmless: both passes are resumable and idempotent, so the next successful
run picks up everything the skipped one would have done. The `--days 7`
resync window is the real bound — up to about a week of consecutive missed
nights is still fully recoverable, and beyond that the only loss is
retroactive corrections to games that have aged out of the window, not the
games themselves.

23:30 is chosen to sit after the snapshot job's 11:00–23:00 window (no
overlap, no contention on `hockey.db`) and late enough that a 19:30 puck drop
— the latest regular start on the 2026-27 schedule — has finished and been
marked `ended=1` on liiga.fi's side.
