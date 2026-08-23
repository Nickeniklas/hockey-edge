"""Throwaway probe: determine liiga.fi's season-numbering convention and find
out which seasons exist that we haven't backfilled yet.

Read-only. Does NOT write to data/hockey.db, does NOT touch sync_state or
raw_responses, and does NOT go through hockey_edge.ingest.raw_cache or the
backfill code path at all — this is a plain fetch-and-print script, modelled
on scripts/oddspapi_probe.py. Uses GAMES_BY_SEASON.url_template from
hockey_edge.ingest.liiga.endpoints (the same catalog the real ingest code
uses) so the URL shape can't drift from what backfill.py actually calls, but
never imports raw_cache/backfill/db.

Run manually (venv active — src/hockey_edge is pip-install-e'd, see README.md's
Setup section): python scripts/season_probe.py [season ...]
Defaults to probing seasons 2024 2025 2026 2027 if none given.

Context: CLAUDE.md's 2026-08-22 status has the 10-season backfill (2015-2024)
complete; this probe checks what's next (2025, 2026, and the in-progress
2026-27 season under whatever number the API uses for it) before deciding
whether/how to extend the backfill.
"""

import sys
import time
from urllib.parse import urlencode

import requests

from hockey_edge.ingest.liiga.endpoints import GAMES_BY_SEASON

# Same UA/rate-limit convention as hockey_edge.ingest.raw_cache — polite
# scraping applies to probes too, not just the real backfill.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 20
TOURNAMENT = "runkosarja"  # regular season is enough to answer the probe questions


def fetch_games_by_season(season: int) -> tuple[int, list | dict | None]:
    url = GAMES_BY_SEASON.url_template.format(tournament=TOURNAMENT, season=season)
    print(f"GET {url}")
    try:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        print(f"  -> request failed: {exc}", file=sys.stderr)
        return 0, None
    print(f"  -> HTTP {resp.status_code}")
    if not resp.ok:
        print(f"  -> body (first 500 chars): {resp.text[:500]}")
        return resp.status_code, None
    try:
        return resp.status_code, resp.json()
    except ValueError:
        print(f"  -> non-JSON body (first 500 chars): {resp.text[:500]}")
        return resp.status_code, None


def report_season(season: int) -> dict:
    status, games = fetch_games_by_season(season)
    result: dict = {"season": season, "http_status": status, "games": games}

    if not isinstance(games, list) or not games:
        result["num_games"] = 0 if games == [] else None
        return result

    result["num_games"] = len(games)

    starts = [g.get("start") for g in games if g.get("start")]
    result["earliest_start"] = min(starts) if starts else None
    result["latest_start"] = max(starts) if starts else None

    team_names = set()
    for g in games:
        home = g.get("homeTeam", {}) or {}
        away = g.get("awayTeam", {}) or {}
        if home.get("teamName"):
            team_names.add(home["teamName"])
        if away.get("teamName"):
            team_names.add(away["teamName"])
    result["team_names"] = sorted(team_names)
    result["num_teams"] = len(team_names)

    result["num_started"] = sum(1 for g in games if g.get("started") is True)
    result["num_ended"] = sum(1 for g in games if g.get("ended") is True)

    xg_present = 0
    xg_populated = 0
    for g in games:
        for side in ("homeTeam", "awayTeam"):
            team = g.get(side, {}) or {}
            if "expectedGoals" in team:
                xg_present += 1
                if team.get("expectedGoals") is not None:
                    xg_populated += 1
    result["xg_present_count"] = xg_present  # out of 2 * num_games (home + away slots)
    result["xg_populated_count"] = xg_populated

    return result


def print_report(result: dict) -> None:
    season = result["season"]
    print(f"\n=== season={season} ===")
    print(f"HTTP status: {result['http_status']}")
    if result.get("num_games") in (None, 0):
        print(f"games returned: {result.get('num_games')}")
        return
    print(f"games returned: {result['num_games']}")
    print(f"earliest start: {result.get('earliest_start')}")
    print(f"latest start:   {result.get('latest_start')}")
    print(f"distinct teams: {result['num_teams']}")
    for name in result["team_names"]:
        print(f"    - {name}")
    print(f"started=true: {result['num_started']}/{result['num_games']}")
    print(f"ended=true:   {result['num_ended']}/{result['num_games']}")
    print(
        f"expectedGoals field present: {result['xg_present_count']}/{result['num_games'] * 2} "
        f"team-slots; populated (non-null): {result['xg_populated_count']}/{result['num_games'] * 2}"
    )


def main() -> None:
    seasons = [int(a) for a in sys.argv[1:]] or [2024, 2025, 2026, 2027]

    results = []
    for i, season in enumerate(seasons):
        if i > 0:
            time.sleep(REQUEST_DELAY_SECONDS)
        results.append(report_season(season))

    for result in results:
        print_report(result)

    print("\n=== ANSWERS ===")

    r2024 = next((r for r in results if r["season"] == 2024), None)
    if r2024 and r2024.get("earliest_start") and r2024.get("latest_start"):
        earliest_year = r2024["earliest_start"][:4]
        latest_year = r2024["latest_start"][:4]
        print(
            f"1. season=2024 games run {r2024['earliest_start']} .. {r2024['latest_start']} "
            f"(years {earliest_year}-{latest_year}). "
        )
        if earliest_year == "2023" and latest_year in ("2024",):
            print(
                "   -> Matches ENDING-YEAR convention (Sep 2023 - Apr 2024, i.e. "
                "the '2023-24 season' is called season=2024)."
            )
        elif earliest_year == "2024":
            print(
                "   -> Matches STARTING-YEAR convention (Sep 2024 - ..., i.e. "
                "the '2024-25 season' is called season=2024) — contradicts the "
                "ending-year assumption in CLAUDE.md."
            )
        else:
            print("   -> Does not cleanly match either convention — inspect manually.")
    else:
        print("1. season=2024 returned no usable start-date data — cannot determine convention.")

    for s in (2025, 2026, 2027):
        r = next((x for x in results if x["season"] == s), None)
        if r is None:
            continue
        has_data = r.get("num_games")
        print(f"2. season={s}: {'REAL DATA' if has_data else 'no data'} ({r.get('num_games')} games)")

    r2027 = next((r for r in results if r["season"] == 2027), None)
    if r2027 and r2027.get("num_games"):
        print(
            f"3. season=2027: {r2027['num_teams']} teams, {r2027['num_games']} games "
            f"(expected: 17 teams, 544 regular-season games). "
            f"{'MATCHES expectation' if r2027['num_teams'] == 17 and r2027['num_games'] == 544 else 'DOES NOT MATCH expectation — report as-is, do not assume the expectation is right.'}"
        )
    else:
        print("3. season=2027 returned no games — cannot confirm 2026-27 season coverage.")


if __name__ == "__main__":
    main()
