"""Live fixture discovery for the snapshot job -- Layer 2.

Deliberately reads from liiga.fi's live `games_by_date` endpoint, not
`data/hockey.db`: the two databases stay decoupled, and the live endpoint
picks up preseason games and schedule changes the backfilled DB won't have.
The original motivating case (2026-08-23: `hockey.db` had zero rows for
season 2027 while the live API already returned all of it) has since been
closed -- season 2027 was ingested 2026-09-01 and `scripts/nightly_sync.py`
keeps it current -- but the decoupling is deliberate regardless: this job
must not depend on an ingest pass having run, and must never write to
hockey.db's tables. See docs/SNAPSHOT_FINDINGS.md.

This module only ever performs GET requests against liiga.fi and writes to
`data/snapshots.db` (via storage.py) -- it must never write to `data/hockey.db`
or its `sync_state`/`raw_responses` tables, which belong to `ingest/`. It
imports `GAMES_BY_DATE` from `hockey_edge.ingest.liiga.endpoints` purely to
reuse the URL template (that module's whole purpose per its own docstring is
"no other module should hardcode a liiga.fi URL") -- no write path from here
ever touches `ingest/`'s tables.
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from hockey_edge.ingest.liiga.endpoints import GAMES_BY_DATE
from hockey_edge.snapshot import storage

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 20

# Tournaments polled by default. playoffs/playout/qualifications are omitted
# for 2026-27 (no playout/qualification bracket this season -- see CLAUDE.md
# structural-changes note) but the list stays here, not hardcoded away,
# so a later season with a playoff bracket just needs this list extended.
DEFAULT_TOURNAMENTS = ("runkosarja", "valmistavat_ottelut")

# How many days ahead to poll each run. Only needs to comfortably clear the
# longest window (T-24h) plus the 15-minute scheduler cadence; 2 days is
# generous headroom without hammering an undocumented API on every tick.
DEFAULT_DAYS_AHEAD = 2

WINDOW_OFFSETS = {
    "opening": timedelta(hours=24),
    "mid": timedelta(hours=3),
    "closing": timedelta(minutes=25),
}

logger = logging.getLogger("hockey_edge.snapshot")


def _iso_utc(dt: datetime) -> str:
    """liiga.fi's own `start`/`start_utc` strings are 'Z'-suffixed, no
    microseconds (e.g. '2026-08-25T15:30:00Z'). due_at gets compared against
    those via plain SQL string ordering (see storage.get_due_windows/
    mark_missed_windows) — Python's default .isoformat() would emit
    '+00:00' and microseconds instead, which sorts differently at exact-tie
    instants. Match the API's own format exactly so the comparison is a
    true string-ordering-equals-chronological-ordering comparison, not just
    approximately right."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_iso() -> str:
    return _iso_utc(datetime.now(timezone.utc))


def _fetch_games_by_date(tournament: str, date: str) -> tuple[list[dict], str]:
    url = GAMES_BY_DATE.url_template.format(tournament=tournament, date=date)
    time.sleep(REQUEST_DELAY_SECONDS)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    games = data.get("games", []) if isinstance(data, dict) else data
    return games, url


def discover_fixtures(
    conn,
    *,
    days_ahead: int = DEFAULT_DAYS_AHEAD,
    tournaments: tuple[str, ...] = DEFAULT_TOURNAMENTS,
) -> int:
    """Poll games_by_date for today..today+days_ahead across `tournaments`,
    append a discovered_fixtures row per fixture seen, and ensure
    capture_windows rows exist for each. Returns the number of distinct
    fixtures seen this pass. Makes `days_ahead * len(tournaments)` live HTTP
    requests -- never call this from --dry-run."""
    captured_at = _now_iso()
    today = datetime.now(timezone.utc).date()
    seen: dict[tuple[int, int], dict] = {}

    for offset in range(days_ahead + 1):
        date = (today + timedelta(days=offset)).isoformat()
        for tournament in tournaments:
            try:
                games, url = _fetch_games_by_date(tournament, date)
            except requests.RequestException as exc:
                logger.warning(
                    "fixture discovery: games_by_date failed (tournament=%s date=%s): %s",
                    tournament, date, exc,
                )
                continue

            for game in games:
                season = game.get("season")
                game_id = game.get("id")
                if season is None or game_id is None:
                    continue
                key = (season, game_id)
                seen[key] = game

                storage.insert_discovered_fixture(
                    conn,
                    league="liiga",
                    season=season,
                    game_id=game_id,
                    serie=game.get("serie", ""),
                    start_utc=game.get("start", ""),
                    home_team=game.get("homeTeam", {}).get("teamName", ""),
                    away_team=game.get("awayTeam", {}).get("teamName", ""),
                    started=bool(game.get("started")),
                    ended=bool(game.get("ended")),
                    captured_at=captured_at,
                    source=f"games_by_date:{tournament}:{date}",
                    raw_payload=json.dumps(game),
                )

    for (season, game_id), game in seen.items():
        if game.get("ended"):
            continue  # no capture windows for a game that's already over
        start_utc = game.get("start")
        if not start_utc:
            continue
        start_dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
        for window, offset in WINDOW_OFFSETS.items():
            due_at = _iso_utc(start_dt - offset)
            for kind in storage.CAPTURE_KINDS:
                storage.upsert_capture_window(
                    conn, league="liiga", season=season, game_id=game_id,
                    kind=kind, window=window, due_at=due_at,
                )

    logger.info(
        "fixture discovery: %d distinct fixture(s) seen across %d date(s) x %d tournament(s)",
        len(seen), days_ahead + 1, len(tournaments),
    )
    return len(seen)
