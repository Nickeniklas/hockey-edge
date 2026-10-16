# BACKUP — weekly off-machine copy of data/

Built 2026-10-14, phase S1 of `docs/PIPELINE_SAFETY_PLAN.md`. Script:
`scripts/backup_data.py` (its docstring has the step-by-step detail).

## What and where

One zip a week, `hockey-edge-data-YYYY-MM-DD.zip`, in `BACKUP_DIR` from `.env`:
`D:\Users\Nikla_000\DATA\hockey-edge-backups`, a folder that Google Drive for
desktop syncs. The script writes the file; Drive uploads it in the background.
The script cannot tell when the upload is done, so leave the PC on for a while
after a run.

| In the zip | How |
|---|---|
| `data/hockey.db` | SQLite online backup (`Connection.backup()`), never a file copy |
| `data/snapshots.db` | the same |
| `data/raw/` | every file |
| `data/recovery/` | every file, when the folder exists |

Left out: `data/backups/` (1.2 GB of old pre-recovery copies), `features.db`
and `backtest.db` (rebuilt in seconds), `snapshots.pre-kind-migration.db`,
`.venv`, `logs/`.

Sizes at the first run, 2026-10-14:

| Part | Size |
|---|---|
| `hockey.db` | 246.0 MB |
| `snapshots.db` | 105.9 MB |
| `raw/` | 2,182.2 MB (28,340 files) |
| `recovery/` | 8.8 MB |
| `backups/` (excluded) | 1,217.9 MB |
| **zip** | **195.6 MB** (28,364 members), built and verified in 377 s |

Quota estimate at `--keep 4`: about 1.6 GB.

Arcnames are repo-relative (`data/hockey.db`, `data/raw/...`), so the zip
unpacks straight into the repo root.

## Safety rules the script follows

- Only a verified zip reaches the Drive folder. It is built in a local temp
  folder and checked there: `testzip()`, the member list against the plan,
  then each database extracted again with `PRAGMA integrity_check` and a row
  count (`games`, `odds_snapshots`) equal to the copy's.
- It goes into the Drive folder under a hidden `.partial` name, then is renamed.
  The final name never points at a half-written file.
- Retention keeps the `--keep` newest files (default 4) whose name fully
  matches `hockey-edge-data-YYYY-MM-DD.zip`. Nothing else in the folder is
  touched. A `.partial` left by a crashed run is logged, not deleted.
- A missing destination, a missing database or any failed check exits 1.

**Drive quota.** Deleted files sit in Drive's trash for 30 days and still count,
so plan for about `2 × keep × zip size`. Each run logs that estimate.

## Commands

```
python scripts/backup_data.py --dry-run
python scripts/backup_data.py
python scripts/backup_data.py --keep 3
python scripts/backup_data.py --dest D:\some\other\folder
```

Log: `logs/backup.log`.

## Restore

From the repo root in PowerShell, venv active. Nothing is deleted: the current
`data/` is set aside first. Pick the zip to restore in the first line.

```
$zip = "D:\Users\Nikla_000\DATA\hockey-edge-backups\hockey-edge-data-2026-10-14.zip"
Disable-ScheduledTask -TaskName "hockey-edge snapshot job"
Disable-ScheduledTask -TaskName "hockey-edge nightly sync"
Rename-Item data data.before-restore
python -m zipfile -e $zip .
python -c "import sqlite3; [print(f, sqlite3.connect(f'data/{f}').execute('PRAGMA integrity_check').fetchone()[0]) for f in ('hockey.db', 'snapshots.db')]"
Move-Item data.before-restore\backups data\backups
python -m hockey_edge.features.build
Enable-ScheduledTask -TaskName "hockey-edge snapshot job"
Enable-ScheduledTask -TaskName "hockey-edge nightly sync"
```

`Disable-ScheduledTask` on the snapshot job needs an elevated prompt (its
registration carries a `<Principal>`; see `docs/RESYNC.md`). The `Move-Item`
line brings back the old backup copies, which are not in the zip. Delete
`data.before-restore` yourself once the restore checks out.

## The weekly task

Sunday 11:05 local, `pythonw.exe`, working directory the repo root,
`StartWhenAvailable` and `WakeToRun` on, no second instance, 2 h limit. 11:05
sits inside the snapshot job's waking hours, so the PC is normally up long
enough afterwards for Drive to upload. Register from an ordinary PowerShell 7
prompt (no elevation needed, since no principal is set):

```
$repo = "C:\Users\Nikla_000\Documents\local-repo\hockey-edge"
$action = New-ScheduledTaskAction -Execute "$repo\.venv\Scripts\pythonw.exe" -Argument "`"$repo\scripts\backup_data.py`"" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 11:05
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "hockey-edge backup" -Action $action -Trigger $trigger -Settings $settings
```

Check it afterwards:

```
Get-ScheduledTaskInfo -TaskName "hockey-edge backup"
```

Under `pythonw` there is no console, so a failure before logging starts shows
only as a non-zero Last Run Result there. `WakeToRun` needs wake timers allowed
in the power plan, and only wakes a sleeping PC, not one that is off.

## Heartbeat

Added in phase S2. A real run pings the healthchecks.io `backup` check
(`HC_BACKUP` in `.env`): start, then success, or fail with the error message.
The check expects a ping every Sunday 11:00 with a 1-day grace, so a week
without a backup, a PC that is off all Sunday, or a failed run sends an email.
`--dry-run` sends nothing. A ping never stops the backup; see
`src/hockey_edge/ops/heartbeat.py`.
