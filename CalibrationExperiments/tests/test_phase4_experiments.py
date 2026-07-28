from __future__ import annotations

import unittest

from calibration.experiments import (
    StructuredObservation,
    ToolTrajectory,
    build_experiment_plans,
    build_retrieval_conditions,
    map_reasoning_features,
    schedule_tool_repeats,
    validate_coverage,
    validate_structured_observation,
)
from calibration.experimental_retries import schedule_experimental_retries, validate_retry_lineage
from calibration.fitting import (
    BernoulliRow,
    MonotoneCurve,
    PromotionDecision,
    TauRatios,
    compare_candidate_curve,
    fit_hierarchical_effects,
    fit_monotone_curve,
    fit_ordinal_effects,
    fit_paired_effects,
    fit_quality_critical_tilts,
    fit_retry_decay,
    fit_validator_effects,
)
from calibration.models import CanonicalCase, Message, ProviderRequest
from calibration.operational import (
    Fault,
    FaultLibrary,
    ReplayRecord,
    ReplaySource,
    deidentify_input,
    fit_operational_multipliers,
)


class Phase4ExperimentTests(unittest.TestCase):
    def test_registered_plans_cover_all_experiments_and_phase1_rules(self) -> None:
        plans = build_experiment_plans()
        self.assertEqual({f"experiment-{index}" for index in range(1, 9)}, set(plans))
        experiment1 = plans["experiment-1"]
        self.assertEqual(
            {"mmlu_adjacent", "gpqa", "gsm8k", "proofwriter", "pubmedqa", "legalbench", "finqa"},
            {dataset.dataset_id for dataset in experiment1.datasets},
        )
        self.assertEqual(("baseline",), tuple(item.condition_id for item in experiment1.conditions))
        self.assertEqual(2000, experiment1.sample_min)
        self.assertEqual(5000, experiment1.sample_max)
        self.assertTrue(plans["experiment-5"].repeat_count >= 5)
        self.assertTrue(plans["experiment-4"].overlap_sensitivity)

    def test_execution_gates_and_paired_retrieval_features(self) -> None:
        report = validate_coverage(("a", "b", "c"), ("a", "b"))
        self.assertFalse(report.passed)
        case = CanonicalCase("case-1", {"prompt": "question"}, "answer")
        conditions = build_retrieval_conditions(
            case,
            ({"id": "evidence", "text": "answer"}, {"id": "other", "text": "noise"}),
            seed=4,
        )
        self.assertEqual(
            {"oracle", "clean", "noisy", "very_large", "no_context", "measured_retrieval"},
            {condition["condition_id"] for condition in conditions},
        )
        self.assertEqual(
            {"reasoning_depth": "deep", "hop_count": 5, "branching_factor": 2, "dependency_depth": 5, "intermediate_state": True},
            map_reasoning_features({"proofwriter_depth": 5, "branching_factor": 2}),
        )

    def test_tool_and_structured_execution_records_are_independent(self) -> None:
        trajectory = ToolTrajectory("case", (), (), 0, 0, 0, {"done": True}, {"done": True}, 2, False)
        trajectory.validate()
        self.assertEqual((0, 1, 2, 3, 4), schedule_tool_repeats(trajectory))
        observation = StructuredObservation("case", "prompted_json", "{}", True, True, True, True, False, True, True)
        validate_structured_observation(observation)

    def test_monotone_curve_fit_and_promotion_rules(self) -> None:
        rows = tuple(
            BernoulliRow(index, (index + case) >= 20, "held_out" if case % 5 == 0 else "fit", f"d-{case % 3}", f"m-{case % 4}", f"c-{case}")
            for case in range(30)
            for index in (0, 10, 20, 30, 40, 50)
        )
        result = fit_monotone_curve(rows, tau_ratios=TauRatios())
        result.curve.validate()
        self.assertEqual(1.0, result.curve.slopes[0])
        self.assertTrue(all(left <= right for left, right in zip(result.curve.slopes[1:], result.curve.slopes[2:])))
        current = MonotoneCurve(0, (1, 1, 1, 1, 1, 1), 0.02)
        decision = compare_candidate_curve(current, result.curve, tuple(row for row in rows if row.split == "held_out"), bootstrap_stability=0.9)
        self.assertIn(decision.decision, {PromotionDecision.KEEP, PromotionDecision.CHANGE})

    def test_paired_ordinal_hierarchical_retry_and_validator_fits(self) -> None:
        paired = fit_paired_effects(
            ({"baseline_probability": 0.5, "treatment_probability": 0.7, "dataset_id": "a"}, {"baseline_probability": 0.6, "treatment_probability": 0.8, "dataset_id": "b"}),
            tau=8,
            error_floor=0.02,
        )
        self.assertTrue(paired.lower <= paired.effect <= paired.upper)
        ordinal = fit_ordinal_effects(
            ({"level": "single", "effect": 0.0}, {"level": "deep", "effect": 0.4}, {"level": "shallow", "effect": 0.1}),
            ordered_levels=("single", "shallow", "deep"),
        )
        self.assertTrue(ordinal.monotone)
        hierarchical = fit_hierarchical_effects(
            ({"domain": "math", "effect": 0.2}, {"domain": "math", "effect": 0.4}, {"domain": "law", "effect": 0.1}),
            group_key="domain",
        )
        self.assertIn("math", hierarchical.effects)
        retry = fit_retry_decay(
            ({"strategy": "same_prompt", "repeat_index": 0, "unresolved_probability": 1.0}, {"strategy": "same_prompt", "repeat_index": 1, "unresolved_probability": 0.5}),
        )
        self.assertTrue(retry.cross_validated)
        validator = fit_validator_effects(
            ({"semantic_validated": 0.8, "semantic_unvalidated": 0.7, "strict_success": 0.8, "prompted_success": 0.8, "validator_decision": True, "correct": True, "with_extraction": 0.8, "without_extraction": 0.7},),
        )
        self.assertGreaterEqual(validator.sensitivity, 0)
        tilts = fit_quality_critical_tilts(({"success": True, "quality_share": 0.8, "critical_share": 0.0}, {"success": False, "quality_share": 0.0, "critical_share": 0.5}))
        self.assertIn("quality_tilt", tilts)

    def test_experimental_retry_lineage_never_collapses_cache_keys(self) -> None:
        request = ProviderRequest(
            "case", "model", "model-v1", "fake", (Message("user", "question"),), 0, 32, None, "baseline", "p-v1", 0
        )
        retries = schedule_experimental_retries(request, strategy_id="same_prompt", count=5)
        validate_retry_lineage(tuple(retry for _, retry in retries))
        self.assertEqual(5, len({child.request_hash for child, _ in retries}))
        with self.assertRaises(ValueError):
            schedule_experimental_retries(request, strategy_id="repair_feedback", count=1)

    def test_operational_replay_is_deidentified_and_gated(self) -> None:
        token = deidentify_input({"customer": "Alice", "prompt": "secret"}, salt="review")
        self.assertTrue(token.startswith("sha256:"))
        library = FaultLibrary((Fault("f1", "provider", "high", True, {"seed": 1}),))
        self.assertEqual("f1", library.get("f1").fault_id)
        rows = tuple(
            ReplayRecord(str(index), token, "allow", "high", "customer", "reversible", "allow", 3.0, "low", ReplaySource.SYNTHETIC)
            for index in range(3)
        )
        fit = fit_operational_multipliers(rows, minimum_sample_size=2)
        self.assertFalse(fit.retained_prior)
        self.assertTrue(fit.diagnostics["oracle_gates_excluded"])


if __name__ == "__main__":
    unittest.main()
