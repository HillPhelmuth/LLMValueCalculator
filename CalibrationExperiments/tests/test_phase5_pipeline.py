from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from calibration.fitting_dataset import (
    FittingDatasetRules,
    build_fitting_dataset,
)
from calibration.fitting import BernoulliRow
from calibration.profile import ProfileError, baseline_profile
from calibration.profile_codegen import generate_csharp, generate_json, write_application_artifacts
from calibration.refit import staged_refit
from calibration.regression import (
    RecommendationSnapshot,
    build_scenario_matrix,
    compare_profiles,
    validate_regression_diffs,
)
from calibration.reports import CalibrationCard, render_markdown, render_split_svg, write_calibration_card
from calibration.statistical import StatisticalModel, fit_statistical_model, grouped_bootstrap_rows


class Phase5PipelineTests(unittest.TestCase):
    def test_fitting_dataset_excludes_holdouts_and_retains_lineage(self) -> None:
        attempts = (
            {"attempt_id": "a1", "model_id": "m1", "case_id": "c1", "condition_id": "baseline", "prompt_version": "p1", "repeat_index": 0},
            {"attempt_id": "a2", "model_id": "m1", "case_id": "c2", "condition_id": "baseline", "prompt_version": "p1", "repeat_index": 0},
        )
        scores = (
            {"attempt_id": "a1", "scorer_name": "exact", "scorer_version": "1", "success": True},
            {"attempt_id": "a2", "scorer_name": "exact", "scorer_version": "1", "success": False},
        )
        features = (
            {"case_id": "c1", "dataset_id": "d1", "split": "fit", "category": "math"},
            {"case_id": "c2", "dataset_id": "d1", "split": "dataset_holdout", "category": "math"},
        )
        dataset = build_fitting_dataset(attempts, scores, features, rules=FittingDatasetRules())
        self.assertEqual(1, len(dataset.rows))
        self.assertEqual(("a1", "a1:exact:1"), dataset.rows[0].source_row_ids)
        self.assertEqual(1, dataset.quality.holdout_rows_seen)
        with tempfile.TemporaryDirectory() as directory:
            rows_path, lock_path = dataset.write(directory)
            self.assertTrue(Path(rows_path).is_file())
            self.assertTrue(Path(lock_path).is_file())

    def test_shared_statistical_fit_and_grouped_bootstrap(self) -> None:
        rows = tuple(
            BernoulliRow(index, index >= 20, "held_out" if case == 0 else "fit", f"d{case % 2}", f"m{case % 3}", f"c{case}")
            for case in range(10)
            for index in (0, 10, 20, 30, 40, 50)
        )
        fit = fit_statistical_model(StatisticalModel.BERNOULLI, rows, bootstrap_replicates=3)
        self.assertTrue(fit.diagnostics.identifiable)
        self.assertIn("curve", fit.estimates)
        bootstrap = grouped_bootstrap_rows(
            ({"model_id": "m1", "dataset_id": "d1", "case_id": "c1"}, {"model_id": "m2", "dataset_id": "d1", "case_id": "c2"}),
            repeats=3,
        )
        self.assertEqual(3, len(bootstrap))

    def test_staged_refit_freezes_capability_before_risk(self) -> None:
        rows = tuple(
            BernoulliRow(index, index >= 20, "fit", "d1", f"m{case % 2}", f"c{case}-{index}")
            for case in range(10)
            for index in (0, 10, 20, 30, 40, 50)
        )
        result = staged_refit(rows, ({"experiment_id": "experiment-7", "parameter": "floor", "decision": "keep", "evidence_ids": ["e1"]},))
        self.assertTrue(result.capability.prediction_hash)
        self.assertTrue(result.capability.diagnostics["risk_rows_excluded"])
        self.assertTrue(result.promotable)

    def test_profile_is_schema_valid_hashed_immutable_and_codegen_roundtrips(self) -> None:
        profile = baseline_profile()
        document = profile.to_json()
        self.assertEqual(profile.profile_hash, document["profile_hash"])
        self.assertIn("Profile hash:", generate_csharp(profile))
        self.assertIn(profile.profile_hash, generate_json(profile))
        with tempfile.TemporaryDirectory() as directory:
            path = profile.write_immutable(directory)
            self.assertEqual(profile.profile_hash, profile.load(path).profile_hash)
            write_application_artifacts(profile, Path(directory) / "generated")
            path.write_text("tampered", encoding="utf-8")
            with self.assertRaises(ProfileError):
                profile.write_immutable(directory)

    def test_regression_suite_and_calibration_card(self) -> None:
        scenarios = build_scenario_matrix(("rag",), ("easy", "hard"), ("none",), ("low",), ("one",), ("normal",))

        def evaluate(scenario, model_id, profile_hash):
            return RecommendationSnapshot(model_id, True, 1, 0.8 if profile_hash == "base" else 0.81, 0.1, 1.0, 2.0, profile_hash)

        diffs = compare_profiles(scenarios, ("m1",), baseline_hash="base", candidate_hash="candidate", evaluator=evaluate)
        validate_regression_diffs(diffs)
        self.assertEqual(2, len(diffs))
        card = CalibrationCard("Experiment", "experiment-1", {"rows": 2}, {"holdout": 1}, {"usd": 2}, {"converged": True}, {"split_values": {"fit": 0.8, "model_holdout": 0.7}}, {"effect": [0, 1]}, {"overlap": False}, ({"parameter": "tau", "decision": "keep", "rationale": "stable"},), ("prov",), ("est",))
        self.assertIn("Lineage", render_markdown(card))
        self.assertIn("svg", render_split_svg(card.holdout_metrics))
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(all(path.is_file() for path in write_calibration_card(card, directory)))


if __name__ == "__main__":
    unittest.main()
