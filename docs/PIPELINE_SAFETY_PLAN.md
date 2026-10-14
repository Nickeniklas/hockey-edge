# PIPELINE_SAFETY_PLAN — backup and failure alerts

Design chat, 2026-10-14.
Items marked **[DECISION: Niklas]** are the user's call. Steps marked **[Niklas]** are
manual and can't be done by Claude Code. Stop at every "STOP" line and report before
going on.

**Status: S1 built 2026-10-14 (`scripts/backup_data.py`, `docs/BACKUP.md`), first
backup verified; weekly task not yet registered. S2 not started.**

Order: **S1 (backup) → S2 (alerts)**.

---

## Why

- The local `data/` is better than liiga.fi's own copy, and for 2015–2024 period stats
  it is the only good copy. There is no automated off-machine copy.
- The snapshot job missed 2026-10-08 to 10-10 because the PC was off. Nothing alerted,
  and the gap was found three days later. Missed windows are lost for good.

An alert can't recover a missed window. It shortens the gap from days to hours. Only an
always-on machine (the i5 Linux box, open decision 6) prevents gaps.

## Scope

In:
- A weekly backup of `data/hockey.db`, `data/snapshots.db` and `data/raw/` to the
  Google Drive folder, verified, keeping the last four.
- A heartbeat for each scheduled job, through healthchecks.io, so a PC that is off or a
  job that fails sends an email.
- A missed-window alert from the snapshot job.

Out:
- `features.db`, `backtest.db` (rebuild in seconds), `.venv`, logs.
- Moving scheduling to Linux.
- Any change to what the snapshot job or nightly sync capture.

---

## Design answers

**1. Copying SQLite safely.** Use Python's `sqlite3` `Connection.backup()` to copy each
database to a temp folder. It is safe while the snapshot job or nightly sync is writing,
so the backup can run at any time. Never copy the live `.db` files as plain files, and
never point Drive at the live `data/` folder.

**2. The zip.**
- Contents: the two backed-up databases, `data/raw/`, and `data/recovery/` if present.
  **Not** `data/backups/` (old pre-recovery copies) unless S1's size report shows it is
  small. Report the size of each part first.
- Name: `hockey-edge-data-YYYY-MM-DD.zip`.
- Built in a local temp folder, then moved into the Drive folder in one step, so Drive
  never syncs a half-written file.
- Verified before the move: `zipfile.testzip()`, then each database extracted to temp
  and checked with `PRAGMA integrity_check` and a row count of `games` and
  `odds_snapshots` against the source.
- Retention: keep the four newest zips matching the name pattern. Delete older ones
  only if they match the pattern exactly. Never touch other files in the folder.
- Drive folder path from `.env` (`BACKUP_DIR`). If the path doesn't exist, fail loudly.
- The Drive folder is a local folder synced by **Google Drive for desktop** (e.g.
  `G:\My Drive\hockey-edge-backups`). The script only writes a file there; the app
  uploads it in the background. The script can't confirm the upload finished, so the
  PC must stay on for a while after a run (the Sunday 11:05 slot sits inside the
  snapshot job's waking hours).
- Retention must fit the Drive quota (15 GB on a free account, shared with Gmail and
  Photos). Deleted files sit in Drive's trash for 30 days and still count, so plan for
  about `2 × keep × zip size`. S1's size report checks that against the free space and
  proposes a lower `--keep` if needed.

**3. Heartbeats: healthchecks.io.** A dead-man's switch. Each job pings a URL when it
runs. If a ping doesn't arrive on schedule, healthchecks.io sends an alert. This is the
only design that catches a PC that is off, because the alert comes from outside the PC.
Free plan, up to 20 checks. Alerts go to the account email by default. Phone push
(ntfy, Telegram and others) is a setting in its web UI, no code change.
**[DECISION: Niklas]** healthchecks.io (recommended). Alternatives: email from the PC
itself (can't report a PC that is off), Windows notifications (same, and only visible
at the desk).

**4. Checks.** Four, all schedules in `Europe/Helsinki`:

| Check | Pinged by | Schedule (cron) | Grace |
|---|---|---|---|
| `snapshot-job` | every snapshot tick | `*/15 11-22 * * *` | 45 min |
| `missed-windows` | snapshot job, see below | none (fail/success only) | — |
| `nightly-sync` | nightly sync | `0 23 * * *` | 2 h |
| `backup` | backup script | `0 11 * * 0` | 1 day |

- `snapshot-job` alerts on any day the PC is off during 11:00–23:00, game day or not.
  That noise is accepted. Pause the check in the web UI when away on purpose.
- `missed-windows`: after each run's missed-window marking, if any windows became
  missed since the last report, send a **fail** ping whose body lists the season,
  game_id and window of each. The next run with nothing new missed sends a success
  ping. A fail ping alerts immediately.
- Nightly sync and backup send a start ping, then success or fail with the exit
  status. A fail body carries the last error line.

**5. The ping code.** One small module, `src/hockey_edge/ops/heartbeat.py`:
- `ping(check, status="success" | "fail" | "start", body="")`.
- URLs from `.env` (`HC_SNAPSHOT_JOB`, `HC_MISSED_WINDOWS`, `HC_NIGHTLY_SYNC`,
  `HC_BACKUP`). A missing variable is a no-op with one log line, so tests, the Mac and
  dry runs work without it.
- **Never raises.** Timeout 10 s, no retry. A failed ping logs a warning and the job
  carries on. Capture always matters more than the alert.
- **Never logs the URL.** The ping URL works like a password (same lesson as the
  OddsPapi key in `raise_for_status()` text).
- Standard library only (`urllib.request`).

**6. Editing live job code.** The scheduled tasks run from the working checkout, so a
half-finished edit to `job.py` during 11:00–23:00 can break a capture. S2 is built in a
separate git worktree on a branch. The live checkout changes only by a fast-forward
merge after tests pass, at a time with no capture window due in the next 30 minutes
(after the day's last puck drop is simplest). S1 adds a new script only and can be
built in place.

---

## Phase S1 — Backup

Before building, report the sizes of `data/hockey.db`, `data/snapshots.db`,
`data/raw/`, `data/recovery/` and `data/backups/`.

- `scripts/backup_data.py` per design answers 1–2. Options: `--dest` (overrides
  `BACKUP_DIR`), `--keep 4`, `--dry-run` (sizes and plan only). Logs to
  `logs/backup.log`. Exit non-zero on any failure. Leaves a heartbeat hook that S2
  fills in.
- `docs/BACKUP.md`: what is backed up, where, how to restore (one command per line),
  and how to register the weekly task.
- Weekly Windows task: Sunday 11:05, `pythonw.exe`, working directory the repo root,
  `StartWhenAvailable=true`, `WakeToRun=true`. Give the registration command; Niklas
  runs it.

Tests (stdlib unittest, temp folders, zero HTTP):
- Backup of a database while another connection holds a write transaction succeeds
  and passes `integrity_check`.
- Zip contents match the plan; excluded paths are absent.
- Retention keeps the four newest matching files and never deletes a non-matching
  file.
- A missing destination fails loudly.
- A corrupted zip fails verification and is never moved into the destination.

**[Niklas]** Install Google Drive for desktop, sign in, and create a folder for the
backups inside its drive. Put `BACKUP_DIR=<that folder's path>` in `.env`. Then run the
first backup:

```
python scripts/backup_data.py
```

**STOP after S1.** Report: sizes, zip size, runtime, the verification output of the
first real backup, and the registration command.

---

## Phase S2 — Alerts

**[Niklas], before S2 starts:**
1. Create a free healthchecks.io account.
2. Create the four checks from design answer 4, with those schedules, grace times and
   the `Europe/Helsinki` time zone.
3. Copy each check's ping URL into `.env` under the names in design answer 5.
4. Optional: add a phone integration (ntfy or Telegram) in the web UI.

Build in a worktree per design answer 6.

- `src/hockey_edge/ops/heartbeat.py` per design answer 5.
- Snapshot job: a success ping on every tick, and the missed-window report after
  missed-window marking.
- Nightly sync: start ping, then success or fail.
- Backup script: start ping, then success or fail.
- `python -m hockey_edge.ops.heartbeat --test <check>` sends one fail ping and then one
  success ping, so Niklas can confirm the email arrives.
- Update `docs/BACKUP.md`, `docs/RESYNC.md` (nightly sync section) and the snapshot
  job docstring.

Tests (HTTP mocked, zero real requests):
- A ping that raises, times out or returns 500 never breaks the calling job.
- A missing env variable is a no-op.
- The URL never appears in log output, including on failure.
- Missed-window report: newly missed windows send one fail ping with their ids; the
  same windows are not reported twice; a clean run after a fail sends success.
- The snapshot job's existing tests still pass unchanged.

**STOP after S2**, before merging into the live checkout. Report: test count, the diff
summary, and the merge time chosen.

**[Niklas]** after the merge:

```
python -m hockey_edge.ops.heartbeat --test missed-windows
```

Confirm the alert email arrived, and that all four checks show as up after their next
scheduled run.

---

## Decisions for Niklas (collected)

- Heartbeat service: healthchecks.io (recommended).
- Whether `data/backups/` goes into the zip (decided from S1's size report).
