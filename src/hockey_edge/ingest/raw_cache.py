"""Rate-limited, resumable fetch-and-cache of liiga.fi JSON responses.

Every call goes through `fetch()`, which never hardcodes a URL — callers pass
an `Endpoint` from `hockey_edge.ingest.liiga.endpoints` plus the params to
fill its template. Raw response bodies are written once as files under
data/raw/liiga/<endpoint>/ (gitignored) and never rewritten; `sync_state`
tracks per-entity fetch status so a re-run only fetches what's missing or
previously failed retryably — a `status='success'` row means "load the cached
file, don't hit the network again."

Two endpoints (game_stats, shotmap) are documented as recent-seasons-only:
they 500 (or return a 200 with an error-shaped body) for old game_ids. Those
failures are recorded as `failed_permanent` so a resumable backfill doesn't
retry them forever — see docs/SCHEMA_DRAFT.md point 4 and CLAUDE.md's Gotchas.
"""

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from hockey_edge.ingest.liiga.endpoints import Endpoint

RAW_DIR = Path(__file__).resolve().parents[3] / "data" / "raw" / "liiga"

# A normal desktop Chrome UA, matching the "polite scraping" convention used
# during endpoint discovery (see endpoints.py docstring) — no special headers,
# no auth, just look like an ordinary browser tab.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 20

# Endpoints known to 500 (or return a 200 with an error-shaped placeholder
# body) on old game_ids — treat their failures as permanent, not retryable.
RECENT_SEASONS_ONLY_ENDPOINTS = {"game_stats", "shotmap"}

logger = logging.getLogger("hockey_edge.ingest")


@dataclass
class FetchResult:
    data: Any
    status: str  # 'success' | 'failed_retryable' | 'failed_permanent'
    from_cache: bool
    raw_response_id: int | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _looks_like_error_placeholder(data: Any) -> bool:
    """Detect the '{"stats": "Remote server error"}'-style 200-with-an-error-
    string body seen on game_stats for old seasons, distinct from a genuine
    HTTP error status."""
    if not isinstance(data, dict) or len(data) != 1:
        return False
    (value,) = data.values()
    return isinstance(value, str) and "error" in value.lower()


def _last_raw_response(
    conn: sqlite3.Connection, league: str, endpoint: str, entity_id: str
) -> tuple[int, Path] | None:
    row = conn.execute(
        "SELECT id, file_path FROM raw_responses "
        "WHERE league = ? AND endpoint = ? AND entity_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (league, endpoint, entity_id),
    ).fetchone()
    if row is None:
        return None
    raw_id, file_path = row
    return raw_id, Path(__file__).resolve().parents[3] / file_path


def _sync_status(
    conn: sqlite3.Connection, league: str, endpoint: str, entity_id: str
) -> str | None:
    row = conn.execute(
        "SELECT status FROM sync_state WHERE league = ? AND endpoint = ? AND entity_id = ?",
        (league, endpoint, entity_id),
    ).fetchone()
    return row[0] if row else None


def _record_sync_state(
    conn: sqlite3.Connection,
    *,
    league: str,
    endpoint: str,
    entity_id: str,
    season: int | None,
    status: str,
    http_status: int | None,
    content_hash: str | None,
    error: str | None,
) -> None:
    conn.execute(
        "INSERT INTO sync_state "
        "(league, endpoint, entity_id, season, status, http_status, content_hash, fetched_at, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (league, endpoint, entity_id) DO UPDATE SET "
        "season=excluded.season, status=excluded.status, http_status=excluded.http_status, "
        "content_hash=excluded.content_hash, fetched_at=excluded.fetched_at, error=excluded.error",
        (league, endpoint, entity_id, season, status, http_status, content_hash, _now_iso(), error),
    )
    conn.commit()


def _record_raw_response(
    conn: sqlite3.Connection,
    *,
    league: str,
    endpoint: str,
    entity_id: str,
    season: int | None,
    url: str,
    http_status: int,
    content_hash: str,
    file_path: Path,
) -> int:
    repo_root = Path(__file__).resolve().parents[3]
    cur = conn.execute(
        "INSERT INTO raw_responses "
        "(league, endpoint, entity_id, season, url, fetched_at, http_status, content_hash, file_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            league,
            endpoint,
            entity_id,
            season,
            url,
            _now_iso(),
            http_status,
            content_hash,
            str(file_path.relative_to(repo_root)).replace("\\", "/"),
        ),
    )
    conn.commit()
    return cur.lastrowid


def fetch(
    conn: sqlite3.Connection,
    endpoint: Endpoint,
    entity_id: str,
    url: str,
    *,
    season: int | None = None,
    league: str = "liiga",
    force: bool = False,
) -> FetchResult:
    """Fetch `url` (already filled in from `endpoint.url_template`), caching
    the raw body to disk and recording sync_state. Idempotent: if this
    (league, endpoint, entity_id) already has a 'success' row and `force` is
    False, loads the cached file instead of hitting the network. A prior
    'failed_permanent' row (recent-seasons-only 500s) is also skipped without
    a network call unless `force`."""
    prior_status = _sync_status(conn, league, endpoint.name, entity_id)

    if not force and prior_status == "success":
        cached = _last_raw_response(conn, league, endpoint.name, entity_id)
        if cached is not None and cached[1].exists():
            raw_id, cached_path = cached
            data = json.loads(cached_path.read_text(encoding="utf-8"))
            return FetchResult(data=data, status="success", from_cache=True, raw_response_id=raw_id)

    if not force and prior_status == "failed_permanent":
        logger.info(
            "skipping %s entity_id=%s — previously recorded failed_permanent "
            "(recent-seasons-only endpoint, old data confirmed unavailable)",
            endpoint.name,
            entity_id,
        )
        return FetchResult(data=None, status="failed_permanent", from_cache=True)

    time.sleep(REQUEST_DELAY_SECONDS)
    try:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        logger.error("fetch failed (network error) %s entity_id=%s: %s", endpoint.name, entity_id, exc)
        _record_sync_state(
            conn, league=league, endpoint=endpoint.name, entity_id=entity_id, season=season,
            status="failed_retryable", http_status=None, content_hash=None, error=str(exc),
        )
        return FetchResult(data=None, status="failed_retryable", from_cache=False)

    is_recent_seasons_only = endpoint.name in RECENT_SEASONS_ONLY_ENDPOINTS

    if resp.status_code >= 500:
        status = "failed_permanent" if is_recent_seasons_only else "failed_retryable"
        logger.warning(
            "fetch %s entity_id=%s -> HTTP %d (%s)", endpoint.name, entity_id, resp.status_code, status
        )
        _record_sync_state(
            conn, league=league, endpoint=endpoint.name, entity_id=entity_id, season=season,
            status=status, http_status=resp.status_code, content_hash=None,
            error=f"HTTP {resp.status_code}",
        )
        return FetchResult(data=None, status=status, from_cache=False)

    if not resp.ok:
        logger.error("fetch %s entity_id=%s -> HTTP %d", endpoint.name, entity_id, resp.status_code)
        _record_sync_state(
            conn, league=league, endpoint=endpoint.name, entity_id=entity_id, season=season,
            status="failed_retryable", http_status=resp.status_code, content_hash=None,
            error=f"HTTP {resp.status_code}",
        )
        return FetchResult(data=None, status="failed_retryable", from_cache=False)

    data = resp.json()

    if is_recent_seasons_only and _looks_like_error_placeholder(data):
        logger.info(
            "fetch %s entity_id=%s -> HTTP 200 with error-placeholder body %r "
            "(recent-seasons-only endpoint, treating as failed_permanent)",
            endpoint.name, entity_id, data,
        )
        _record_sync_state(
            conn, league=league, endpoint=endpoint.name, entity_id=entity_id, season=season,
            status="failed_permanent", http_status=resp.status_code, content_hash=None,
            error=json.dumps(data),
        )
        return FetchResult(data=None, status="failed_permanent", from_cache=False)

    body = resp.content
    content_hash = _content_hash(body)
    out_dir = RAW_DIR / endpoint.name
    out_dir.mkdir(parents=True, exist_ok=True)
    # ':' must be sanitized too, not just '/': entity_ids like "2024:runkosarja"
    # (season-scoped games_by_season fetches, one per tournament phase) hit
    # Windows/NTFS's alternate-data-stream syntax ("file:stream") on a raw
    # colon — it silently succeeds but writes into a hidden stream invisible
    # to ls/Explorer/git instead of erroring, so this can't be caught by a
    # try/except on the write.
    safe_entity_id = entity_id.replace("/", "_").replace(":", "-")
    file_path = out_dir / f"{safe_entity_id}__{content_hash[:8]}.json"
    if not file_path.exists():
        file_path.write_bytes(body)

    raw_response_id = _record_raw_response(
        conn, league=league, endpoint=endpoint.name, entity_id=entity_id, season=season,
        url=url, http_status=resp.status_code, content_hash=content_hash, file_path=file_path,
    )
    _record_sync_state(
        conn, league=league, endpoint=endpoint.name, entity_id=entity_id, season=season,
        status="success", http_status=resp.status_code, content_hash=content_hash, error=None,
    )
    logger.info("fetched %s entity_id=%s -> HTTP %d, cached to %s", endpoint.name, entity_id, resp.status_code, file_path)
    return FetchResult(data=data, status="success", from_cache=False, raw_response_id=raw_response_id)
