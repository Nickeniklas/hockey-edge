"""Confirmed-lineup + starting-goalie capture — STUBBED, not implemented.

Blocked on a real pre-game test: whether liiga.fi's `game_detail` endpoint
(GAME_DETAIL in src/hockey_edge/ingest/liiga/endpoints.py) returns
partial/no lineup data before puck drop, or already-full lineups too early, is
untested — no games were scheduled during the 2026-07 off-season endpoint
discovery session. Verify against a live pre-game fetch (next real Liiga game,
e.g. a preseason match around 2026-08-07) before trusting this endpoint for
real capture: a lineup source that fills in before its data is actually
official would silently violate hard rule 1 (no leakage) the moment it's wired
into the feature store.

Do not implement real capture logic until that test confirms pre-puck-drop
behavior. veikkaus.fi/kokoonpanot and liigakokoonpanot.com remain fallback
sources per docs/PLAN.md if game_detail turns out unusable pre-game.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class LineupSnapshot:
    game_id: str
    source: str
    captured_at: datetime
    raw_payload: str
    goalie_confirmed: bool | None = None


def fetch_lineup(game_id: str, season: str) -> LineupSnapshot:
    raise NotImplementedError(
        "Blocked on the pre-game game_detail test — see module docstring."
    )
