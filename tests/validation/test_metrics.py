"""Metrics on hand-computed cases.

Run: python -m unittest discover -s tests
"""

import math
import unittest

import numpy as np

from hockey_edge.validation import metrics

# Columns are home, draw, away.
PROBS = [[0.5, 0.3, 0.2], [0.2, 0.3, 0.5]]
RESULTS = ["H", "D"]


class ScoreTest(unittest.TestCase):
    def test_log_loss(self):
        np.testing.assert_allclose(metrics.game_log_loss(PROBS, RESULTS), [-math.log(0.5), -math.log(0.3)])
        self.assertAlmostEqual(metrics.log_loss(PROBS, RESULTS), (0.693147 + 1.203973) / 2, places=6)
        # Base rates of a three-way coin: ln 3.
        self.assertAlmostEqual(metrics.log_loss([[1 / 3] * 3], ["A"]), math.log(3))

    def test_brier(self):
        # (0.5^2 + 0.3^2 + 0.2^2) and (0.2^2 + 0.7^2 + 0.5^2)
        self.assertAlmostEqual(metrics.brier(PROBS, RESULTS), (0.38 + 0.78) / 2)

    def test_rps(self):
        # H: cumulative (0.5, 0.8) against (1, 1). D: (0.2, 0.5) against (0, 1).
        self.assertAlmostEqual(metrics.rps(PROBS, RESULTS), ((0.25 + 0.04) / 2 + (0.04 + 0.25) / 2) / 2)
        # Missing by two places costs more than missing by one.
        self.assertGreater(metrics.rps([[0.8, 0.1, 0.1]], ["A"]), metrics.rps([[0.8, 0.1, 0.1]], ["D"]))
        self.assertEqual(metrics.rps([[1.0, 0.0, 0.0]], ["H"]), 0.0)
        self.assertEqual(metrics.rps([[1.0, 0.0, 0.0]], ["A"]), 1.0)

    def test_accuracy(self):
        self.assertEqual(metrics.accuracy(PROBS, RESULTS), 0.5)

    def test_paired_difference(self):
        paired = metrics.paired_difference([1.0, 2.0, 3.0], [0.5, 2.5, 2.0])
        # Differences 0.5, -0.5, 1.0: mean 1/3, sample variance 7/12.
        self.assertAlmostEqual(paired.mean, 1 / 3)
        self.assertAlmostEqual(paired.se, math.sqrt(7 / 12 / 3))
        self.assertEqual(paired.n, 3)
        with self.assertRaises(ValueError):
            metrics.paired_difference([1.0], [0.5])


class CalibrationTest(unittest.TestCase):
    def test_bins_merge_until_thirty_games(self):
        predicted = [0.02] * 10 + [0.07] * 25 + [0.12] * 40 + [0.97] * 5
        happened = [0] * 10 + [1] * 5 + [0] * 20 + [1] * 8 + [0] * 32 + [1] * 5
        bins = metrics.calibration_bins(predicted, happened)
        # 10 games are short of 30, so the first bin takes the next one too.
        # The 5 at the top are short as well and join the bin below.
        self.assertEqual([(b.low, b.high, b.n) for b in bins], [(0.0, 0.1, 35), (0.1, 1.0, 45)])
        self.assertAlmostEqual(bins[0].mean_predicted, (10 * 0.02 + 25 * 0.07) / 35)
        self.assertAlmostEqual(bins[0].observed, 5 / 35)
        self.assertAlmostEqual(bins[1].mean_predicted, (40 * 0.12 + 5 * 0.97) / 45)
        self.assertAlmostEqual(bins[1].observed, 13 / 45)
        expected = (35 * abs(5 / 35 - bins[0].mean_predicted) + 45 * abs(13 / 45 - bins[1].mean_predicted)) / 80
        self.assertAlmostEqual(metrics.expected_calibration_error(bins), expected)

    def test_full_bins_stay_apart(self):
        bins = metrics.calibration_bins([0.21] * 30 + [0.26] * 30 + [0.31] * 31, [1] * 91)
        self.assertEqual([(b.low, b.high, b.n) for b in bins], [(0.0, 0.25, 30), (0.25, 0.3, 30), (0.3, 1.0, 31)])

    def test_small_sample_is_one_bin(self):
        bins = metrics.calibration_bins([0.5] * 10, [1] * 4 + [0] * 6)
        self.assertEqual([(b.low, b.high, b.n) for b in bins], [(0.0, 1.0, 10)])
        self.assertAlmostEqual(metrics.expected_calibration_error(bins), 0.1)

    def test_edges(self):
        # 1.0 belongs to the top bin; a value on an edge to the bin above it.
        bins = metrics.calibration_bins([0.05] * 30 + [1.0] * 30, [0] * 30 + [1] * 30)
        self.assertEqual([(b.low, b.high, b.n) for b in bins], [(0.0, 0.1, 30), (0.1, 1.0, 30)])

    def test_per_class_tables(self):
        probs = np.tile([0.5, 0.3, 0.2], (40, 1))
        results = ["H"] * 20 + ["D"] * 12 + ["A"] * 8
        tables = metrics.calibration(probs, results)
        self.assertEqual(set(tables), {"H", "D", "A"})
        self.assertEqual([b.n for b in tables["D"]], [40])
        self.assertAlmostEqual(tables["D"][0].observed, 0.3)
        large = metrics.calibration_in_the_large(probs, results)
        for name, (predicted, observed) in large.items():
            self.assertAlmostEqual(predicted, observed, msg=name)


if __name__ == "__main__":
    unittest.main()
