"""Standalone lineup probe -- fetches game_detail + game_preview for one live
game and reports whether liiga.fi exposes a confirmed pre-game lineup.

Read-only: makes GET requests to liiga.fi only, writes nothing to any
database (hockey.db or snapshots.db), only to fixtures/liiga/lineup_probe/ on
disk. Built for the scheduled T-90min / T-30min lineup test on 2026-08-25
(season=2027, game_id=2701831) but takes --season/--game-id so it can be
rerun against any game, e.g. the 2026-09-01 regular-season opener later.

Looks the game's start_utc up itself (from game_detail's own `game.start`)
rather than requiring it as an argument, because game_preview needs a full
ISO datetime for `gameDate` -- a bare date 500s (confirmed 2026-08-23, see
docs/SNAPSHOT_FINDINGS.md section 1b) -- and because homeTeam/awayTeam slugs
for game_preview also come from game_detail's `game.homeTeam.teamId` /
`game.awayTeam.teamId` (format "651304385:tps" -> slug "tps"), not something
worth making the caller supply by hand.

Usage:
    python scripts/lineup_probe.py --season 2027 --game-id 2701831
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from hockey_edge.ingest.liiga.endpoints import GAME_DETAIL, GAME_PREVIEW

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "fixtures" / "liiga" / "lineup_probe"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 20


def _now_filename_stamp() -> str:
    # ':' is NTFS's alternate-data-stream separator on Windows -- a raw colon
    # in a filename silently writes to a hidden stream instead of erroring
    # (see CLAUDE.md Gotchas, and the same fix in ingest/raw_cache.py). '-'
    # substitutes for both '/' and ':' here, same convention.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


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


def _save(data, name: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _summarize_roster(players: list[dict], label: str) -> None:
    n = len(players)
    goalies = [p for p in players if p.get("roleCode") == "MV"]
    with_line = [p for p in players if p.get("line") is not None]
    print(f"  {label}: {n} players, {len(goalies)} goalies (roleCode='MV'), "
          f"{len(with_line)} with non-null 'line'")
    if goalies:
        names = ", ".join(f"{g.get('firstName')} {g.get('lastName')}" for g in goalies)
        print(f"    goalies listed: {names}")
    if with_line:
        for p in with_line:
            print(f"    line set: {p.get('firstName')} {p.get('lastName')} -> line={p.get('line')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--game-id", type=int, required=True)
    args = parser.parse_args()

    stamp = _now_filename_stamp()

    detail_url = GAME_DETAIL.url_template.format(season=args.season, game_id=args.game_id)
    print(f"GET {detail_url}")
    resp = _get_with_retry(detail_url)
    resp.raise_for_status()
    detail = resp.json()
    detail_path = _save(detail, f"game_detail_{args.season}_{args.game_id}_{stamp}.json")
    print(f"  saved -> {detail_path.relative_to(REPO_ROOT)}")

    game = detail.get("game", {})
    start_utc = game.get("start")
    started = game.get("started")
    ended = game.get("ended")
    home_team_id = game.get("homeTeam", {}).get("teamId", "")
    away_team_id = game.get("awayTeam", {}).get("teamId", "")
    home_slug = home_team_id.split(":")[-1] if home_team_id else ""
    away_slug = away_team_id.split(":")[-1] if away_team_id else ""

    if start_utc:
        now = datetime.now(timezone.utc)
        game_start = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
        delta = game_start - now
        print(f"  game.start={start_utc} started={started} ended={ended} "
              f"(now vs. puck drop: {delta})")
    else:
        print(f"  WARNING: no game.start found in response -- started={started} ended={ended}")

    print()
    print("=== game_detail roster ===")
    home_players = detail.get("homeTeamPlayers") or []
    away_players = detail.get("awayTeamPlayers") or []
    _summarize_roster(home_players, f"home ({home_slug or home_team_id})")
    _summarize_roster(away_players, f"away ({away_slug or away_team_id})")

    if not start_utc or not home_slug or not away_slug:
        print("\nCannot fetch game_preview: missing start_utc/home slug/away slug from game_detail.")
        return

    preview_url = GAME_PREVIEW.url_template.format(
        season=args.season, game_id=args.game_id,
        game_date=start_utc, home_slug=home_slug, away_slug=away_slug,
    )
    print(f"\nGET {preview_url}")
    resp = _get_with_retry(preview_url)
    resp.raise_for_status()
    preview = resp.json()
    preview_path = _save(preview, f"game_preview_{args.season}_{args.game_id}_{stamp}.json")
    print(f"  saved -> {preview_path.relative_to(REPO_ROOT)}")

    print()
    print("=== game_preview goaliesToWatch ===")
    goalies_to_watch = preview.get("goaliesToWatch", {})
    any_populated = any(v for v in goalies_to_watch.values())
    print(f"  goaliesToWatch populated: {any_populated}")
    if any_populated:
        print(f"  {json.dumps(goalies_to_watch, indent=2, ensure_ascii=False)}")
    else:
        print(f"  (empty for both teams: {goalies_to_watch})")


if __name__ == "__main__":
    main()
