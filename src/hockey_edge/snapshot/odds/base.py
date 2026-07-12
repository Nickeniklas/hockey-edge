"""Odds provider interface — swappable source per docs/DATA_PIPELINE.md Layer 2.

Implementations fetch and return snapshots; they never write to storage
themselves and never mutate previously-returned data — append-only writing is
the caller's (job.py) responsibility.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class OddsSnapshot:
    """One odds observation for one fixture, captured at one point in time.

    `parsed` is False whenever `home_odds`/`draw_odds`/`away_odds` could not be
    extracted from `raw_payload` yet (e.g. the market/outcome shape hasn't been
    confirmed against a live fixture). `raw_payload` is always populated so
    parsing can be redone later without recapturing — same pattern as Layer 1's
    raw_responses cache.
    """

    league: str
    book: str
    fixture_ref: str
    captured_at: datetime
    raw_payload: str
    market: str | None = None
    home_odds: float | None = None
    draw_odds: float | None = None
    away_odds: float | None = None
    parsed: bool = False


class OddsProvider(ABC):
    name: str

    @abstractmethod
    def fetch_odds(self, *, tournament_ref: str, book: str) -> list[OddsSnapshot]:
        """Fetch current odds for every fixture on a tournament's board. Return
        an empty list if the tournament currently has no fixtures with odds —
        that is expected off-season, not an error."""
