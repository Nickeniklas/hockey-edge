"""Catalog of liiga.fi JSON API endpoints — single source of truth for URLs/shapes.

No other module should hardcode a liiga.fi URL; import from here instead.

Each verified endpoint has one real sample response checked into
fixtures/liiga/<name>/<season>.json — one file per verified season, since
historical seasons may use different endpoints or response shapes.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Endpoint:
    name: str
    url_template: str
    concept: str
    notes: str
    method: str = "GET"
    verified_seasons: tuple[str, ...] = field(default_factory=tuple)


# Placeholders from docs/DATA_PIPELINE.md — UNCONFIRMED, replace once devtools
# network captures come in.

GAME_KOKOONPANOT = Endpoint(
    name="game_kokoonpanot",
    url_template="https://liiga.fi/fi/peli/{season}/{game_id}/kokoonpanot",
    concept="lineup",
    notes="Page URL from docs, not necessarily the JSON endpoint itself — "
    "devtools capture needed to find what it actually calls.",
)

STATS_EN = Endpoint(
    name="stats_en",
    url_template="https://liiga.fi/en/stats",
    concept="stats",
    notes="Skater/goalie/team stats page — JSON API shape unknown.",
)

PELAAJAT_FI = Endpoint(
    name="pelaajat_fi",
    url_template="https://liiga.fi/fi/pelaajat",
    concept="roster",
    notes="Player list page — JSON API shape unknown.",
)

ENDPOINTS = {e.name: e for e in [GAME_KOKOONPANOT, STATS_EN, PELAAJAT_FI]}
