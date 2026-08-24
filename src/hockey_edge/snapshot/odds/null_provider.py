"""Null odds provider -- makes zero HTTP requests, always returns no snapshots.

Exists so Phase 2 (fixture discovery, the scheduler, window logic, the
satisfaction table, api_usage, --dry-run) can ship and run for real against
this season's live preseason games without wiring a real odds source.

Why: Phase 1 recon (docs/SNAPSHOT_FINDINGS.md) found /odds-by-tournaments
returns no price data, just a hasOdds flag -- real per-fixture pricing needs a
different endpoint whose request-cost-per-game-night is still being decided
(2026-08-23 OddsPapi docs recon). Which provider ends up primary is an open
decision, made outside this session. Everything downstream of `OddsProvider`
is written against the interface, not against OddsPapi specifically, so
swapping this out for a real provider later is a one-line change in job.py.

With this provider wired, every capture_windows row will correctly progress
pending -> missed once its game's start_utc passes, never pending -> satisfied
-- that's the honest state: no odds are actually being captured yet.
"""

from hockey_edge.snapshot.odds.base import OddsProvider, OddsSnapshot


class NullOddsProvider(OddsProvider):
    name = "null"
    is_stub = True

    def fetch_odds(self, *, tournament_ref: str, book: str) -> list[OddsSnapshot]:
        return []
