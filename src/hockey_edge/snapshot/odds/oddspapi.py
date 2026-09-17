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

Parsing (implemented 2026-09-17 against in-season board responses for
pinnacle and bet365, fixtures/oddspapi/odds-by-tournaments_134_*_2026-09-17.json;
see docs/ODDS_PLAN.md):
- One board response = every posted fixture, for exactly one bookmaker. Each
  fixture yields one OddsSnapshot per parsed market, each carrying the full
  per-fixture JSON in raw_payload, so parse_fixture() can be re-run on stored
  rows without refetching.
- Market 153 = 3-way regulation 1X2, outcomes 153/154/155 = home/draw/away.
  Market 151 = 2-way moneyline incl. OT, outcomes 151/152 = home/away.
  Pinnacle labels its outcomes literally ('home'/'draw'/'away') and they match
  these ids on every fixture sampled; bet365's labels are opaque numbers, so
  for bet365 the mapping rests on the shared outcome ids plus favourite
  direction agreeing with Pinnacle's. Where a book does label outcomes as
  text, a label that contradicts the expected role fails the row.
- participant1Id is the home team (checked against hockey.db).
- A market is parsed=True only if every outcome is present, active, priced
  above 1.0, and the overround sits inside OVERROUND_BAND; anything else is
  parsed=False with a WARNING, raw kept. A fixture with neither market usable
  still yields one parsed=False row (market=None) so its payload is kept.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from hockey_edge.snapshot.odds.base import OddsProvider, OddsSnapshot

logger = logging.getLogger(__name__)

BASE_URL = "https://api.oddspapi.io/v4"
LIIGA_TOURNAMENT_ID = "134"

# market id -> (stored market name, [(outcome id, role), ...]) in home/draw/away order.
MARKETS = {
    "153": ("1x2_regulation", [("153", "home"), ("154", "draw"), ("155", "away")]),
    "151": ("moneyline_incl_ot", [("151", "home"), ("152", "away")]),
}

# Sum of implied probabilities. Observed 2026-09-17: 1.049–1.079 across
# pinnacle/bet365/betsson; paf/unibet's 1.30 on the same game is the kind of
# value this exists to reject. Below 1.0 would be a free arbitrage — bad data.
OVERROUND_BAND = (1.0, 1.15)

_ROLE_LABELS = ("home", "draw", "away")

TEAM_MAP_PATH = Path(__file__).with_name("oddspapi_teams.json")


def load_team_map(path: Path = TEAM_MAP_PATH) -> dict[int, str]:
    """Curated participant id -> liiga.fi teamId (see the file's _about)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {team["participant_id"]: team["liiga_team_id"] for team in doc["teams"]}


def _normalize_start(start_time: str | None) -> str | None:
    """'2026-09-18T15:30:00.000Z' -> '2026-09-18T15:30:00Z', the format
    discovered_fixtures.start_utc and capture_windows.due_at use."""
    if not start_time:
        return None
    dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _redact_api_key(text: str) -> str:
    """apiKey is a query param (never a header), so it lands in requests'
    default HTTPError message (which includes resp.url) and in resp.url
    itself. A bare raise_for_status()/str(exc) would leak it into a log line
    or an unhandled traceback -- redact before it can reach either."""
    return re.sub(r"(apiKey=)[^&\s]+", r"\1<redacted>", text)


def _parse_market(market: dict, outcome_roles: list[tuple[str, str]]) -> tuple[dict | None, str | None]:
    """Return ({role: price}, None) if the market passes every check, else
    (None, reason)."""
    if not market.get("marketActive"):
        return None, "market inactive"
    prices: dict[str, float] = {}
    for outcome_id, role in outcome_roles:
        outcome = market.get("outcomes", {}).get(outcome_id)
        player = (outcome or {}).get("players", {}).get("0")
        if player is None:
            return None, f"outcome {outcome_id} ({role}) missing"
        if not player.get("active"):
            return None, f"outcome {outcome_id} ({role}) inactive"
        label = str(player.get("bookmakerOutcomeId") or "").lower()
        label_role = next((r for r in _ROLE_LABELS if label.endswith(r)), None)
        if label_role is not None and label_role != role:
            return None, f"outcome {outcome_id} expected {role}, book labels it {label!r}"
        price = player.get("price")
        if not isinstance(price, (int, float)) or price <= 1.0:
            return None, f"outcome {outcome_id} ({role}) bad price {price!r}"
        prices[role] = float(price)
    overround = sum(1 / p for p in prices.values())
    low, high = OVERROUND_BAND
    if not (low < overround <= high):
        return None, f"overround {overround:.3f} outside {OVERROUND_BAND}"
    return prices, None


def parse_fixture(
    fixture: dict, *, book: str, captured_at: datetime, league: str = "liiga"
) -> list[OddsSnapshot]:
    """Turn one board fixture into OddsSnapshots — one per known market,
    parsed or not, or a single market=None row if the book has no markets
    for it at all. Pure: no I/O, safe to re-run on a stored raw_payload."""
    fixture_ref = str(fixture.get("fixtureId"))
    raw_payload = json.dumps(fixture, ensure_ascii=False)
    base = dict(league=league, book=book, fixture_ref=fixture_ref,
                captured_at=captured_at, raw_payload=raw_payload,
                home_participant_id=fixture.get("participant1Id"),
                away_participant_id=fixture.get("participant2Id"),
                start_utc=_normalize_start(fixture.get("startTime")))

    book_odds = (fixture.get("bookmakerOdds") or {}).get(book)
    if not book_odds:
        logger.warning("oddspapi: fixture %s has no odds for book=%s — raw kept, unparsed", fixture_ref, book)
        return [OddsSnapshot(**base)]
    book_unusable = None
    if not book_odds.get("bookmakerIsActive"):
        book_unusable = "bookmaker inactive"
    elif book_odds.get("suspended"):
        book_unusable = "suspended"

    snapshots = []
    markets = book_odds.get("markets") or {}
    for market_id, (market_name, outcome_roles) in MARKETS.items():
        if market_id not in markets:
            logger.warning(
                "oddspapi: fixture %s book=%s has no market %s (%s)",
                fixture_ref, book, market_id, market_name,
            )
            continue
        if book_unusable:
            prices, reason = None, book_unusable
        else:
            prices, reason = _parse_market(markets[market_id], outcome_roles)
        if prices is None:
            logger.warning(
                "oddspapi: fixture %s book=%s market=%s unparsed: %s — raw kept",
                fixture_ref, book, market_name, reason,
            )
            snapshots.append(OddsSnapshot(**base, market=market_name))
            continue
        snapshots.append(OddsSnapshot(
            **base,
            market=market_name,
            home_odds=prices["home"],
            draw_odds=prices.get("draw"),
            away_odds=prices["away"],
            parsed=True,
        ))
    return snapshots or [OddsSnapshot(**base)]


class OddsPapiProvider(OddsProvider):
    name = "oddspapi"

    def __init__(self, api_key: str | None = None, timeout: float = 15.0):
        self.api_key = api_key or os.environ["ODDSPAPI_KEY"]
        self.timeout = timeout

    def fetch_odds(
        self, *, tournament_ref: str = LIIGA_TOURNAMENT_ID, book: str = "pinnacle"
    ) -> list[OddsSnapshot]:
        captured_at = datetime.now(timezone.utc)
        try:
            resp = requests.get(
                f"{BASE_URL}/odds-by-tournaments",
                params={
                    "tournamentIds": tournament_ref,
                    "bookmaker": book,
                    "apiKey": self.api_key,
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            # A connection/timeout error's str() can embed the request URL
            # (with apiKey) -- redact before it's allowed to propagate.
            raise RuntimeError(_redact_api_key(str(exc))) from None

        if resp.status_code == 404:
            body = resp.json()
            if body.get("error", {}).get("code") == "FIXTURE_NOT_FOUND":
                logger.info(
                    "oddspapi: no fixtures with odds (tournament=%s book=%s)",
                    tournament_ref,
                    book,
                )
                return []
            raise RuntimeError(f"oddspapi: unexpected 404 body: {_redact_api_key(json.dumps(body))}")

        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(_redact_api_key(str(exc))) from None
        fixtures = resp.json()

        snapshots = [
            snapshot
            for fixture in fixtures
            for snapshot in parse_fixture(fixture, book=book, captured_at=captured_at)
        ]
        logger.info(
            "oddspapi: captured %d fixture(s), %d snapshot(s), %d parsed (tournament=%s book=%s)",
            len(fixtures),
            len(snapshots),
            sum(s.parsed for s in snapshots),
            tournament_ref,
            book,
        )
        return snapshots
