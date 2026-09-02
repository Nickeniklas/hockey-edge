"""Starter-verification check: is the captured starter signal actually right?

hockey_edge.snapshot.lineups infers each team's starting goalie from a
structural signal (`line==1` among the confirmed goalies, see that module's
docstring) because game_detail has no literal "starter" field. That
inference has never been checked against a real result -- this script is
the check, so the assumption is monitored going forward instead of quietly
trusted for months.

Read-only against both databases (data/snapshots.db, data/hockey.db) --
opens each connection in SQLite read-only URI mode so this can never write
to either, even by accident. No DB writes, no HTTP requests.

GOTCHA discovered building this: `game_goalkeeper_events` (the source
literally named when this check was requested) does NOT record who started
-- there is no begin_time=0 row in any of it (checked: zero, across every
season-2026 game), and 217/605 season-2026 games (36%) have ZERO rows in it
at all (a goalie who plays the whole game with no empty-net pull leaves no
trace). It only logs mid-game substitutions/empty-net pulls, not the
opening assignment. The actual ground truth used here is
`game_goalie_period_stats`: whichever goalie has period=1 `shots_on_goal` >
0 for a team is who started (a real period with zero shots faced is not a
realistic outcome), cross-checked against `game_goalkeeper_events` only to
flag -- not silently resolve -- games where a substitution happened in the
first 5 minutes (begin_time < 300s), since an extremely early change could
make the period-1-stats read ambiguous between the true starter and their
early replacement.

Usage:
    python scripts/verify_starters.py
    python scripts/verify_starters.py --season 2027
"""

import argparse
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS_DB = REPO_ROOT / "data" / "snapshots.db"
HOCKEY_DB = REPO_ROOT / "data" / "hockey.db"

EARLY_CHANGE_THRESHOLD_SECONDS = 300  # 5 minutes into period 1


def _readonly_connection(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"{db_path} does not exist")
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _latest_captured_starters(
    snap_conn: sqlite3.Connection, *, season: int | None
) -> list[sqlite3.Row]:
    """One row per (season, game_id, team_role) -- the most recent capture
    (by captured_at) that actually identified a starter (starter_player_id
    NOT NULL). Earlier/emptier captures (opening/mid windows, typically
    still line=null for everyone) are superseded, not blended -- this
    mirrors what a real prediction-time read would use: the freshest
    confirmed capture before puck drop."""
    query = """
        SELECT ls.*
        FROM lineup_snapshots ls
        JOIN (
            SELECT season, game_id, team_role, MAX(captured_at) AS max_captured_at
            FROM lineup_snapshots
            WHERE parsed = 1 AND starter_player_id IS NOT NULL
            {season_filter}
            GROUP BY season, game_id, team_role
        ) latest
        ON latest.season = ls.season AND latest.game_id = ls.game_id
           AND latest.team_role = ls.team_role AND latest.max_captured_at = ls.captured_at
        WHERE ls.parsed = 1 AND ls.starter_player_id IS NOT NULL
    """
    params: tuple = ()
    if season is not None:
        query = query.format(season_filter="AND season = ?")
        params = (season,)
    else:
        query = query.format(season_filter="")
    return snap_conn.execute(query, params).fetchall()


def _team_id_for_role(hockey_conn: sqlite3.Connection, season: int, game_id: int, role: str) -> str | None:
    row = hockey_conn.execute(
        "SELECT home_team_id, away_team_id, ended FROM games WHERE season = ? AND game_id = ?",
        (season, game_id),
    ).fetchone()
    if row is None or not row["ended"]:
        return None
    return row["home_team_id"] if role == "home" else row["away_team_id"]


def _derive_actual_starter(
    hockey_conn: sqlite3.Connection, season: int, game_id: int, team_id: str
) -> tuple[int | None, str]:
    """Returns (actual_starter_player_id_or_None, reason). reason is
    'ok' on a clean single-candidate derivation, or a string explaining why
    it's None (ambiguous / no data / flagged early change) -- never a
    guess."""
    period1 = hockey_conn.execute(
        "SELECT player_id, shots_on_goal, saves FROM game_goalie_period_stats "
        "WHERE season = ? AND game_id = ? AND team_id = ? AND period = 1",
        (season, game_id, team_id),
    ).fetchall()
    if not period1:
        return None, "no_period1_stats"

    candidates = [r["player_id"] for r in period1 if (r["shots_on_goal"] or 0) > 0]
    if len(candidates) == 0:
        candidates = [r["player_id"] for r in period1 if (r["saves"] or 0) > 0]
    if len(candidates) != 1:
        return None, f"ambiguous_period1_stats ({len(candidates)} candidates)"

    starter_id = candidates[0]

    early_change = hockey_conn.execute(
        "SELECT COUNT(*) AS n FROM game_goalkeeper_events "
        "WHERE season = ? AND game_id = ? AND team_id = ? "
        "AND begin_time < ? AND player_id != 0",
        (season, game_id, team_id, EARLY_CHANGE_THRESHOLD_SECONDS),
    ).fetchone()
    if early_change["n"] > 0:
        return None, "flagged_early_change"

    return starter_id, "ok"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, help="restrict to one season (default: all)")
    args = parser.parse_args()

    snap_conn = _readonly_connection(SNAPSHOTS_DB)
    hockey_conn = _readonly_connection(HOCKEY_DB)

    captured = _latest_captured_starters(snap_conn, season=args.season)
    if not captured:
        print("No captured lineup snapshots with an identified starter found in "
              f"{SNAPSHOTS_DB.relative_to(REPO_ROOT)}. Nothing to verify yet.")
        return

    agree = []
    disagree = []
    undetermined = []
    not_ended = []

    for row in captured:
        season, game_id, role = row["season"], row["game_id"], row["team_role"]
        team_id = _team_id_for_role(hockey_conn, season, game_id, role)
        if team_id is None:
            not_ended.append(row)
            continue

        actual_id, reason = _derive_actual_starter(hockey_conn, season, game_id, team_id)
        if actual_id is None:
            undetermined.append((row, reason))
            continue

        if actual_id == row["starter_player_id"]:
            agree.append(row)
        else:
            disagree.append((row, actual_id))

    scored = len(agree) + len(disagree)

    print(f"Captured starter snapshots checked: {len(captured)}")
    print(f"  not yet checkable (game not ended in {HOCKEY_DB.name}, or season not backfilled): {len(not_ended)}")
    print(f"  ground truth undetermined (ambiguous stats / early change / no data): {len(undetermined)}")
    print(f"  scored (clean ground truth available): {scored}")
    if scored:
        rate = len(agree) / scored * 100
        print(f"\n  AGREEMENT RATE: {len(agree)}/{scored} ({rate:.1f}%) -- starter_source="
              f"{captured[0]['starter_source']!r}")
    else:
        print("\n  No games with clean, checkable ground truth yet -- nothing to score.")

    if disagree:
        print(f"\n=== DISAGREEMENTS ({len(disagree)}) ===")
        for row, actual_id in disagree:
            print(f"  season={row['season']} game_id={row['game_id']} team_role={row['team_role']} "
                  f"team={row['team_name']} captured_starter={row['starter_name']!r} "
                  f"(id={row['starter_player_id']}, source={row['starter_source']}) "
                  f"ACTUAL_STARTER_id={actual_id}")

    if undetermined:
        print(f"\n=== UNDETERMINED ({len(undetermined)}) ===")
        for row, reason in undetermined:
            print(f"  season={row['season']} game_id={row['game_id']} team_role={row['team_role']} "
                  f"team={row['team_name']} reason={reason}")

    snap_conn.close()
    hockey_conn.close()


if __name__ == "__main__":
    main()
