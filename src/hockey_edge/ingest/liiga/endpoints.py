"""Catalog of liiga.fi JSON API endpoints — single source of truth for URLs/shapes.

No other module should hardcode a liiga.fi URL; import from here instead.

Each verified endpoint has one real sample response checked into
fixtures/liiga/<name>/<season>.json — one file per verified season, since
historical seasons may use different endpoints or response shapes.

Discovery method (2026-07-12): fetched https://liiga.fi/en/ and grepped the
Vite bundle at /assets/index-BpJxp8Fx.js for axios call sites (the app talks
to `https://<hostname>/api/v2` — i.e. https://liiga.fi/api/v2 in production).
Candidate paths were then curled directly (normal desktop Chrome UA, ~1.5s
between requests, no auth/cookies) to confirm which return JSON. Tournament
values seen in the bundle: "runkosarja" (regular season), "playoffs",
"valmistavat_ottelut" (preseason), "playout", "qualifications".
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Endpoint:
    name: str
    url_template: str
    concept: str
    notes: str
    method: str = "GET"
    verified_seasons: tuple[str, ...] = field(default_factory=tuple)


BASE_URL = "https://liiga.fi/api/v2"


GAMES_BY_SEASON = Endpoint(
    name="games_by_season",
    url_template=BASE_URL + "/games?tournament={tournament}&season={season}",
    concept="games",
    notes="Primary backfill endpoint: full season game list with each game's "
    "homeTeam/awayTeam goalEvents embedded (scorer, assists, gameTime, "
    "period, powerplay/shorthanded flags) and per-team expectedGoals. "
    "Verified back to season=1976 (the league's first season, 1975-76) "
    "through season=2024 (2023-24) — data exists for the whole history at "
    "this one path. expectedGoals is present on recent seasons but absent "
    "(field missing) on season=2000 — take whatever the API gives per "
    "season, don't assume xG exists universally.",
    verified_seasons=("1976", "2000", "2010", "2015", "2024"),
)

GAMES_BY_DATE = Endpoint(
    name="games_by_date",
    url_template=BASE_URL + "/games?tournament={tournament}&date={date}",
    concept="games",
    notes="GOTCHA: `season` is silently ignored on this path — the server "
    "always resolves against whatever it considers the current season, "
    "regardless of the `date` or `season` query params. Passing a date from "
    "a past season (tried 2023-09-12 with and without season=2024, during "
    "the 2026-07 off-season) returned an empty games list plus "
    "previousGameDate/nextGameDate pointing at the *live* season's schedule. "
    "Only useful for the live snapshot job polling today's games — for "
    "historical/backfill use games_by_season instead.",
    verified_seasons=("2026",),
)

GAMES_BY_WEEK = Endpoint(
    name="games_by_week",
    url_template=BASE_URL + "/games?tournament={tournament}&week={week}&season={season}",
    concept="games",
    notes="`week` is a 1-based integer game-week index within the season "
    "(see GAMEWEEKS for the max), not a date — a date string here 500s.",
    verified_seasons=("2024",),
)

SCHEDULE_BY_SEASON = Endpoint(
    name="schedule_by_season",
    url_template=BASE_URL + "/schedule?tournament={tournament}&season={season}",
    concept="games",
    notes="Lighter-weight parallel to games_by_season: flat list of games "
    "with score/xG/spectators/rink but no embedded goalEvents. Useful if "
    "goal-event detail isn't needed for a given pass.",
    verified_seasons=("2015", "2024"),
)

STANDINGS = Endpoint(
    name="standings",
    url_template=BASE_URL + "/standings?season={season}",
    concept="standings",
    notes="Dict keyed by phase: season (regular-season table), playoffs, "
    "playoffsLines, valmistavat_ottelut (preseason), playout, "
    "qualifications. Verified back to season=2000.",
    verified_seasons=("2000", "2024"),
)

GAME_DETAIL = Endpoint(
    name="game_detail",
    url_template=BASE_URL + "/games/{season}/{game_id}",
    concept="lineup",
    notes="Confirmed lineups: top-level homeTeamPlayers/awayTeamPlayers "
    "arrays include every dressed player (role/roleCode, e.g. roleCode "
    "'MV' = goalie) — this is the actual kokoonpanot data, not a separate "
    "endpoint; the liiga.fi '/kokoonpanot' page URL is a client-side route "
    "on top of this same JSON. NOT YET CONFIRMED whether this endpoint "
    "returns partial/no lineup data before puck drop for a future game — "
    "only tested against already-completed historical games (no games were "
    "scheduled at check time, 2026-07 off-season). Verify against a live "
    "pre-game fetch before trusting it for the snapshot job (leakage risk "
    "per hard rule 1). Also accepts ?dataType=pastAndFutureGames (confirmed "
    "working, returns neighboring games) and ?dataType=playersWithStats "
    "(500s in testing — seen in the bundle but couldn't get a working call; "
    "may need a param this session didn't find).",
    verified_seasons=("2010", "2024"),
)

GAME_STATS = Endpoint(
    name="game_stats",
    url_template=BASE_URL + "/games/stats/{season}/{game_id}",
    concept="game_events",
    notes="Per-player, per-period stats (corsi for/against, faceoffs, "
    "shots, TOI, penalty minutes) plus team-level puckStats — this is the "
    "richest source for the team shot-quality feature family. GOTCHA: "
    "confirmed working for season=2024 but returned "
    '{\"stats\": \"Remote server error\"} for season=2010 — this per-game '
    "detail looks like it's only backed for recent seasons. Treat failures "
    "on old game_ids as expected-and-skippable, not a broken endpoint; "
    "sync_state should record the failure per game_id rather than retry "
    "forever.",
    verified_seasons=("2024",),
)

GAME_PREVIEW = Endpoint(
    name="game_preview",
    url_template=BASE_URL
    + "/games/preview/{season}/{game_id}?gameDate={game_date}&homeTeam={home_slug}&awayTeam={away_slug}",
    concept="lineup",
    notes="Requires all three query params (gameDate, homeTeam, awayTeam as "
    "team slugs like 'lukko'/'hpk') or 502s — the bare path alone is not "
    "enough. Returns teamComparison, playersToWatch, goaliesToWatch, "
    "homePreviousGames/awayPreviousGames. 'goaliesToWatch' may be a useful "
    "pre-game starter-goalie signal but isn't a confirmed-lineup guarantee — "
    "same untested pre-puck-drop-timing caveat as GAME_DETAIL applies.",
    verified_seasons=("2024",),
)

SHOTMAP = Endpoint(
    name="shotmap",
    url_template=BASE_URL + "/shotmap/{season}/{game_id}",
    concept="game_events",
    notes="Array of shot events with shotX/shotY coordinates, shooter/"
    "blocker player ids, period, gameTime, eventType. GOTCHA: confirmed "
    "working for season=2024 (88 shots) but 500s ('Remote server error') "
    "for season=2010 — like game_stats, shot-coordinate data looks recent-"
    "seasons-only. Record what's there per season; don't backfill-fail on "
    "this.",
    verified_seasons=("2024",),
)

PLAYER_INFO = Endpoint(
    name="player_info",
    url_template=BASE_URL + "/players/info/{player_id}",
    concept="roster",
    notes="Single player bio + per-team-per-season history keyed by team "
    "slug.",
    verified_seasons=("2024",),
)

PLAYER_LIST = Endpoint(
    name="player_list",
    url_template=BASE_URL
    + "/players/info?tournament={tournament}&fromSeason={from_season}&toSeason={to_season}",
    concept="roster",
    notes="Full player roster list for a season range/tournament; also "
    "accepts optional &nationality=&team= filters (seen in bundle, not "
    "independently curled). Verified for both a recent and an old season "
    "range.",
    verified_seasons=("2010", "2024"),
)

PLAYER_GAMES = Endpoint(
    name="player_games",
    url_template=BASE_URL + "/players/info/{player_id}/games/{season}",
    concept="game_events",
    notes="Per-player game log for one season, split into qualifications/"
    "practice/chl/regular/playout/playoffs arrays, each with full per-game "
    "box-score stats (goals, assists, TOI, saves for goalies via "
    "'goalkeeper' flag, corsi-adjacent fields). Returns all-empty arrays "
    "(not an error) for a season/player combo with no games — confirmed "
    "against a player who wasn't active in 2010.",
    verified_seasons=("2010", "2024"),
)

TEAM_INFO = Endpoint(
    name="team_info",
    url_template=BASE_URL + "/teams/info?team={team_slug}",
    concept="team_stats",
    notes="GOTCHA: takes no effective season parameter — always returns "
    "the full season-by-season franchise history (51 seasons for 'lukko' "
    "in testing) regardless of what's passed. `team` is the lowercase slug "
    "half of a teamId (e.g. teamId '624554857:lukko' -> team=lukko).",
    verified_seasons=("all_seasons",),
)

GAMEWEEKS = Endpoint(
    name="gameweeks",
    url_template=BASE_URL + "/gameweeks?season={season}&tournament={tournament}",
    concept="games",
    notes="Tiny helper: {season, serie, nbWeeks} — the max valid `week` "
    "index for GAMES_BY_WEEK on that season/tournament.",
    verified_seasons=("2010", "2024"),
)

MILESTONES = Endpoint(
    name="milestones",
    url_template=BASE_URL + "/stats/milestones/{tournament}",
    concept="team_stats",
    notes="Career milestone tracker (players approaching round numbers of "
    "games/goals/assists/points) — reflects live state, not season-scoped. "
    "Low priority for the model; not in the planned feature families.",
    verified_seasons=("current",),
)

TOURNAMENT = Endpoint(
    name="tournament",
    url_template=BASE_URL + "/tournament",
    concept="games",
    notes="No parameters; tiny live-state blob ({cachebust, gameWeek, "
    "maxGameWeek}) for whatever tournament/season the site currently "
    "considers active. Low value for this project beyond confirming the "
    "API is reachable.",
    verified_seasons=("current",),
)

TEAMS_STATS = Endpoint(
    name="teams_stats",
    url_template=BASE_URL
    + "/teams/stats?seasonFrom={season_from}&seasonTo={season_to}&tournament={tournament}&dataType={data_type}",
    concept="team_stats",
    notes="UNCONFIRMED — path and query param names come straight from the "
    "bundle (b.get(\"/teams/stats?seasonFrom=...\")) but every combination "
    "tried this session 500'd or 502'd: bare params, dataType in "
    "{summed, regular, team, overall, stats, homeAway, players, teamStats, "
    "basic, all}, a wide seasonFrom/seasonTo range, and an uppercase "
    "tournament value — roughly 14 requests total, all rate-limited. "
    "Either a required param/value this session didn't guess, or the "
    "upstream is presently broken independent of what we send. NEEDS "
    "DEVTOOLS: open https://liiga.fi/en/stats (team stats tab), change a "
    "filter, and capture the actual request URL + response.",
    verified_seasons=(),
)


ENDPOINTS = {
    e.name: e
    for e in [
        GAMES_BY_SEASON,
        GAMES_BY_DATE,
        GAMES_BY_WEEK,
        SCHEDULE_BY_SEASON,
        STANDINGS,
        GAME_DETAIL,
        GAME_STATS,
        GAME_PREVIEW,
        SHOTMAP,
        PLAYER_INFO,
        PLAYER_LIST,
        PLAYER_GAMES,
        TEAM_INFO,
        GAMEWEEKS,
        MILESTONES,
        TOURNAMENT,
        TEAMS_STATS,
    ]
}
