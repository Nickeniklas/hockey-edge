"""Parse raw liiga.fi JSON (already cached to disk by raw_cache.fetch) into
curated table rows and write them to hockey.db.

Every `upsert_*` function deletes existing rows for the entity it's given
before reinserting — curated tables are derived and rebuildable, per
docs/SCHEMA_DRAFT.md's design principles. Re-running a parse (e.g. after
fixing a bug here) never needs a refetch, only a re-run against the cached
raw file.

Field access is defensive (`.get(...)`) throughout: fixtures are trimmed real
responses and some fields are present in one season's sample and absent in
another's (see SCHEMA_DRAFT.md's "fixtures are trimmed" caveat) — this parser
never assumes a field that's missing in one payload will always be missing,
nor that a field present once is always present.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bool_to_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(bool(value))


def _list_count(value: Any) -> int | None:
    """powerPlayInstances/shortHandedInstances arrive as a list of instance
    codes (e.g. ["B", "F"]), not a count — this table stores the count."""
    if value is None:
        return None
    if isinstance(value, list):
        return len(value)
    return value


# ---------------------------------------------------------------------------
# games + game_goal_events  (source: games_by_season)
# ---------------------------------------------------------------------------


def parse_games_by_season(
    games: list[dict], season: int, raw_response_id: int
) -> tuple[list[tuple], list[tuple]]:
    game_rows = []
    goal_rows = []
    for g in games:
        home = g.get("homeTeam") or {}
        away = g.get("awayTeam") or {}
        rink = g.get("iceRink") or {}
        game_id = g["id"]

        game_rows.append(
            (
                game_id,
                season,
                g.get("serie"),
                g.get("start"),
                g.get("end"),
                home.get("teamId"),
                home.get("teamName"),
                home.get("goals"),
                away.get("teamId"),
                away.get("teamName"),
                away.get("goals"),
                home.get("expectedGoals"),
                away.get("expectedGoals"),
                g.get("gameTime"),
                _bool_to_int(g.get("started")),
                _bool_to_int(g.get("ended")),
                g.get("finishedType"),
                g.get("spectators"),
                g.get("gameWeek"),
                g.get("playOffPair"),
                g.get("playOffPhase"),
                g.get("playOffReqWins"),
                rink.get("name"),
                rink.get("city"),
                raw_response_id,
            )
        )

        for side_team, events in (
            (home.get("teamId"), home.get("goalEvents") or []),
            (away.get("teamId"), away.get("goalEvents") or []),
        ):
            for ev in events:
                scorer = ev.get("scorerPlayer") or {}
                goal_rows.append(
                    (
                        game_id,
                        side_team,
                        ev.get("eventId"),
                        ev.get("scorerPlayerId"),
                        scorer.get("firstName"),
                        scorer.get("lastName"),
                        ev.get("period"),
                        ev.get("gameTime"),
                        ev.get("logTime"),
                        json.dumps(ev.get("goalTypes") or []),
                        json.dumps(ev.get("assistantPlayerIds") or []),
                        ev.get("homeTeamScore"),
                        ev.get("awayTeamScore"),
                        _bool_to_int(ev.get("winningGoal")),
                    )
                )
    return game_rows, goal_rows


def upsert_games_for_season(
    conn: sqlite3.Connection, season: int, game_rows: list[tuple], goal_rows: list[tuple]
) -> None:
    """Upserts `games` by game_id (never deletes) — a blanket delete-by-season
    would violate the FK from every other per-game table (game_rosters,
    game_*_stats, shot_events, ...) that other endpoints have already written
    against these game_ids. `game_goal_events` IS solely owned by this parse
    (games_by_season is its only source), so it's fine to delete-and-reinsert
    scoped to just the game_ids in this batch."""
    conn.executemany(
        "INSERT INTO games (game_id, season, phase, start_utc, end_utc, home_team_id, "
        "home_team_name, home_goals, away_team_id, away_team_name, away_goals, "
        "home_expected_goals, away_expected_goals, game_time_seconds, started, ended, "
        "finished_type, spectators, game_week, play_off_pair, play_off_phase, "
        "play_off_req_wins, rink_name, rink_city, source_raw_response_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (game_id) DO UPDATE SET "
        "season=excluded.season, phase=excluded.phase, start_utc=excluded.start_utc, "
        "end_utc=excluded.end_utc, home_team_id=excluded.home_team_id, "
        "home_team_name=excluded.home_team_name, home_goals=excluded.home_goals, "
        "away_team_id=excluded.away_team_id, away_team_name=excluded.away_team_name, "
        "away_goals=excluded.away_goals, home_expected_goals=excluded.home_expected_goals, "
        "away_expected_goals=excluded.away_expected_goals, game_time_seconds=excluded.game_time_seconds, "
        "started=excluded.started, ended=excluded.ended, finished_type=excluded.finished_type, "
        "spectators=excluded.spectators, game_week=excluded.game_week, "
        "play_off_pair=excluded.play_off_pair, play_off_phase=excluded.play_off_phase, "
        "play_off_req_wins=excluded.play_off_req_wins, rink_name=excluded.rink_name, "
        "rink_city=excluded.rink_city, source_raw_response_id=excluded.source_raw_response_id",
        game_rows,
    )

    game_ids = [row[0] for row in game_rows]
    if game_ids:
        placeholders = ",".join("?" * len(game_ids))
        conn.execute(f"DELETE FROM game_goal_events WHERE game_id IN ({placeholders})", game_ids)
    if goal_rows:
        conn.executemany(
            "INSERT OR IGNORE INTO game_goal_events (game_id, team_id, event_id, "
            "scorer_player_id, scorer_first_name, scorer_last_name, period, "
            "game_time_seconds, log_time_utc, goal_types, assistant_player_ids, "
            "home_score_after, away_score_after, winning_goal) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            goal_rows,
        )
    conn.commit()


# ---------------------------------------------------------------------------
# game_rosters + players + game_penalty_events + game_goalkeeper_events
# (source: game_detail)
# ---------------------------------------------------------------------------


def parse_game_detail(data: dict, game_id: int) -> dict[str, list[tuple]]:
    game = data.get("game") or {}
    home = game.get("homeTeam") or {}
    away = game.get("awayTeam") or {}

    roster_rows = []
    player_rows = []
    for players in (data.get("homeTeamPlayers") or [], data.get("awayTeamPlayers") or []):
        for p in players:
            team_id = p.get("teamId")
            player_id = p.get("id")
            roster_rows.append(
                (
                    game_id,
                    team_id,
                    player_id,
                    p.get("role"),
                    p.get("roleCode"),
                    p.get("jersey"),
                    _bool_to_int(p.get("captain")),
                    _bool_to_int(p.get("alternateCaptain")),
                    _bool_to_int(p.get("rookie")),
                    _bool_to_int(p.get("injured")),
                    _bool_to_int(p.get("suspended")),
                    _bool_to_int(p.get("removed")),
                )
            )
            player_rows.append(
                (
                    player_id,
                    p.get("firstName"),
                    p.get("lastName"),
                    p.get("handedness"),
                    p.get("height"),
                    p.get("weight"),
                    p.get("countryOfBirth"),
                    p.get("nationality"),
                    p.get("dateOfBirth"),
                    team_id,
                    _now_iso(),
                )
            )

    penalty_rows = []
    goalkeeper_rows = []
    for team_id, team in ((home.get("teamId"), home), (away.get("teamId"), away)):
        for ev in team.get("penaltyEvents") or []:
            penalty_rows.append(
                (
                    game_id,
                    team_id,
                    ev.get("eventId"),
                    ev.get("playerId"),
                    ev.get("suffererPlayerId"),
                    ev.get("period"),
                    ev.get("gameTime"),
                    ev.get("penaltyBegintime"),
                    ev.get("penaltyEndtime"),
                    ev.get("penaltyFaultName"),
                    ev.get("penaltyFaultType"),
                    ev.get("penaltyMinutes"),
                )
            )
        for ev in team.get("goalKeeperEvents") or []:
            goalkeeper_rows.append(
                (
                    game_id,
                    team_id,
                    ev.get("eventId"),
                    ev.get("playerId"),
                    ev.get("period"),
                    ev.get("gameTime"),
                    ev.get("beginTime"),
                    ev.get("endTime"),
                    ev.get("emptyNet"),
                )
            )

    return {
        "rosters": roster_rows,
        "players": player_rows,
        "penalties": penalty_rows,
        "goalkeeper_events": goalkeeper_rows,
    }


def upsert_game_detail(conn: sqlite3.Connection, game_id: int, parsed: dict[str, list[tuple]]) -> None:
    conn.execute("DELETE FROM game_rosters WHERE game_id = ?", (game_id,))
    conn.execute("DELETE FROM game_penalty_events WHERE game_id = ?", (game_id,))
    conn.execute("DELETE FROM game_goalkeeper_events WHERE game_id = ?", (game_id,))

    if parsed["rosters"]:
        conn.executemany(
            "INSERT INTO game_rosters (game_id, team_id, player_id, role, role_code, "
            "jersey, captain, alternate_captain, rookie, injured, suspended, removed) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            parsed["rosters"],
        )
    if parsed["players"]:
        conn.executemany(
            "INSERT INTO players (player_id, first_name, last_name, handedness, height, "
            "weight, country_of_birth, nationality, date_of_birth, last_seen_team_id, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (player_id) DO UPDATE SET "
            "first_name=excluded.first_name, last_name=excluded.last_name, "
            "handedness=excluded.handedness, height=excluded.height, weight=excluded.weight, "
            "country_of_birth=excluded.country_of_birth, nationality=excluded.nationality, "
            "date_of_birth=excluded.date_of_birth, last_seen_team_id=excluded.last_seen_team_id, "
            "updated_at=excluded.updated_at",
            parsed["players"],
        )
    if parsed["penalties"]:
        conn.executemany(
            "INSERT OR IGNORE INTO game_penalty_events (game_id, team_id, event_id, "
            "player_id, sufferer_player_id, period, game_time_seconds, penalty_begin_time, "
            "penalty_end_time, fault_name, fault_type, penalty_minutes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            parsed["penalties"],
        )
    if parsed["goalkeeper_events"]:
        conn.executemany(
            "INSERT OR IGNORE INTO game_goalkeeper_events (game_id, team_id, event_id, "
            "player_id, period, game_time_seconds, begin_time, end_time, empty_net) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            parsed["goalkeeper_events"],
        )
    conn.commit()


# ---------------------------------------------------------------------------
# game_team_period_stats / game_player_period_stats / game_goalie_period_stats
# / game_puck_control  (source: game_stats — recent-seasons-only)
# ---------------------------------------------------------------------------


def parse_game_stats(data: dict, game_id: int) -> dict[str, list[tuple]]:
    team_rows = []
    player_rows = []
    goalie_rows = []

    for team_list in (data.get("homeTeam") or [], data.get("awayTeam") or []):
        for team_period in team_list:
            team_id = team_period.get("teamId")
            period = team_period.get("period")
            team_rows.append(
                (
                    game_id,
                    team_id,
                    period,
                    team_period.get("goals"),
                    team_period.get("shots"),
                    _list_count(team_period.get("powerPlayInstances")),
                    team_period.get("powerPlayGoals"),
                    _list_count(team_period.get("shortHandedInstances")),
                    team_period.get("shortHandedGoalsAgainst"),
                    team_period.get("penaltyMinutes"),
                    team_period.get("faceOffWins"),
                    team_period.get("totalDistanceTravelled"),
                )
            )
            for ps in team_period.get("periodPlayerStats") or []:
                stat = ps.get("period") or {}
                player_rows.append(
                    (
                        game_id,
                        team_id,
                        ps.get("playerId"),
                        ps.get("jerseyId"),
                        stat.get("period", period),
                        stat.get("goals"),
                        stat.get("assists"),
                        stat.get("points"),
                        stat.get("plusminus"),
                        stat.get("shots"),
                        stat.get("penaltyminutes"),
                        stat.get("powerplayGoals"),
                        stat.get("shortHandedGoals"),
                        stat.get("blockedShots"),
                        stat.get("faceoffsTotal"),
                        stat.get("faceoffsWon"),
                        stat.get("corsiFor"),
                        stat.get("corsiAgainst"),
                        stat.get("timeofice"),
                        ps.get("distance"),
                        ps.get("expectedGoalsPlayer"),
                        ps.get("expectedGoalsAgainst"),
                    )
                )
            for gs in team_period.get("goaliePeriodStats") or []:
                stat = gs.get("period") or {}
                goalie_rows.append(
                    (
                        game_id,
                        team_id,
                        gs.get("playerId"),
                        gs.get("jerseyId"),
                        stat.get("period", period),
                        stat.get("shotsOnGoal"),
                        stat.get("saves"),
                        stat.get("goalsAllowed"),
                        stat.get("savesPercentage"),
                        stat.get("timeofice"),
                    )
                )

    puck_rows = [
        (
            game_id,
            ps.get("periodNumber"),
            ps.get("homeTeamControlDuration"),
            ps.get("awayTeamControlDuration"),
            ps.get("contestedControlDuration"),
        )
        for ps in data.get("puckStats") or []
    ]

    return {"team": team_rows, "player": player_rows, "goalie": goalie_rows, "puck": puck_rows}


def upsert_game_stats(conn: sqlite3.Connection, game_id: int, parsed: dict[str, list[tuple]]) -> None:
    conn.execute("DELETE FROM game_team_period_stats WHERE game_id = ?", (game_id,))
    conn.execute("DELETE FROM game_player_period_stats WHERE game_id = ?", (game_id,))
    conn.execute("DELETE FROM game_goalie_period_stats WHERE game_id = ?", (game_id,))
    conn.execute("DELETE FROM game_puck_control WHERE game_id = ?", (game_id,))

    if parsed["team"]:
        conn.executemany(
            "INSERT OR IGNORE INTO game_team_period_stats (game_id, team_id, period, goals, "
            "shots, powerplay_instances, powerplay_goals, shorthanded_instances, "
            "shorthanded_goals_against, penalty_minutes, face_off_wins, total_distance_travelled) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            parsed["team"],
        )
    if parsed["player"]:
        conn.executemany(
            "INSERT OR IGNORE INTO game_player_period_stats (game_id, team_id, player_id, "
            "jersey_id, period, goals, assists, points, plusminus, shots, penalty_minutes, "
            "powerplay_goals, shorthanded_goals, blocked_shots, faceoffs_total, faceoffs_won, "
            "corsi_for, corsi_against, time_on_ice_seconds, distance, expected_goals_player, "
            "expected_goals_against) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            parsed["player"],
        )
    if parsed["goalie"]:
        conn.executemany(
            "INSERT OR IGNORE INTO game_goalie_period_stats (game_id, team_id, player_id, "
            "jersey_id, period, shots_on_goal, saves, goals_allowed, save_percentage, "
            "time_on_ice_seconds) VALUES (?,?,?,?,?,?,?,?,?,?)",
            parsed["goalie"],
        )
    if parsed["puck"]:
        conn.executemany(
            "INSERT OR IGNORE INTO game_puck_control (game_id, period, home_control_seconds, "
            "away_control_seconds, contested_control_seconds) VALUES (?,?,?,?,?)",
            parsed["puck"],
        )
    conn.commit()


# ---------------------------------------------------------------------------
# shot_events  (source: shotmap — recent-seasons-only)
# ---------------------------------------------------------------------------


def parse_shotmap(data: list[dict], game_id: int) -> list[tuple]:
    return [
        (
            game_id,
            ev.get("period"),
            ev.get("gameTime"),
            ev.get("shootingTeamId"),
            ev.get("shooterId"),
            ev.get("blockerId"),
            ev.get("shotX"),
            ev.get("shotY"),
            ev.get("eventType"),
            ev.get("type"),
            ev.get("ownTeamPlayersOnIce"),
            ev.get("otherTeamPlayersOnIce"),
        )
        for ev in data
    ]


def upsert_shot_events(conn: sqlite3.Connection, game_id: int, rows: list[tuple]) -> None:
    conn.execute("DELETE FROM shot_events WHERE game_id = ?", (game_id,))
    if rows:
        conn.executemany(
            "INSERT INTO shot_events (game_id, period, game_time_seconds, shooting_team_id, "
            "shooter_player_id, blocker_player_id, shot_x, shot_y, event_type, strength_type, "
            "own_team_players_on_ice, other_team_players_on_ice) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    conn.commit()


# ---------------------------------------------------------------------------
# standings
# ---------------------------------------------------------------------------

STANDINGS_PHASES = ("season", "playoffs", "playout", "qualifications", "valmistavat_ottelut")


def parse_standings(data: dict, season: int) -> list[tuple]:
    rows = []
    for phase in STANDINGS_PHASES:
        for team in data.get(phase) or []:
            rows.append(
                (
                    season,
                    phase,
                    team.get("teamId"),
                    team.get("teamName"),
                    team.get("ranking"),
                    team.get("games"),
                    team.get("wins"),
                    team.get("losses"),
                    team.get("ties"),
                    team.get("overtimeWins"),
                    team.get("overtimeLosses"),
                    team.get("points"),
                    team.get("goals"),
                    team.get("goalsAgainst"),
                    team.get("powerPlayInstances"),
                    team.get("powerPlayGoals"),
                    _to_float(team.get("powerPlayPercentage")),
                    team.get("shortHandedInstances"),
                    team.get("shortHandedGoalsAgainst"),
                    _to_float(team.get("shortHandedPercentage")),
                    team.get("penaltyMinutes"),
                    team.get("distance"),
                    team.get("pointsPerGame"),
                    _to_float(team.get("winPercentage")),
                )
            )
    return rows


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def upsert_standings(conn: sqlite3.Connection, season: int, rows: list[tuple]) -> None:
    conn.execute("DELETE FROM standings WHERE season = ?", (season,))
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO standings (season, phase, team_id, team_name, ranking, "
            "games, wins, losses, ties, overtime_wins, overtime_losses, points, goals, "
            "goals_against, powerplay_instances, powerplay_goals, powerplay_percentage, "
            "shorthanded_instances, shorthanded_goals_against, shorthanded_percentage, "
            "penalty_minutes, distance, points_per_game, win_percentage) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    conn.commit()
