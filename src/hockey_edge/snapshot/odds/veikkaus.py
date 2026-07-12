"""Veikkaus scrape fallback for Liiga odds — STUBBED, not implemented.

docs/PLAN.md and docs/DATA_PIPELINE.md name this as the guaranteed fallback if
OddsPapi coverage or budget turns out insufficient in-season: Veikkaus posts
odds on every Liiga game and is the book actually bettable in Finland (also the
practical closing-line benchmark if Pinnacle-via-OddsPapi is unavailable).

Not built yet because OddsPapi is confirmed working (2026-07-12) and untested
in-season — build this out only if OddsPapi turns out insufficient once real
Liiga games are on the board. Requires a real scrape (rate-limited, normal UA,
review veikkaus.fi ToS first per hard rule 6).
"""

from hockey_edge.snapshot.odds.base import OddsProvider, OddsSnapshot


class VeikkausProvider(OddsProvider):
    name = "veikkaus"

    def fetch_odds(self, *, tournament_ref: str, book: str = "veikkaus") -> list[OddsSnapshot]:
        raise NotImplementedError(
            "Veikkaus fallback not built yet — see module docstring and "
            "docs/DATA_PIPELINE.md OddsPapi section."
        )
