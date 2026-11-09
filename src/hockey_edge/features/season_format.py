"""season_format and team_season: league structure per season.

The format is hand-curated here and checked against `game_spine` on every
build, so a new season, a promoted team or a wrong entry stops the build
instead of passing silently. Games per team are counted, not assumed:
2020-2022 are uneven (docs/FEATURE_AUDIT.md item 3).
"""

import sqlite3

# season: (teams, nominal games per team, has playoffs, playoff spots,
# relegation, relegation spots).
# playoff spots: the top N of the regular-season table go to the playoffs
# (wildcard round included). Checked against the teams in PLAYOFFS games
# for every finished season that had playoffs.
# relegation: 'none', 'playout_qualification' (the bottom N play the
# playout, its loser the qualification series against the Mestis
# champion) or 'direct_bottom_three' (the bottom N go down directly);
# relegation spots is that N, 0 for 'none'.
SEASON_FORMAT = {
    2015: (14, 60, True, 10, "none", 0),
    2016: (15, 60, True, 10, "none", 0),
    2017: (15, 60, True, 10, "none", 0),
    2018: (15, 60, True, 10, "none", 0),
    2019: (15, 60, True, 10, "none", 0),
    # Cut short in March 2020, playoffs cancelled. The planned top-10 line
    # is kept: it was the line teams played for until the cancellation.
    2020: (15, 60, False, 10, "none", 0),
    2021: (15, 60, True, 10, "none", 0),
    2022: (15, 60, True, 10, "none", 0),
    2023: (15, 60, True, 10, "none", 0),
    2024: (15, 60, True, 10, "none", 0),
    2025: (16, 60, True, 12, "playout_qualification", 2),  # places 15-16 to the playout
    2026: (16, 60, True, 12, "none", 0),  # no relegation from Liiga (Niklas, 2026-11-09)
    # Top 12 of 17 to the playoffs, bottom three (15-17) relegated directly
    # (confirmed by Niklas from MTV Uutiset's season preview, 2026-08-31).
    2027: (17, 64, True, 12, "direct_bottom_three", 3),
}
RELEGATION_SPOTS = {"none": 0, "playout_qualification": 2, "direct_bottom_three": 3}

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


def season_teams(conn: sqlite3.Connection, season: int) -> list[str]:
    """The season's teams, from team_season (never from games seen so far)."""
    return [r[0] for r in conn.execute("SELECT team_id FROM team_season WHERE season = ? ORDER BY team_id", (season,))]


def season_row(conn: sqlite3.Connection, season: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM season_format WHERE season = ?", (season,)).fetchone()


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
        n_teams, games_per_team, has_playoffs, playoff_spots, relegation, relegation_spots = SEASON_FORMAT[season]
        if RELEGATION_SPOTS.get(relegation) != relegation_spots:
            raise SeasonFormatError(f"season {season}: relegation {relegation!r} with {relegation_spots} spots")
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
        # Playoff games are listed round by round (the wildcard round first),
        # so a season's count reaches its spots only once a later season
        # exists; before that it may only fall short.
        finished = season < seasons[-1]
        if playoff_teams > playoff_spots or (finished and playoff_teams and playoff_teams != playoff_spots):
            raise SeasonFormatError(
                f"season {season}: {playoff_teams} teams in PLAYOFFS games, {playoff_spots} spots curated"
            )

        conn.execute(
            "INSERT INTO season_format VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (season, n_teams, games_per_team, int(has_playoffs), relegation,
             counts["n"], counts["ended"], playoff_teams or None, playoff_spots, relegation_spots),
        )
        conn.executemany(
            "INSERT INTO team_season VALUES (?, ?, ?, ?, ?, ?)",
            [(season, r["team_id"], r["team_name"], r["games_scheduled"], r["games_played"],
              int((r["team_id"], season) in derived_first)) for r in teams],
        )
