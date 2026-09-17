"""Parser tests against real OddsPapi board responses (fixtures/oddspapi/).

Run: python -m unittest discover -s tests
"""

import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hockey_edge.snapshot.odds.oddspapi import parse_fixture

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "oddspapi"
CAPTURED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


def load_board(book: str) -> list[dict]:
    path = FIXTURES / f"odds-by-tournaments_134_{book}_2026-09-17.json"
    return json.loads(path.read_text(encoding="utf-8"))


def by_market(snapshots):
    return {s.market: s for s in snapshots}


class CleanBoardsTest(unittest.TestCase):
    def test_every_fixture_parses_both_markets(self):
        for book, n_fixtures in (("pinnacle", 7), ("bet365", 5)):
            board = load_board(book)
            self.assertEqual(len(board), n_fixtures)
            for fixture in board:
                with self.subTest(book=book, fixture=fixture["fixtureId"]):
                    snaps = by_market(parse_fixture(fixture, book=book, captured_at=CAPTURED_AT))
                    self.assertEqual(set(snaps), {"1x2_regulation", "moneyline_incl_ot"})
                    self.assertTrue(all(s.parsed for s in snaps.values()))
                    self.assertIsNotNone(snaps["1x2_regulation"].draw_odds)
                    self.assertIsNone(snaps["moneyline_incl_ot"].draw_odds)
                    # raw_payload round-trips to the same fixture -> reparse without refetch
                    self.assertEqual(json.loads(snaps["1x2_regulation"].raw_payload), fixture)

    def test_known_prices(self):
        # Jukurit (home) v Pelicans, 2026-09-18, bet365 — values read off the raw fixture.
        fixture = next(f for f in load_board("bet365") if f["participant1Id"] == 3824)
        snaps = by_market(parse_fixture(fixture, book="bet365", captured_at=CAPTURED_AT))
        one_x_two = snaps["1x2_regulation"]
        self.assertEqual((one_x_two.home_odds, one_x_two.draw_odds, one_x_two.away_odds), (2.8, 4.0, 2.2))
        ml = snaps["moneyline_incl_ot"]
        self.assertEqual((ml.home_odds, ml.away_odds), (2.02, 1.72))

    def test_favourite_direction_agrees_between_1x2_and_moneyline(self):
        """Outcome-mapping check: if 153/155 were swapped relative to 151/152,
        the 1X2 favourite would contradict the 2-way favourite on some game."""
        for book in ("pinnacle", "bet365"):
            for fixture in load_board(book):
                snaps = by_market(parse_fixture(fixture, book=book, captured_at=CAPTURED_AT))
                a, b = snaps["1x2_regulation"], snaps["moneyline_incl_ot"]
                with self.subTest(book=book, fixture=fixture["fixtureId"]):
                    self.assertEqual(
                        (a.home_odds > a.away_odds) - (a.home_odds < a.away_odds),
                        (b.home_odds > b.away_odds) - (b.home_odds < b.away_odds),
                    )

    def test_bet365_favourite_agrees_with_pinnacle(self):
        pinnacle = {f["fixtureId"]: f for f in load_board("pinnacle")}
        shared = 0
        for fixture in load_board("bet365"):
            if fixture["fixtureId"] not in pinnacle:
                continue
            shared += 1
            b = by_market(parse_fixture(fixture, book="bet365", captured_at=CAPTURED_AT))["1x2_regulation"]
            p = by_market(parse_fixture(pinnacle[fixture["fixtureId"]], book="pinnacle",
                                        captured_at=CAPTURED_AT))["1x2_regulation"]
            with self.subTest(fixture=fixture["fixtureId"]):
                if b.home_odds != b.away_odds:
                    self.assertEqual(b.home_odds < b.away_odds, p.home_odds < p.away_odds)
        self.assertEqual(shared, 4)


class RejectionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = load_board("pinnacle")[0]

    def _parse(self, fixture, book="pinnacle"):
        return by_market(parse_fixture(fixture, book=book, captured_at=CAPTURED_AT))

    def test_betsson_suspended_and_missing(self):
        board = load_board("betsson")
        no_odds = parse_fixture(board[0], book="betsson", captured_at=CAPTURED_AT)
        self.assertEqual(len(no_odds), 1)
        self.assertIsNone(no_odds[0].market)
        self.assertFalse(no_odds[0].parsed)
        suspended = parse_fixture(board[1], book="betsson", captured_at=CAPTURED_AT)
        self.assertTrue(suspended)
        self.assertFalse(any(s.parsed for s in suspended))
        self.assertTrue(all(s.home_odds is None for s in suspended))

    def test_inactive_outcome(self):
        f = copy.deepcopy(self.fixture)
        f["bookmakerOdds"]["pinnacle"]["markets"]["153"]["outcomes"]["154"]["players"]["0"]["active"] = False
        snaps = self._parse(f)
        self.assertFalse(snaps["1x2_regulation"].parsed)
        self.assertTrue(snaps["moneyline_incl_ot"].parsed)

    def test_missing_outcome(self):
        f = copy.deepcopy(self.fixture)
        del f["bookmakerOdds"]["pinnacle"]["markets"]["153"]["outcomes"]["155"]
        self.assertFalse(self._parse(f)["1x2_regulation"].parsed)

    def test_overround_outside_band(self):
        f = copy.deepcopy(self.fixture)
        for o in f["bookmakerOdds"]["pinnacle"]["markets"]["153"]["outcomes"].values():
            o["players"]["0"]["price"] = 2.0  # 1.5 overround
        self.assertFalse(self._parse(f)["1x2_regulation"].parsed)

    def test_swapped_text_label_is_rejected(self):
        f = copy.deepcopy(self.fixture)
        outcomes = f["bookmakerOdds"]["pinnacle"]["markets"]["153"]["outcomes"]
        outcomes["153"]["players"]["0"]["bookmakerOutcomeId"] = "away"
        self.assertFalse(self._parse(f)["1x2_regulation"].parsed)

    def test_missing_market_skipped_other_kept(self):
        f = copy.deepcopy(self.fixture)
        del f["bookmakerOdds"]["pinnacle"]["markets"]["151"]
        snaps = self._parse(f)
        self.assertEqual(set(snaps), {"1x2_regulation"})
        self.assertTrue(snaps["1x2_regulation"].parsed)


if __name__ == "__main__":
    unittest.main()
