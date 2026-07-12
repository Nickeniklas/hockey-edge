"""Throwaway probe: find Liiga's OddsPapi tournamentId and count HTTP requests
made, so the count can be diffed against the OddsPapi dashboard usage counter to
confirm billing semantics (per-fixture vs per-sport-board).

Run manually: python scripts/oddspapi_probe.py [tournament_id]

Context: docs/PLAN.md flags OddsPapi billing semantics as unverified — this is
that verification, per docs/DATA_PIPELINE.md Layer 2 / OddsPapi section.
"""

import os
import sys
import time
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

BASE_URL = "https://api.oddspapi.io/v4"
ICE_HOCKEY_SPORT_ID = 15
REQUEST_DELAY_SECONDS = 1.5
LIIGA_NAME_HINTS = ("liiga", "sm-liiga", "finland")


def load_api_key() -> str:
    load_dotenv()
    api_key = os.environ.get("ODDSPAPI_KEY")
    if not api_key:
        print("ODDSPAPI_KEY not set in .env", file=sys.stderr)
        sys.exit(1)
    return api_key


def redact(text: str, api_key: str) -> str:
    return text.replace(api_key, "***REDACTED***")


class RequestCounter:
    """Counts every HTTP request actually sent, success or failure — billing
    counts the hit regardless of status code, so this must too."""

    def __init__(self) -> None:
        self.count = 0


def request_json(
    path: str, params: dict, api_key: str, counter: RequestCounter
) -> list | dict | None:
    """GET path, print the request with apiKey stripped, and on any error print
    a redacted response body instead of letting requests' default exception
    (which embeds the full URL incl. apiKey) reach the console. Returns None on
    a non-2xx response instead of exiting, so the caller can decide whether
    that's expected (e.g. 404 = no fixtures) and still report the true request
    count."""
    display_qs = urlencode(params)
    print(f"GET {BASE_URL}{path}?{display_qs}")
    try:
        resp = requests.get(
            f"{BASE_URL}{path}", params={**params, "apiKey": api_key}, timeout=15
        )
    except requests.RequestException as exc:
        counter.count += 1
        print(f"  -> request failed: {redact(str(exc), api_key)}", file=sys.stderr)
        return None
    counter.count += 1
    if not resp.ok:
        print(
            f"  -> HTTP {resp.status_code}: {redact(resp.text, api_key)[:1000]}",
        )
        return None
    body = resp.json()
    count = len(body) if isinstance(body, list) else 1
    print(f"  -> HTTP {resp.status_code}, {count} item(s) returned")
    return body


def is_liiga_candidate(tournament: dict) -> bool:
    name = str(tournament.get("tournamentName", "")).lower()
    slug = str(tournament.get("tournamentSlug", "")).lower()
    return any(hint in name or hint in slug for hint in LIIGA_NAME_HINTS)


def main() -> None:
    api_key = load_api_key()
    counter = RequestCounter()

    # Optional: skip tournament disambiguation on a re-run, e.g. after a prior
    # run printed multiple candidates and you picked one by hand.
    forced_tournament_id = sys.argv[1] if len(sys.argv) > 1 else None

    tournaments = request_json(
        "/tournaments", {"sportId": ICE_HOCKEY_SPORT_ID}, api_key, counter
    )
    if tournaments is None:
        print(f"\nTotal HTTP requests made: {counter.count}")
        sys.exit(1)

    candidates = [t for t in tournaments if is_liiga_candidate(t)]

    print("\nCandidate tournaments matching 'liiga' / 'sm-liiga' / 'finland':")
    if not candidates:
        print("  none matched by name/slug hint — full tournament list follows, "
              "pick manually:")
        for t in tournaments:
            print(f"  tournamentId={t.get('tournamentId')} "
                  f"name={t.get('tournamentName')!r} category={t.get('categoryName')!r}")
        print(f"\nTotal HTTP requests made: {counter.count}")
        return

    for t in candidates:
        print(f"  tournamentId={t.get('tournamentId')} name={t.get('tournamentName')!r} "
              f"category={t.get('categoryName')!r} slug={t.get('tournamentSlug')!r} "
              f"futureFixtures={t.get('futureFixtures')} "
              f"upcomingFixtures={t.get('upcomingFixtures')}")

    if forced_tournament_id is not None:
        tournament_id = forced_tournament_id
        print(f"\nUsing manually-specified tournamentId={tournament_id} "
              f"(skipping auto-pick among {len(candidates)} candidates).")
    elif len(candidates) != 1:
        print("\nMultiple candidates found — not guessing which is Liiga. Pick the "
              "tournamentId manually and re-run as "
              "`python scripts/oddspapi_probe.py <tournamentId>` before wiring this "
              "into the snapshot job.")
        print(f"\nTotal HTTP requests made: {counter.count}")
        return
    else:
        tournament_id = candidates[0]["tournamentId"]

    time.sleep(REQUEST_DELAY_SECONDS)

    odds = request_json(
        "/odds-by-tournaments",
        {"tournamentIds": tournament_id, "bookmaker": "pinnacle"},
        api_key,
        counter,
    )
    if odds is None:
        print("  (a 404/FIXTURE_NOT_FOUND here is expected in July off-season — "
              "Liiga has no upcoming fixtures with odds yet. Still counts as a "
              "billed request.)")
    elif not odds:
        print("  (expected: July off-season, no Liiga fixtures with odds posted yet)")

    print(f"\nTotal HTTP requests made: {counter.count}")
    print("Diff this against the OddsPapi dashboard usage counter to confirm billing "
          "is per-HTTP-request (not per-fixture or per-bookmaker-board).")


if __name__ == "__main__":
    main()
