"""Ordered-logit link: valid probabilities, the right shape, a working fit.

Run: python -m unittest discover -s tests
"""

import unittest

import numpy as np

from hockey_edge.models import CLASSES, link

PARAMS = link.LinkParams(c1=-0.7, c2=0.3, beta=2.5)


class LinkTest(unittest.TestCase):
    def test_probabilities_are_valid(self):
        probs = link.predict(PARAMS, np.linspace(-2, 2, 81))
        self.assertEqual(probs.shape, (81, 3))
        np.testing.assert_allclose(probs.sum(axis=1), 1.0, atol=1e-12)
        self.assertTrue(((probs > 0) & (probs < 1)).all())

    def test_columns_follow_the_cutpoints(self):
        home, draw, away = link.predict(PARAMS, [0.0])[0]
        self.assertAlmostEqual(away, 1 / (1 + np.exp(0.7)))
        self.assertAlmostEqual(away + draw, 1 / (1 + np.exp(-0.3)))
        self.assertAlmostEqual(home, 1 - 1 / (1 + np.exp(-0.3)))

    def test_home_rises_and_away_falls_with_the_gap(self):
        probs = link.predict(PARAMS, np.linspace(-2, 2, 81))
        self.assertTrue((np.diff(probs[:, 0]) > 0).all())
        self.assertTrue((np.diff(probs[:, 2]) < 0).all())

    def test_draw_peaks_between_the_cutpoints(self):
        x = np.linspace(-2, 2, 4001)
        peak = x[link.predict(PARAMS, x)[:, 1].argmax()]
        # Symmetric around the midpoint of c1 and c2: beta * x = (c1 + c2) / 2.
        self.assertAlmostEqual(peak, (PARAMS.c1 + PARAMS.c2) / 2 / PARAMS.beta, places=3)

    def test_fit_recovers_known_parameters(self):
        rng = np.random.default_rng(7)
        x = rng.normal(0.1, 0.25, 40_000)
        draws = rng.random(len(x))
        cumulative = np.cumsum(link.predict(PARAMS, x), axis=1)
        results = [CLASSES[i] for i in (draws[:, None] > cumulative).sum(axis=1)]
        fitted = link.fit(x, results)
        self.assertAlmostEqual(fitted.c1, PARAMS.c1, delta=0.05)
        self.assertAlmostEqual(fitted.c2, PARAMS.c2, delta=0.05)
        self.assertAlmostEqual(fitted.beta, PARAMS.beta, delta=0.15)
        self.assertLess(fitted.c1, fitted.c2)

    def test_fit_with_no_signal_returns_the_base_rates(self):
        rng = np.random.default_rng(11)
        x = rng.normal(0, 0.25, 6_000)
        results = rng.choice(CLASSES, size=len(x), p=[0.44, 0.23, 0.33])
        fitted = link.fit(x, results)
        rates = [np.mean(results == c) for c in CLASSES]
        np.testing.assert_allclose(link.predict(fitted, x).mean(axis=0), rates, atol=0.002)
        self.assertLess(fitted.beta, 0.3)

    def test_fit_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            link.fit([0.1, 0.2], ["H"])
        with self.assertRaises(link.LinkFitError):
            link.fit([0.1, 0.2, 0.3], ["H", "A", "H"])


if __name__ == "__main__":
    unittest.main()
