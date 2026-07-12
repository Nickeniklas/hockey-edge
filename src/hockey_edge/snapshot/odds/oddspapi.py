"""OddsPapi implementation of OddsProvider — Liiga odds.

Verified 2026-07-12 against a real free-tier key (see docs/DATA_PIPELINE.md
OddsPapi section and scripts/oddspapi_probe.py for how this was confirmed):
- Base URL https://api.oddspapi.io/v4; auth is `apiKey` as a query param.
- Liiga tournamentId = 134 (not 34596 = Auroraliiga women's league, not 48851 =
  Estonia's Hokiliiga — both matched a naive name search and are wrong).
- A tournament with zero fixtures currently posted returns HTTP 404 with
  {"error": {"code": "FIXTURE_NOT_FOUND"}} rather than HTTP 200 with []; this is
  the expected off-season response and is handled as "no snapshots", not a
  failure.

Odds parsing (home/draw/away extraction from bookmakerOdds.markets.outcomes) is
NOT implemented yet — this session only saw FIXTURE_NOT_FOUND responses
(off-season), never a real fixture payload, so the market/outcome id mapping for
ice hockey 1X2 is unconfirmed. Implement `_parse_fixture` once a live in-season
response can be inspected; until then every snapshot is written with
parsed=False and the full fixture JSON preserved in raw_payload so parsing can
be done retroactively without recapturing (missed capture is the only
unrecoverable failure mode here).
"""

import json
import logging
import os
from datetime import datetime, timezone

import requests

from hockey_edge.snapshot.odds.base import OddsProvider, OddsSnapshot

logger = logging.getLogger(__name__)

BASE_URL = "https://api.oddspapi.io/v4"
LIIGA_TOURNAMENT_ID = "134"


class OddsPapiProvider(OddsProvider):
    name = "oddspapi"

    def __init__(self, api_key: str | None = None, timeout: float = 15.0):
        self.api_key = api_key or os.environ["ODDSPAPI_KEY"]
        self.timeout = timeout

    def fetch_odds(
        self, *, tournament_ref: str = LIIGA_TOURNAMENT_ID, book: str = "pinnacle"
    ) -> list[OddsSnapshot]:
        captured_at = datetime.now(timezone.utc)
        resp = requests.get(
            f"{BASE_URL}/odds-by-tournaments",
            params={
                "tournamentIds": tournament_ref,
                "bookmaker": book,
                "apiKey": self.api_key,
            },
            timeout=self.timeout,
        )

        if resp.status_code == 404:
            body = resp.json()
            if body.get("error", {}).get("code") == "FIXTURE_NOT_FOUND":
                logger.info(
                    "oddspapi: no fixtures with odds (tournament=%s book=%s)",
                    tournament_ref,
                    book,
                )
                return []
            raise RuntimeError(f"oddspapi: unexpected 404 body: {body}")

        resp.raise_for_status()
        fixtures = resp.json()

        snapshots = [
            OddsSnapshot(
                league="liiga",
                book=book,
                fixture_ref=str(fixture.get("fixtureId")),
                captured_at=captured_at,
                raw_payload=json.dumps(fixture),
                parsed=False,
            )
            for fixture in fixtures
        ]
        logger.info(
            "oddspapi: captured %d fixture(s) (tournament=%s book=%s)",
            len(snapshots),
            tournament_ref,
            book,
        )
        return snapshots
