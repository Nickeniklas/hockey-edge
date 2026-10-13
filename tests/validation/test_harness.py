"""Walk-forward harness: folds, isolation, storage, report.

The fixture has seasons 2022, 2025 and 2027, so the folds here are 2025
(tuned on 2022) and 2027 (tuned on 2022 and 2025), with a two-config grid.

Run: python -m unittest discover -s tests
"""

import unittest
from pathlib import Path

import numpy as np

from features.support import FeatureTest
from hockey_edge.models import baselines, elo, link
from hockey_edge.validation import folds, harness, report

CONFIG = harness.BacktestConfig(
    burn_in=0, first_test=2025,
    grid={"k": (4, 16), "hfa": (40,), "r": (0.5,), "margin": (False,), "s_ot": (0.0,)},
)
ALL_MODELS = (harness.ELO, harness.ELO_FLAT_ENTRY, harness.BASE_RATE, harness.GD_RATING)

# Every played game of a season from `?` on.
POISON = (
    "UPDATE games SET home_goals = 99, away_goals = 0, "
    "finished_type = 'ENDED_DURING_REGULAR_GAME_TIME' WHERE ended = 1 AND season >= ?"
)


class FoldsTest(unittest.TestCase):
    def test_production_folds(self):
        made = folds.make_folds(range(2015, 2028), burn_in=1, first_test=2018)
        self.assertEqual([f.test_season for f in made], list(range(2018, 2028)))
        self.assertEqual(made[0].tuning_seasons, (2016, 2017))
        self.assertEqual(made[-1].tuning_seasons, tuple(range(2016, 2027)))
        for fold in made:
            self.assertLess(max(fold.tuning_seasons), fold.test_season)
            self.assertNotIn(2015, fold.tuning_seasons)

    def test_fixture_folds(self):
        made = folds.make_folds({2027, 2022, 2025}, burn_in=0, first_test=2025)
        self.assertEqual(made, [folds.Fold(2025, (2022,)), folds.Fold(2027, (2022, 2025))])

    def test_a_first_test_season_needs_tuning_seasons(self):
        with self.assertRaises(ValueError):
            folds.make_folds([2022, 2025, 2027], burn_in=1, first_test=2025)
        with self.assertRaises(ValueError):
            folds.make_folds([2022, 2025], burn_in=0, first_test=2030)


class HarnessTest(FeatureTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data = harness.load(cls.full)
        cls.results = harness.backtest(cls.data, CONFIG)
        cls.backtest = harness.connect(cls.dir / "backtest.db")
        cls.addClassCleanup(cls.backtest.close)
        cls.run_id = harness.store(cls.backtest, cls.data, CONFIG, cls.results)

    @staticmethod
    def fitted(results):
        return {(r.model, r.fold.test_season): (r.chosen_params, r.link_params) for r in results}

    def stored(self, run_id):
        return [tuple(r)[1:] for r in self.backtest.execute(
            "SELECT * FROM backtest_prediction WHERE run_id = ? ORDER BY model, season, game_id", (run_id,)
        )]

    def test_every_model_runs_every_fold(self):
        self.assertEqual(
            sorted((r.model, r.fold.test_season) for r in self.results),
            sorted((m, s) for m in ALL_MODELS for s in (2025, 2027)),
        )

    def test_fold_isolation(self):
        full = self.fitted(self.results)
        for season in (2025, 2027):
            with self.subTest(poisoned_from=season):
                hockey = self.edited_hockey(f"poison{season}", (POISON, (season,)))
                poisoned = self.build_features(f"poison{season}", hockey)
                results = harness.backtest(harness.load(poisoned), CONFIG)
                fitted = self.fitted(results)
                for model in ALL_MODELS:
                    self.assertEqual(fitted[model, season], full[model, season], model)
                # The poison is real: the fold's own test games changed.
                changed = next(r for r in results if (r.model, r.fold.test_season) == (harness.ELO, season))
                self.assertEqual({p.result for p in changed.predictions}, {"H"})
                if season == 2025:
                    # And it reaches the next fold, which tunes on 2025.
                    for model in (harness.ELO, harness.BASE_RATE, harness.GD_RATING):
                        self.assertNotEqual(fitted[model, 2027], full[model, 2027], model)

    def test_the_grid_choice_is_the_lower_tuning_loss(self):
        rows = harness._Rows(self.data.games)
        for result in self.results:
            if result.model != harness.ELO:
                continue
            losses = {}
            for k in CONFIG.grid["k"]:
                params = elo.EloParams(k=k, hfa=40, r=0.5)
                gaps = np.array([r.gap for r in elo.run(self.data.games, self.data.team_seasons, params).games])
                losses[k] = harness._fit_link(gaps, rows, result.fold)[1]
            self.assertEqual(result.chosen_params["k"], min(losses, key=losses.get))
            self.assertAlmostEqual(result.link_params["tuning_log_loss"], min(losses.values()), places=12)
            # Both values of a two-value dimension are ends; only longer ones are edges.
            self.assertEqual(result.grid_edges, {})

    def test_home_edge_is_fixed_not_tuned(self):
        self.assertNotIn("hfa", harness.DEFAULT_GRID)
        grid = harness._elo_grid(harness.DEFAULT_GRID, harness.FIXED_PARAMS)
        self.assertEqual(len(grid), 6 * 5 * 2 * 2)
        self.assertEqual({p.hfa for p in grid}, {0.0})
        # A grid entry of the same name still wins, as in this file's CONFIG.
        self.assertEqual({r.chosen_params["hfa"] for r in self.results if r.model == harness.ELO}, {40})

    def test_grid_edges(self):
        grid = {"k": (4, 8, 16), "hfa": (0, 40), "r": (0.2, 0.5, 0.8)}
        self.assertEqual(harness._grid_edges(elo.EloParams(k=4, hfa=0, r=0.5), grid), {"k": "low"})
        self.assertEqual(harness._grid_edges(elo.EloParams(k=8, hfa=40, r=0.8), grid), {"r": "high"})

    def test_predictions_belong_to_their_fold(self):
        rows = self.backtest.execute(
            "SELECT season, test_season, COUNT(*) FROM backtest_prediction WHERE run_id = ? GROUP BY 1, 2",
            (self.run_id,),
        ).fetchall()
        self.assertEqual({(r[0], r[1]) for r in rows}, {(2025, 2025), (2027, 2027)})
        # Each stored Elo row is its fold's config and frozen link, nothing else.
        for result in self.results:
            if result.model != harness.ELO:
                continue
            season = result.fold.test_season
            rated = {
                (r.season, r.game_id): r for r in elo.run(
                    self.data.games, self.data.team_seasons, elo.EloParams(**result.chosen_params)
                ).games
            }
            fitted = link.LinkParams(*(result.link_params[k] for k in ("c1", "c2", "beta")))
            stored = self.backtest.execute(
                "SELECT * FROM backtest_prediction WHERE run_id = ? AND model = 'elo' AND test_season = ?",
                (self.run_id, season),
            ).fetchall()
            self.assertGreater(len(stored), 50)
            for p in stored:
                game = rated[p["season"], p["game_id"]]
                self.assertEqual((p["gap"], p["rating_home_pre"], p["rating_away_pre"]),
                                 (game.gap, game.home_pre, game.away_pre))
                np.testing.assert_allclose(
                    (p["p_home"], p["p_draw"], p["p_away"]), link.predict(fitted, [game.gap])[0], atol=1e-12
                )

    def test_every_ended_eval_game_is_predicted_once(self):
        expected = {
            tuple(r) for r in self.full.execute(
                "SELECT season, game_id, regulation_result FROM game_spine "
                "WHERE in_eval_sample = 1 AND ended = 1 AND season IN (2025, 2027)"
            )
        }
        self.assertGreater(len(expected), 500)
        for model in ALL_MODELS:
            with self.subTest(model=model):
                stored = [tuple(r) for r in self.backtest.execute(
                    "SELECT season, game_id, result FROM backtest_prediction WHERE run_id = ? AND model = ?",
                    (self.run_id, model),
                )]
                self.assertEqual(len(stored), len(set(stored)))
                self.assertTrue(expected <= set(stored))
        # Nothing unplayed, and no burn-in or tuning-only season, is stored.
        self.assertEqual(
            self.backtest.execute(
                "SELECT COUNT(*) FROM backtest_prediction WHERE run_id = ? AND season = 2022", (self.run_id,)
            ).fetchone()[0], 0,
        )
        unplayed = self.full.execute("SELECT COUNT(*) FROM game_spine WHERE season = 2027 AND ended = 0").fetchone()[0]
        self.assertGreater(unplayed, 100)
        played = self.full.execute(
            "SELECT COUNT(*) FROM game_spine WHERE feeds_ratings = 1 AND ended = 1 AND season IN (2025, 2027)"
        ).fetchone()[0]
        self.assertEqual(len(self.stored(self.run_id)), played * len(ALL_MODELS))

    def test_probabilities_are_valid(self):
        for p in self.backtest.execute(
            "SELECT p_home, p_draw, p_away FROM backtest_prediction WHERE run_id = ?", (self.run_id,)
        ):
            self.assertAlmostEqual(sum(p), 1.0, places=12)
            self.assertGreater(min(p), 0.0)

    def test_base_rates_come_from_the_tuning_seasons(self):
        for result in self.results:
            if result.model != harness.BASE_RATE:
                continue
            rates = baselines.base_rates([
                g.result for g in self.data.games
                if g.in_eval and g.ended and g.season in result.fold.tuning_seasons
            ])
            np.testing.assert_allclose(list(result.chosen_params.values()), rates)
            self.assertEqual({(p.p_home, p.p_draw, p.p_away) for p in result.predictions}, {tuple(rates)})

    def test_rerun_is_identical_under_a_new_run_id(self):
        again = harness.store(self.backtest, self.data, CONFIG, harness.backtest(self.data, CONFIG))
        self.assertNotEqual(again, self.run_id)
        self.assertEqual(self.stored(again), self.stored(self.run_id))
        runs = self.backtest.execute(
            "SELECT model_version, code_version, features_build, config_json FROM backtest_run "
            "WHERE run_id IN (?, ?)", (self.run_id, again),
        ).fetchall()
        self.assertEqual(tuple(runs[0]), tuple(runs[1]))
        self.assertTrue(runs[0]["model_version"].startswith("elo-1-"))
        self.assertIn('"code_version": "f1.1"', runs[0]["features_build"])

    def test_unknown_model_is_refused(self):
        with self.assertRaisesRegex(ValueError, "lightgbm"):
            harness.backtest(self.data, harness.BacktestConfig(models=("elo", "lightgbm")))

    def test_report_runs_on_fixture_output(self):
        text = report.render(self.backtest, self.full, self.run_id)
        for heading in ("## Log loss by test season", "## Other metrics", "## What each fold chose",
                        "## Calibration, Elo, pooled 2025", "## Slices"):
            self.assertIn(heading, text)
        # 2025 is complete, 2027 is not: one is pooled, the other set apart.
        self.assertIn("| 2025 | 480 |", text)
        self.assertIn("**Pooled 2025**", text)
        self.assertIn("| 2027 (in progress) |", text)
        self.assertIn("K-Espoo 2025, its games | 60 |", text)
        self.assertIn("Jokerit 2027, its games", text)
        self.assertEqual(report.latest_run(self.backtest), self.backtest.execute(
            "SELECT MAX(run_id) FROM backtest_run").fetchone()[0])

    def test_report_keeps_the_written_summary(self):
        path = self.dir / "results.md"
        report.write(path, "first tables\n")
        self.assertTrue(path.read_text(encoding="utf-8").startswith("# Elo baseline results"))
        path.write_text(f"# Results\n\nWritten by hand.\n\n{report.MARKER}\n\nold tables\n", encoding="utf-8")
        report.write(path, "new tables\n")
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Results\n\nWritten by hand.\n\n" + report.MARKER))
        self.assertIn("new tables", text)
        self.assertNotIn("old tables", text)


class SourceRulesTest(unittest.TestCase):
    def test_models_and_harness_never_touch_odds(self):
        for module in (elo, link, baselines, folds, harness):
            with self.subTest(module=module.__name__):
                source = Path(module.__file__).read_text(encoding="utf-8")
                for banned in ("benchmark_odds", "odds_snapshots", "snapshots."):
                    self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
