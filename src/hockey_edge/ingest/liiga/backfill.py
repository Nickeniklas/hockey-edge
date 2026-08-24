"""Resumable Liiga historical backfill — build-order step 1.

Usage (from repo root, venv active — src/hockey_edge is pip-install-e'd, see
README.md's Setup section):

    python -m hockey_edge.ingest.liiga.backfill --season 2024

Idempotent by design: every fetch goes through raw_cache.fetch(), which skips
the network call entirely for anything already recorded 'success' (or
'failed_permanent' for the recent-seasons-only endpoints) in sync_state — a
second run of this exact command touches the network only for what's new or
previously failed retryably. Pass --force to refetch everything regardless,
or --force --endpoints game_stats to scope the refetch to just one (or a
comma-separated few) of {games_by_season,standings,game_detail,game_stats,
shotmap} — endpoints not named still use normal cache-first behavior.
Endpoint scoping matters because --force used to triple the traffic of any
targeted recovery for no evidenced benefit (all 5 endpoints refetched
regardless of which one actually needed it) — see docs/RESYNC.md for the
finding that motivated this (2026-08-24: game_detail, game_stats, and
shotmap ALL mutate on refetch for a completed historical game, not just
game_stats as originally assumed, and NOT always in the direction of "more
data" — game_detail can lose event data on refetch). **Do not bulk-refetch
game_detail against historical seasons** — see docs/RESYNC.md before running
any `--endpoints` recovery that includes it.

Only backfills what docs/SCHEMA_DRAFT.md's "Endpoint -> table map" marks as
in scope: games_by_season + standings (season-level), then game_detail +
game_stats + shotmap (per game). schedule_by_season, player_*, team_info,
teams_stats, milestones are cataloged but not fetched here — see that doc's
"Deferred" section.
"""

import argparse
import logging
import sys
from pathlib import Path

from hockey_edge.ingest import db, raw_cache
from hockey_edge.ingest.liiga import parsers
from hockey_edge.ingest.liiga.endpoints import ENDPOINTS

# All endpoints this module can --force. Omitting --endpoints forces all of
# them (the original, pre-2026-08-24 behavior) -- passing --endpoints
# restricts --force to just the named subset; everything else still uses
# normal cache-first fetching regardless of the --force flag.
ALL_FORCEABLE_ENDPOINTS = ("games_by_season", "standings", "game_detail", "game_stats", "shotmap")

# Tournament values a season's games are split across (seen in the liiga.fi
# bundle per endpoints.py's module docstring). Not every season has games in
# every phase (e.g. a season with no qualifications round) — an empty result
# for one tournament value is normal, not a failure.
SEASON_TOURNAMENTS = ("runkosarja", "playoffs", "playout", "valmistavat_ottelut", "qualifications")

logger = logging.getLogger("hockey_edge.ingest")


def _configure_logging() -> None:
    log_dir = Path(__file__).resolve().parents[4] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "liiga_backfill.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _resolve_force(endpoint_name: str, *, force: bool, force_endpoints: set[str] | None) -> bool:
    """force_endpoints=None means --endpoints was not passed: --force (if
    set) applies to every endpoint, matching pre-2026-08-24 behavior exactly.
    A concrete set restricts --force to just those endpoint names."""
    if not force:
        return False
    if force_endpoints is None:
        return True
    return endpoint_name in force_endpoints


def backfill_games_and_standings(
    conn, season: int, *, force: bool = False, force_endpoints: set[str] | None = None
) -> list[int]:
    """Fetch+parse games_by_season (all tournament phases) and standings for
    one season. Returns the list of game_ids now present for that season."""
    games_endpoint = ENDPOINTS["games_by_season"]
    games_force = _resolve_force("games_by_season", force=force, force_endpoints=force_endpoints)
    all_game_rows: list[tuple] = []
    all_goal_rows: list[tuple] = []

    for tournament in SEASON_TOURNAMENTS:
        entity_id = f"{season}:{tournament}"
        url = games_endpoint.url_template.format(tournament=tournament, season=season)
        result = raw_cache.fetch(conn, games_endpoint, entity_id, url, season=season, force=games_force)
        if result.status != "success":
            logger.info("games_by_season season=%s tournament=%s -> %s, skipping", season, tournament, result.status)
            continue
        if not result.data:
            logger.info("games_by_season season=%s tournament=%s -> empty (no games in this phase)", season, tournament)
            continue
        game_rows, goal_rows = parsers.parse_games_by_season(result.data, season, result.raw_response_id)
        all_game_rows.extend(game_rows)
        all_goal_rows.extend(goal_rows)

    parsers.upsert_games_for_season(conn, season, all_game_rows, all_goal_rows)
    logger.info("season=%s: %d games, %d goal events parsed", season, len(all_game_rows), len(all_goal_rows))

    standings_endpoint = ENDPOINTS["standings"]
    standings_force = _resolve_force("standings", force=force, force_endpoints=force_endpoints)
    standings_url = standings_endpoint.url_template.format(season=season)
    result = raw_cache.fetch(conn, standings_endpoint, str(season), standings_url, season=season, force=standings_force)
    if result.status == "success" and result.data:
        standings_rows = parsers.parse_standings(result.data, season)
        parsers.upsert_standings(conn, season, standings_rows)
        logger.info("season=%s: %d standings rows parsed", season, len(standings_rows))
    else:
        logger.warning("standings season=%s -> %s, skipping", season, result.status)

    return [row[0] for row in all_game_rows]


def backfill_game_detail(
    conn, season: int, game_id: int, *, force: bool = False, force_endpoints: set[str] | None = None
) -> str:
    endpoint = ENDPOINTS["game_detail"]
    url = endpoint.url_template.format(season=season, game_id=game_id)
    # entity_id must be season-scoped: game_id alone is not globally unique
    # across seasons (liiga.fi reuses small RUNKOSARJA/PRACTICE ids per
    # season) — see docs/BACKFILL_RESULTS.md for the corruption this caused
    # before this fix.
    entity_id = f"{season}:{game_id}"
    endpoint_force = _resolve_force("game_detail", force=force, force_endpoints=force_endpoints)
    result = raw_cache.fetch(conn, endpoint, entity_id, url, season=season, force=endpoint_force)
    if result.status == "success" and result.data:
        parsed = parsers.parse_game_detail(result.data, game_id, season)
        parsers.upsert_game_detail(conn, game_id, season, parsed)
    return result.status


def backfill_game_stats(
    conn, season: int, game_id: int, *, force: bool = False, force_endpoints: set[str] | None = None
) -> str:
    endpoint = ENDPOINTS["game_stats"]
    url = endpoint.url_template.format(season=season, game_id=game_id)
    entity_id = f"{season}:{game_id}"  # see backfill_game_detail's comment
    endpoint_force = _resolve_force("game_stats", force=force, force_endpoints=force_endpoints)
    result = raw_cache.fetch(conn, endpoint, entity_id, url, season=season, force=endpoint_force)
    if result.status == "success" and result.data:
        parsed = parsers.parse_game_stats(result.data, game_id, season)
        parsers.upsert_game_stats(conn, game_id, season, parsed)
    return result.status


def backfill_shotmap(
    conn, season: int, game_id: int, *, force: bool = False, force_endpoints: set[str] | None = None
) -> str:
    endpoint = ENDPOINTS["shotmap"]
    url = endpoint.url_template.format(season=season, game_id=game_id)
    entity_id = f"{season}:{game_id}"  # see backfill_game_detail's comment
    endpoint_force = _resolve_force("shotmap", force=force, force_endpoints=force_endpoints)
    result = raw_cache.fetch(conn, endpoint, entity_id, url, season=season, force=endpoint_force)
    if result.status == "success" and result.data:
        rows = parsers.parse_shotmap(result.data, game_id, season)
        parsers.upsert_shot_events(conn, game_id, season, rows)
    return result.status


def backfill_season(
    conn, season: int, *, force: bool = False, max_games: int | None = None,
    force_endpoints: set[str] | None = None,
) -> dict[str, int]:
    all_game_ids = backfill_games_and_standings(conn, season, force=force, force_endpoints=force_endpoints)
    game_ids = all_game_ids if max_games is None else all_game_ids[:max_games]
    if max_games is not None:
        logger.info(
            "season=%s: sampling %d/%d games for per-game endpoints (--max-games)",
            season, len(game_ids), len(all_game_ids),
        )

    outcome_counts = {"game_detail": {}, "game_stats": {}, "shotmap": {}}
    for i, game_id in enumerate(game_ids, start=1):
        logger.info("season=%s: per-game fetch %d/%d game_id=%s", season, i, len(game_ids), game_id)

        status = backfill_game_detail(conn, season, game_id, force=force, force_endpoints=force_endpoints)
        outcome_counts["game_detail"][status] = outcome_counts["game_detail"].get(status, 0) + 1

        status = backfill_game_stats(conn, season, game_id, force=force, force_endpoints=force_endpoints)
        outcome_counts["game_stats"][status] = outcome_counts["game_stats"].get(status, 0) + 1

        status = backfill_shotmap(conn, season, game_id, force=force, force_endpoints=force_endpoints)
        outcome_counts["shotmap"][status] = outcome_counts["shotmap"].get(status, 0) + 1

    return {
        "season": season,
        "total_games_in_season": len(all_game_ids),
        "games_with_per_game_endpoints_fetched": len(game_ids),
        "outcomes": outcome_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True, help="Liiga season year, e.g. 2024")
    parser.add_argument("--force", action="store_true", help="refetch even if sync_state already has a success/failed_permanent row")
    parser.add_argument(
        "--endpoints", type=str, default=None,
        help="comma-separated subset of {" + ",".join(ALL_FORCEABLE_ENDPOINTS) + "} that --force "
        "applies to (others still use normal cache-first fetching). Omit to force all of them, the "
        "original behavior. Ignored if --force is not also passed. Do not include game_detail when "
        "targeting historical seasons -- see docs/RESYNC.md.",
    )
    parser.add_argument(
        "--max-games", type=int, default=None,
        help="only fetch per-game endpoints (game_detail/game_stats/shotmap) for the first N games "
        "of the season — games_by_season + standings are always fetched in full. Useful for a fast "
        "smoke test; omit for a real backfill.",
    )
    args = parser.parse_args()

    force_endpoints = None
    if args.endpoints is not None:
        force_endpoints = {e.strip() for e in args.endpoints.split(",") if e.strip()}
        unknown = force_endpoints - set(ALL_FORCEABLE_ENDPOINTS)
        if unknown:
            parser.error(f"unknown endpoint(s) in --endpoints: {sorted(unknown)}, must be from {ALL_FORCEABLE_ENDPOINTS}")

    _configure_logging()
    conn = db.get_connection()
    try:
        summary = backfill_season(
            conn, args.season, force=args.force, max_games=args.max_games, force_endpoints=force_endpoints,
        )
        logger.info("backfill complete: %s", summary)
        print(summary)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
