"""game_spine: one row per game with its flags and the regulation 1X2 target.

Goals come from the final score in `games`, never from
`game_team_period_stats` (stripped for 2025/2026) or goal-event counts.
"""

import sqlite3

COMPETITIVE_PHASES = ("RUNKOSARJA", "PLAYOFFS", "PLAYOUT", "QUALIFICATIONS")
EVAL_PHASE = "RUNKOSARJA"

END_TYPES = {
    "ENDED_DURING_REGULAR_GAME_TIME": "REG",
    "ENDED_DURING_EXTENDED_GAME_TIME": "OT",
    "ENDED_DURING_WINNING_SHOT_COMPETITION": "SO",
}

# Competitive games that must not move a rating, as (season, game_id): reason.
# The reason is stored in game_spine.ratings_exclusion_reason.
RATINGS_EXCLUSIONS = {
    (2022, 49298): "sudden-death tiebreak after a two-game aggregate series, 35 minutes",
}


def regulation_result(end_type: str, home_goals: int, away_goals: int) -> tuple[str, int]:
    """(H/D/A, end_type_anomaly). Overtime and shootout games are draws.

    A regular-time game with a tied final is a draw with the anomaly flag;
    the only known case is 2022:49294 (docs/FEATURE_AUDIT.md item 1).
    """
    if end_type != "REG":
        return "D", 0
    if home_goals > away_goals:
        return "H", 0
    if away_goals > home_goals:
        return "A", 0
    return "D", 1


def build(conn: sqlite3.Connection) -> None:
    # Team ids, never names: foreign club names are not stable.
    liiga_teams = {
        (row["season"], row["team_id"])
        for row in conn.execute(
            "SELECT season, home_team_id AS team_id FROM hockey.games WHERE phase = 'RUNKOSARJA' "
            "UNION SELECT season, away_team_id FROM hockey.games WHERE phase = 'RUNKOSARJA'"
        )
    }
    rows = []
    for g in conn.execute(
        "SELECT game_id, season, start_utc, phase, home_team_id, home_team_name, "
        "away_team_id, away_team_name, ended, home_goals, away_goals, finished_type "
        "FROM hockey.games ORDER BY start_utc, season, game_id"
    ):
        home_goals = away_goals = end_type = result = None
        anomaly = 0
        if g["ended"]:
            if g["finished_type"] not in END_TYPES or g["home_goals"] is None or g["away_goals"] is None:
                raise ValueError(
                    f"ended game {g['season']}:{g['game_id']} has finished_type="
                    f"{g['finished_type']!r}, score {g['home_goals']}-{g['away_goals']}"
                )
            home_goals, away_goals = g["home_goals"], g["away_goals"]
            end_type = END_TYPES[g["finished_type"]]
            result, anomaly = regulation_result(end_type, home_goals, away_goals)
        competitive = g["phase"] in COMPETITIVE_PHASES
        non_liiga = (
            (g["season"], g["home_team_id"]) not in liiga_teams
            or (g["season"], g["away_team_id"]) not in liiga_teams
        )
        exclusion = RATINGS_EXCLUSIONS.get((g["season"], g["game_id"]))
        rows.append((
            g["game_id"], g["season"], g["start_utc"], g["start_utc"][:10], g["phase"],
            g["home_team_id"], g["home_team_name"], g["away_team_id"], g["away_team_name"],
            g["ended"], home_goals, away_goals, end_type, result, anomaly,
            int(competitive), int(non_liiga),
            int(competitive and not non_liiga and exclusion is None),
            int(g["phase"] == EVAL_PHASE and not non_liiga),
            exclusion,
        ))
    conn.executemany(
        "INSERT INTO game_spine VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
