"""Standalone lineup probe -- fetches game_detail + game_preview for one or
many live games and reports whether liiga.fi exposes a confirmed pre-game
lineup.

Read-only: makes GET requests to liiga.fi only, writes nothing to any
database (hockey.db or snapshots.db), only to fixtures/liiga/lineup_probe/ on
disk. Originally built for the scheduled T-90min / T-30min single-game test
on 2026-08-25 (season=2027, game_id=2701831); extended for the
2026-09-01 opening-slate test, which covers every game on the day rather
than one, hence --date.

Two modes:
  --season/--game-id  single game (original behavior).
  --date YYYY-MM-DD    discover every fixture live on that date and probe
                        each one in sequence.

Discovery (--date) polls games_by_date per tournament in
DISCOVERY_TOURNAMENTS (mirrors snapshot/fixtures.py's DEFAULT_TOURNAMENTS --
kept as a separate local constant so this script stays a standalone,
zero-DB-writes tool with no import-time dependency on the snapshot job's
odds/storage machinery). games_by_date 502'd intermittently during Phase 1
recon -- _get_with_retry already retries once; if it still fails for a
tournament, this falls back to games_by_season?season=FALLBACK_SEASON for
that tournament, filtered to the requested date client-side. Whatever
games_by_date/games_by_season actually returns is what gets probed -- this
never substitutes a hardcoded game list.

For each game: looks the start_utc up itself (from game_detail's own
`game.start`) rather than requiring it as an argument, because game_preview
needs a full ISO datetime for `gameDate` -- a bare date 500s (confirmed
2026-08-23, see docs/SNAPSHOT_FINDINGS.md section 1b) -- and because
homeTeam/awayTeam slugs for game_preview also come from game_detail's
`game.homeTeam.teamId` / `game.awayTeam.teamId` (format "651304385:tps" ->
slug "tps"), not something worth making the caller supply by hand.

Output is one consolidated summary table (one row per team per game) rather
than a per-game block, meant to be pasted whole into the next session.

Usage:
    python scripts/lineup_probe.py --season 2027 --game-id 2701831 --label T-90
    python scripts/lineup_probe.py --date 2026-09-01 --label T-90
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from hockey_edge.ingest.liiga.endpoints import GAME_DETAIL, GAME_PREVIEW, GAMES_BY_DATE, GAMES_BY_SEASON

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "fixtures" / "liiga" / "lineup_probe"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 20

# Mirrors snapshot/fixtures.py's DEFAULT_TOURNAMENTS. playoffs/playout/
# qualifications omitted for 2026-27 (no such bracket this season -- see
# CLAUDE.md structural-changes note); extend this if a later season needs it.
DISCOVERY_TOURNAMENTS = ("runkosarja", "valmistavat_ottelut")

# games_by_date silently ignores its own `season` query param (see
# endpoints.py's GAMES_BY_DATE notes) -- there is no way to ask it what
# season "today" belongs to. The games_by_season fallback therefore needs an
# explicit season. 2027 is the live 2026-27 season as of this writing.
FALLBACK_SEASON = 2027


def _now_filename_stamp() -> str:
    # ':' is NTFS's alternate-data-stream separator on Windows -- a raw colon
    # in a filename silently writes to a hidden stream instead of erroring
    # (see CLAUDE.md Gotchas, and the same fix in ingest/raw_cache.py). '-'
    # substitutes for both '/' and ':' here, same convention.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _sanitize_label(label: str) -> str:
    return label.replace(":", "-").replace("/", "-").replace(" ", "")


def _get_with_retry(url: str) -> requests.Response:
    """One retry on a 502 -- games_by_date 502'd twice during Phase 1 recon,
    intermittent rather than systematic. Anything else (non-502 error,
    or a second 502) is not retried."""
    time.sleep(REQUEST_DELAY_SECONDS)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
    if resp.status_code == 502:
        print(f"  502 from {url}, retrying once after {REQUEST_DELAY_SECONDS}s...", file=sys.stderr)
        time.sleep(REQUEST_DELAY_SECONDS)
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
    return resp


def _fetch_json(url: str):
    resp = _get_with_retry(url)
    resp.raise_for_status()
    return resp.json()


def _games_from_response(data) -> list[dict]:
    return data.get("games", []) if isinstance(data, dict) else data


def _save(data, name: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def discover_games_for_date(date: str) -> list[dict]:
    """Discover every fixture live on `date` across DISCOVERY_TOURNAMENTS.
    Per tournament: try games_by_date (with 502-retry); on any request
    failure, fall back to games_by_season?season=FALLBACK_SEASON filtered to
    `date` client-side. Merges across tournaments, deduped by (season, id).
    Never raises on a single tournament's failure -- returns whatever it
    managed to discover, printing what happened along the way."""
    seen: dict[tuple[int, int], dict] = {}

    for tournament in DISCOVERY_TOURNAMENTS:
        games: list[dict] = []
        url = GAMES_BY_DATE.url_template.format(tournament=tournament, date=date)
        print(f"GET {url}")
        try:
            data = _fetch_json(url)
            games = _games_from_response(data)
        except requests.RequestException as exc:
            print(f"  games_by_date failed for tournament={tournament}: {exc}", file=sys.stderr)
            fallback_url = GAMES_BY_SEASON.url_template.format(
                tournament=tournament, season=FALLBACK_SEASON
            )
            print(f"  falling back: GET {fallback_url}", file=sys.stderr)
            try:
                data = _fetch_json(fallback_url)
                all_games = _games_from_response(data)
                games = [g for g in all_games if (g.get("start") or "")[:10] == date]
            except requests.RequestException as exc2:
                print(f"  games_by_season fallback ALSO failed for tournament={tournament}: "
                      f"{exc2}", file=sys.stderr)
                games = []

        for g in games:
            season = g.get("season")
            game_id = g.get("id")
            if season is None or game_id is None:
                continue
            if (g.get("start") or "")[:10] != date:
                continue  # belt-and-suspenders: games_by_season isn't date-scoped server-side
            seen[(season, game_id)] = g

    return list(seen.values())


def probe_one_game(season: int, game_id: int, stamp: str, label: str) -> dict:
    """Fetch game_detail + game_preview for one game. Returns
    {"rows": [one dict per team], "error": str|None} -- never raises; a
    fetch failure is captured in "error" so a batch run keeps going."""
    label_part = f"_{_sanitize_label(label)}" if label else ""
    result: dict = {"season": season, "game_id": game_id, "rows": [], "error": None}

    detail_url = GAME_DETAIL.url_template.format(season=season, game_id=game_id)
    print(f"\nGET {detail_url}")
    try:
        detail = _fetch_json(detail_url)
    except requests.RequestException as exc:
        print(f"  ERROR fetching game_detail: {exc}", file=sys.stderr)
        result["error"] = f"game_detail: {exc}"
        return result
    detail_path = _save(detail, f"game_detail_{season}_{game_id}{label_part}_{stamp}.json")
    print(f"  saved -> {detail_path.relative_to(REPO_ROOT)}")

    game = detail.get("game", {})
    start_utc = game.get("start")
    started = game.get("started")
    ended = game.get("ended")
    home = game.get("homeTeam", {})
    away = game.get("awayTeam", {})
    home_team_id = home.get("teamId", "")
    away_team_id = away.get("teamId", "")
    home_slug = home_team_id.split(":")[-1] if home_team_id else ""
    away_slug = away_team_id.split(":")[-1] if away_team_id else ""
    home_numeric_id = home_team_id.split(":")[0] if home_team_id else ""
    away_numeric_id = away_team_id.split(":")[0] if away_team_id else ""
    home_name = home.get("teamName") or home_slug or home_team_id
    away_name = away.get("teamName") or away_slug or away_team_id

    if start_utc:
        now = datetime.now(timezone.utc)
        game_start = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
        delta = game_start - now
        print(f"  {away_name} @ {home_name}: start={start_utc} started={started} "
              f"ended={ended} (now vs. puck drop: {delta})")
    else:
        print(f"  WARNING: no game.start in response for season={season} game_id={game_id} "
              f"-- started={started} ended={ended}")

    home_players = detail.get("homeTeamPlayers") or []
    away_players = detail.get("awayTeamPlayers") or []

    goalies_to_watch: dict = {}
    if start_utc and home_slug and away_slug:
        preview_url = GAME_PREVIEW.url_template.format(
            season=season, game_id=game_id, game_date=start_utc,
            home_slug=home_slug, away_slug=away_slug,
        )
        print(f"  GET {preview_url}")
        try:
            preview = _fetch_json(preview_url)
            preview_path = _save(
                preview, f"game_preview_{season}_{game_id}{label_part}_{stamp}.json"
            )
            print(f"    saved -> {preview_path.relative_to(REPO_ROOT)}")
            goalies_to_watch = preview.get("goaliesToWatch", {}) or {}
        except requests.RequestException as exc:
            print(f"    ERROR fetching game_preview: {exc}", file=sys.stderr)
            result["error"] = f"game_preview: {exc}"
    else:
        print("  Cannot fetch game_preview: missing start_utc/home slug/away slug from game_detail.")

    def _row(role: str, team_name: str, players: list[dict], numeric_id: str) -> dict:
        goalies = [p for p in players if p.get("roleCode") == "MV"]
        lines_set = [p for p in players if p.get("line") is not None]
        return {
            "season": season, "game_id": game_id, "role": role, "team": team_name,
            "players": len(players), "goalies": len(goalies), "lines_set": len(lines_set),
            "goalies_to_watch": bool(goalies_to_watch.get(numeric_id)),
        }

    result["rows"].append(_row("home", home_name, home_players, home_numeric_id))
    result["rows"].append(_row("away", away_name, away_players, away_numeric_id))
    return result


def print_summary_table(rows: list[dict]) -> None:
    headers = ["season", "game_id", "team", "role", "players", "goalies", "lines_set", "gtw"]
    label_map = {"lines_set": "lines", "goalies_to_watch": "gtw"}

    formatted = []
    for r in rows:
        formatted.append({
            "season": str(r["season"]),
            "game_id": str(r["game_id"]),
            "team": r["team"][:18],
            "role": r["role"],
            "players": str(r["players"]),
            "goalies": str(r["goalies"]),
            "lines_set": str(r["lines_set"]),
            "gtw": "yes" if r["goalies_to_watch"] else "no",
        })

    display_headers = {"lines_set": "lines"}
    widths = {h: len(display_headers.get(h, h)) for h in headers}
    for vals in formatted:
        for h in headers:
            widths[h] = max(widths[h], len(vals[h]))

    def fmt_row(vals: dict) -> str:
        return "  ".join(vals[h].ljust(widths[h]) for h in headers)

    print(fmt_row({h: display_headers.get(h, h) for h in headers}))
    print("  ".join("-" * widths[h] for h in headers))
    for vals in formatted:
        print(fmt_row(vals))


def main() -> None:
    # Team names (Ässät, Kärpät, ...) mangle under Windows consoles whose
    # default codepage isn't UTF-8; reconfigure explicitly rather than let
    # print() silently garble the one output meant to be pasted verbatim.
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--date", help="YYYY-MM-DD -- discover and probe every live fixture on this date")
    parser.add_argument("--season", type=int, help="single-game mode (requires --game-id)")
    parser.add_argument("--game-id", type=int, help="single-game mode (requires --season)")
    parser.add_argument("--label", default="", help="e.g. T-90, T-30 -- embedded in saved filenames")
    args = parser.parse_args()

    if args.date and (args.season is not None or args.game_id is not None):
        parser.error("--date is mutually exclusive with --season/--game-id")
    if not args.date and (args.season is None or args.game_id is None):
        parser.error("either --date, or both --season and --game-id, is required")

    stamp = _now_filename_stamp()

    if args.date:
        print(f"Discovering fixtures for {args.date} via games_by_date "
              f"(tournaments={', '.join(DISCOVERY_TOURNAMENTS)})...")
        games = discover_games_for_date(args.date)
        if not games:
            print(f"\nNo fixtures discovered for {args.date}. Nothing to probe.")
            return
        games.sort(key=lambda g: (g.get("start") or "", g.get("id")))
        print(f"\nDiscovered {len(games)} fixture(s) for {args.date}:")
        for g in games:
            home_name = g.get("homeTeam", {}).get("teamName", "?")
            away_name = g.get("awayTeam", {}).get("teamName", "?")
            print(f"  season={g.get('season')} game_id={g.get('id')} serie={g.get('serie')} "
                  f"{away_name} @ {home_name} start={g.get('start')}")
        targets = [(g["season"], g["id"]) for g in games]
    else:
        targets = [(args.season, args.game_id)]

    all_rows: list[dict] = []
    errors: list[tuple[int, int, str]] = []
    for season, game_id in targets:
        result = probe_one_game(season, game_id, stamp, args.label)
        all_rows.extend(result["rows"])
        if result["error"]:
            errors.append((season, game_id, result["error"]))

    print("\n=== Summary ===")
    if all_rows:
        print_summary_table(all_rows)
    else:
        print("  (no games probed successfully)")
    if errors:
        print("\nErrors:")
        for season, game_id, err in errors:
            print(f"  season={season} game_id={game_id}: {err}")


if __name__ == "__main__":
    main()
