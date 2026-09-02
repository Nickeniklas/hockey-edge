"""Confirmed-lineup + starting-goalie capture.

Unblocked 2026-09-01: the T-30 pre-puck-drop probe on the full 2026-09-01
opening slate (7 games, `scripts/lineup_probe.py --date 2026-09-01`)
confirmed `game_detail` publishes a real confirmed lineup close to game
time -- `line` is non-null for exactly 22 players per team-side (20 skaters
+ 2 goalies, zero exceptions across 14 team-sides checked) at T-30, versus
null for every player at T-9 days for the same game. Fetches `game_preview`
too (best-effort -- see `fetch_lineups`) but its `goaliesToWatch` field has
been empty on every game checked so far (T-9 days through T-30) and
contributes no signal yet; kept only because the raw response is free once
we're already fetching `game_detail` per game, and it's saved in raw_payload
in case that changes.

Starter identification: `game_detail` has no literal "starter" field --
every field on every goalie object was dumped and checked (id, jersey,
handedness, height/weight, captain/rookie/alternateCaptain, injured/
suspended/removed, pictureUrl, awards, sponsors, extra_* -- nothing says
"starting"). What IS real: `line`, the same depth-chart field used for
forward lines (1-4) and D-pairs (1-3), is also populated for goalies, and
the two dressed goalies on every team-side split cleanly into exactly one
at line=1 and one at line=2 -- never 0-2, 2-0, or a tie. That's a genuine
structural signal (liiga.fi assigning it deliberately), not something
invented here to compensate for a missing field, so line==1 is used as the
primary starter signal (`starter_source='goalie_line_value'`).

This is NOT yet confirmed against real results. A competing candidate
(first goalie in the raw players-array order) was raised from an earlier
manual Flashscore cross-check that reportedly matched the actual starter in
7/7 games -- but a structural check across the same 2026-09-01 slate found
array-order-first agrees with line==1 in only 7 of 14 team-sides, i.e. no
better than chance. The two candidates cannot both be right in the games
where they disagree, and this module has no way to independently check real
results. Both signals stay reconstructable from raw_payload regardless of
which one gets written to starter_source; `scripts/verify_starters.py`
scores starter_source='goalie_line_value' against actual starters (from
`game_goalkeeper_events` in hockey.db) once completed games exist to check
against. Treat every starter_* field this module produces as an inference
pending that check, not a fact -- `starter_confidence='inferred_structural'`
is there specifically so downstream feature code can see the basis and
choose to distrust it.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from hockey_edge.ingest.liiga.endpoints import GAME_DETAIL, GAME_PREVIEW

logger = logging.getLogger("hockey_edge.snapshot")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 20

STARTER_SOURCE_LINE_VALUE = "goalie_line_value"


@dataclass(frozen=True)
class LineupSnapshot:
    """One team-side's confirmed-lineup observation, captured at one point in
    time. `roster` holds every line-assigned (non-null `line`) player, not
    just goalies. `parsed=False` only if extraction itself raised (e.g. an
    unannounced shape change) -- raw_payload is always populated regardless,
    same append-only-friendly contract as OddsSnapshot."""

    league: str
    season: int
    game_id: int
    team_role: str  # 'home' | 'away'
    team_name: str
    window: str | None
    captured_at: datetime
    source: str
    confirmed_player_count: int
    confirmed_goalie_count: int
    roster: list[dict] = field(default_factory=list)
    starter_player_id: int | None = None
    starter_name: str | None = None
    starter_jersey: int | None = None
    starter_source: str | None = None
    starter_confidence: str | None = None
    parsed: bool = True
    raw_payload: str = "{}"


def _fetch_json(url: str) -> dict:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def _confirmed_players(players: list[dict]) -> list[dict]:
    return [p for p in players if p.get("line") is not None]


def _identify_starter(confirmed_players: list[dict]) -> tuple[dict | None, str | None, str | None]:
    """Primary (and currently only) candidate: the confirmed goalie at
    line==1. Returns (starter_player_or_None, starter_source, confidence).
    Ambiguous shapes (zero or >1 goalie at line==1) are left unidentified
    rather than guessed at -- see module docstring."""
    goalies = [p for p in confirmed_players if p.get("roleCode") == "MV"]
    line1 = [g for g in goalies if g.get("line") == 1]
    if len(line1) == 1:
        return line1[0], STARTER_SOURCE_LINE_VALUE, "inferred_structural"
    return None, None, None


def _roster_entry(p: dict) -> dict:
    return {
        "id": p.get("id"),
        "firstName": p.get("firstName"),
        "lastName": p.get("lastName"),
        "jersey": p.get("jersey"),
        "role": p.get("role"),
        "roleCode": p.get("roleCode"),
        "line": p.get("line"),
    }


def fetch_lineups(
    season: int,
    game_id: int,
    *,
    window: str | None = None,
    source: str = "game_detail",
    league: str = "liiga",
) -> list[LineupSnapshot]:
    """Fetch game_detail (required) + game_preview (best-effort) for one game
    and return one LineupSnapshot per team-side. Raises on a game_detail
    failure -- there is nothing usable to write without it, and the caller
    (job.py) is what decides how to log/count that, same as odds capture. A
    game_preview failure is swallowed and logged, not raised: game_preview
    has contributed no used signal on any game checked yet (goaliesToWatch
    always empty), so losing it must not cost the lineup capture."""
    captured_at = datetime.now(timezone.utc)

    detail_url = GAME_DETAIL.url_template.format(season=season, game_id=game_id)
    detail = _fetch_json(detail_url)

    game = detail.get("game", {})
    home = game.get("homeTeam", {})
    away = game.get("awayTeam", {})
    home_team_id = home.get("teamId", "")
    away_team_id = away.get("teamId", "")
    start_utc = game.get("start")

    preview = None
    if start_utc and home_team_id and away_team_id:
        preview_url = GAME_PREVIEW.url_template.format(
            season=season,
            game_id=game_id,
            game_date=start_utc,
            home_slug=home_team_id.split(":")[-1],
            away_slug=away_team_id.split(":")[-1],
        )
        try:
            preview = _fetch_json(preview_url)
        except requests.RequestException as exc:
            logger.warning(
                "lineups: game_preview failed (season=%s game_id=%s): %s -- "
                "continuing with game_detail only", season, game_id, exc,
            )

    raw_payload = json.dumps({"game_detail": detail, "game_preview": preview}, ensure_ascii=False)

    snapshots = []
    for role, team, players in (
        ("home", home, detail.get("homeTeamPlayers") or []),
        ("away", away, detail.get("awayTeamPlayers") or []),
    ):
        try:
            confirmed = _confirmed_players(players)
            goalies = [p for p in confirmed if p.get("roleCode") == "MV"]
            starter, starter_source, starter_confidence = _identify_starter(confirmed)
            snapshots.append(LineupSnapshot(
                league=league, season=season, game_id=game_id, team_role=role,
                team_name=team.get("teamName", ""), window=window, captured_at=captured_at,
                source=source, confirmed_player_count=len(confirmed),
                confirmed_goalie_count=len(goalies),
                roster=[_roster_entry(p) for p in confirmed],
                starter_player_id=starter.get("id") if starter else None,
                starter_name=(f"{starter.get('firstName')} {starter.get('lastName')}"
                              if starter else None),
                starter_jersey=starter.get("jersey") if starter else None,
                starter_source=starter_source, starter_confidence=starter_confidence,
                parsed=True, raw_payload=raw_payload,
            ))
        except Exception:
            logger.exception(
                "lineups: parse failed for season=%s game_id=%s role=%s -- "
                "raw payload still saved", season, game_id, role,
            )
            snapshots.append(LineupSnapshot(
                league=league, season=season, game_id=game_id, team_role=role,
                team_name=team.get("teamName", ""), window=window, captured_at=captured_at,
                source=source, confirmed_player_count=0, confirmed_goalie_count=0,
                parsed=False, raw_payload=raw_payload,
            ))
    return snapshots
