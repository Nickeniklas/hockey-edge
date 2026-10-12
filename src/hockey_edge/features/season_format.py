"""season_format and team_season: league structure per season.

The format is hand-curated here and checked against `game_spine` on every
build, so a new season, a promoted team or a wrong entry stops the build
instead of passing silently. Games per team are counted, not assumed:
2020-2022 are uneven (docs/FEATURE_AUDIT.md item 3).
"""

import sqlite3

# season: (teams, nominal games per team, has playoffs, relegation)
# relegation: 'none', 'playout_qualification', 'direct_bottom_three', or
# None where the repo's docs and data do not settle it.
SEASON_FORMAT = {
    2015: (14, 60, True, "none"),
    2016: (15, 60, True, "none"),
    2017: (15, 60, True, "none"),
    2018: (15, 60, True, "none"),
    2019: (15, 60, True, "none"),
    2020: (15, 60, False, "none"),  # cut short, no playoffs
    2021: (15, 60, True, "none"),
    2022: (15, 60, True, "none"),
    2023: (15, 60, True, "none"),
    2024: (15, 60, True, "none"),
    2025: (16, 60, True, "playout_qualification"),
    2026: (16, 60, True, None),  # no PLAYOUT/QUALIFICATIONS games; format unconfirmed
    2027: (17, 64, True, "direct_bottom_three"),
}

# (team_id, season). Teams present in the first stored season are not
# first-season cases: their start is unknown.
FIRST_LIIGA_SEASONS = {
    ("461765763:kookoo", 2016),
    ("292293444:jukurit", 2017),
    ("1368624751:k-espoo", 2025),
    ("238306801:jokerit", 2027),
}


class SeasonFormatError(ValueError):
    pass


def build(conn: sqlite3.Connection) -> None:
    team_rows = conn.execute("""
        SELECT season, team_id, MAX(team_name) AS team_name,
               COUNT(*) AS games_scheduled, SUM(ended) AS games_played
        FROM (
            SELECT season, home_team_id AS team_id, home_team_name AS team_name, ended
            FROM game_spine WHERE phase = 'RUNKOSARJA'
            UNION ALL
            SELECT season, away_team_id, away_team_name, ended
            FROM game_spine WHERE phase = 'RUNKOSARJA'
        )
        GROUP BY season, team_id ORDER BY season, team_id
    """).fetchall()
    seasons = sorted({r["season"] for r in team_rows})
    if not seasons:
        raise SeasonFormatError("no RUNKOSARJA games in game_spine")

    first_stored = seasons[0]
    seen: set[str] = set()
    derived_first = set()
    for season in seasons:
        teams = {r["team_id"] for r in team_rows if r["season"] == season}
        if season != first_stored:
            derived_first |= {(team, season) for team in teams - seen}
        seen |= teams
    expected_first = {(t, s) for t, s in FIRST_LIIGA_SEASONS if s in seasons and s != first_stored}
    if derived_first != expected_first:
        raise SeasonFormatError(
            "first Liiga seasons in the data differ from FIRST_LIIGA_SEASONS: "
            f"{sorted(derived_first ^ expected_first)}"
        )

    for season in seasons:
        if season not in SEASON_FORMAT:
            raise SeasonFormatError(f"season {season} has games but no SEASON_FORMAT entry")
        n_teams, games_per_team, has_playoffs, relegation = SEASON_FORMAT[season]
        teams = [r for r in team_rows if r["season"] == season]
        counts = conn.execute(
            "SELECT COUNT(*) AS n, SUM(ended) AS ended FROM game_spine "
            "WHERE season = ? AND phase = 'RUNKOSARJA'", (season,)
        ).fetchone()
        playoff_teams = conn.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT home_team_id FROM game_spine WHERE season = :s AND phase = 'PLAYOFFS' "
            "UNION SELECT away_team_id FROM game_spine WHERE season = :s AND phase = 'PLAYOFFS')",
            {"s": season},
        ).fetchone()[0]
        complete = counts["n"] == counts["ended"]
        most_games = max(r["games_scheduled"] for r in teams)

        if len(teams) != n_teams:
            raise SeasonFormatError(f"season {season}: {len(teams)} teams in games, {n_teams} curated")
        # Only a finished regular season pins the length exactly; a season in
        # progress may have games not yet listed.
        if most_games > games_per_team or (complete and most_games != games_per_team):
            raise SeasonFormatError(
                f"season {season}: a team has {most_games} regular-season games, {games_per_team} curated"
            )
        if complete and has_playoffs != (playoff_teams > 0):
            raise SeasonFormatError(
                f"season {season}: has_playoffs={has_playoffs} but {playoff_teams} teams in PLAYOFFS games"
            )

        conn.execute(
            "INSERT INTO season_format VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (season, n_teams, games_per_team, int(has_playoffs), relegation,
             counts["n"], counts["ended"], playoff_teams or None),
        )
        conn.executemany(
            "INSERT INTO team_season VALUES (?, ?, ?, ?, ?, ?)",
            [(season, r["team_id"], r["team_name"], r["games_scheduled"], r["games_played"],
              int((r["team_id"], season) in derived_first)) for r in teams],
        )
