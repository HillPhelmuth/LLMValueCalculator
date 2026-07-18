from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from calibration.datasets.base import validate_adapter
from calibration.datasets.jsonl import JsonlDatasetAdapter
from calibration.datasets.registry import DatasetAcquirer, DatasetSpec
from calibration.datasets.sampling import (
    assert_no_leakage,
    freeze_sample,
    hide_holdout_labels,
)
from calibration.failure import (
    CriticalityPolicy,
    FailureClass,
    OutcomeClass,
    assess_outcome,
)
from calibration.judges import (
    JudgeConfig,
    assert_intelligence_curve_safe,
    judge_uncertainty_interval,
    validate_judge,
)
from calibration.models import CanonicalCase, ProviderResponse
from calibration.prompts import PromptRegistry, PromptSpec
from calibration.perturbations import (
    ChoiceOrderTransform,
    PerturbationRegistry,
    TreatmentSpec,
)
from calibration.sandbox import (
    SandboxImageLock,
    SandboxLimits,
    SandboxPolicy,
    SandboxPolicyError,
    scan_lock_hash,
)
from calibration.scorers.deterministic import (
    FieldLevelComparisonScorer,
    NdcgScorer,
    RetrievalRecallScorer,
    SchemaValidityScorer,
)
from calibration.scorers.executable import (
    ExecutionOutcome,
    ExecutionReport,
    TestCaseResult,
    ToolStateScorer,
)
from calibration.scorers.registry import ScorerRegistry
from calibration.manifest import DatasetConfig


class Phase3PlatformTests(unittest.TestCase):
    def test_registry_download_hash_and_offline_reuse(self) -> None:
        content = b'{"case_id":"1"}\n'
        digest = hashlib.sha256(content).hexdigest()
        spec = DatasetSpec(
            dataset_id="unit",
            adapter="jsonl",
            adapter_version="jsonl-1.0.0",
            source_url="https://example.test/unit.jsonl",
            license="CC-BY-4.0",
            revision=f"sha256:{digest}",
            file_name="cases.jsonl",
            splits=("validation",),
        )
        with tempfile.TemporaryDirectory() as directory:
            acquirer = DatasetAcquirer(directory)
            prepared = acquirer.prepare(spec, downloader=lambda _: content)
            reused = acquirer.prepare(spec, offline=True)
            self.assertEqual(prepared.path, reused.path)
            self.assertTrue(Path(prepared.lock_path).is_file())
            self.assertIn("CC-BY-4.0", Path(prepared.license_notice).read_text())

    def test_adapter_conformance_and_explicit_sample_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "cases.jsonl"
            rows = [
                {"case_id": str(index), "prompt": f"prompt {index}", "expected": index, "split": "validation", "metadata": {"category": "a" if index % 2 else "b"}}
                for index in range(1, 9)
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            revision = hashlib.sha256(path.read_bytes()).hexdigest()
            config = DatasetConfig(
                adapter="jsonl",
                revision=f"sha256:{revision}",
                split="validation",
                sample_seed=7,
                options={"path": "cases.jsonl"},
            )
            adapter = JsonlDatasetAdapter(config, root)
            adapter.prepare()
            report = validate_adapter(adapter, "validation")
            self.assertTrue(report.passed)
            cases = tuple(adapter.cases("validation"))
            lock = freeze_sample(
                cases,
                dataset_id="unit",
                dataset_revision=config.revision,
                split="validation",
                sample_size=8,
                seed=7,
                output_directory=root / "sample",
                holdout_fraction=0.25,
            )
            self.assertTrue(lock.membership_hash)
            self.assertTrue(set(lock.fit_case_ids).isdisjoint(lock.holdout_case_ids))
            hidden = hide_holdout_labels(cases[0])
            self.assertIsNone(hidden.expected)
            self.assertFalse(hidden.label_available)
            assert_no_leakage(cases[:2], cases[2:])

    def test_prompt_rendering_and_paired_perturbation_are_stable(self) -> None:
        registry = PromptRegistry(
            {
                "p": PromptSpec(
                    prompt_id="p",
                    version="1.0.0",
                    task_family="qa",
                    messages=({"role": "user", "content": "Answer {question}"},),
                    conditions=("baseline",),
                )
            }
        )
        first = registry.render("p", "baseline", {"question": "A {literal}"})
        second = registry.render("p", "baseline", {"question": "A {literal}"})
        self.assertEqual(first.render_hash, second.render_hash)
        self.assertEqual("Answer A {literal}", first.messages[0].content)

        case = CanonicalCase("case-1", {"choices": ["a", "b", "c"]}, "a")
        treatment = TreatmentSpec("choice_order", "1.0.0", 12, {})
        perturbations = PerturbationRegistry({"choice_order": ChoiceOrderTransform()})
        variant = perturbations.generate(case, (treatment,))[0]
        self.assertEqual(variant, perturbations.generate(case, (treatment,))[0])
        self.assertEqual("case-1", variant.parent_case_id)
        self.assertTrue(variant.invariant_results["expected_answer"])

    def test_deterministic_and_executable_scorers_preserve_metric_detail(self) -> None:
        response = ProviderResponse("r", {}, {"answer": "yes", "score": 1.0}, "stop")
        case = CanonicalCase("case", {}, {"answer": "yes", "score": 1.0})
        self.assertTrue(FieldLevelComparisonScorer().score(case, response).success)
        self.assertEqual(1.0, RetrievalRecallScorer().score(
            CanonicalCase("r", {}, ["a", "b"]),
            ProviderResponse("r", {}, ["a", "b", "c"], "stop"),
        ).semantic_score)
        self.assertEqual(1.0, NdcgScorer().score(
            CanonicalCase("n", {}, {"a": 2, "b": 1}),
            ProviderResponse("r", {}, ["a", "b"], "stop"),
        ).semantic_score)
        valid = SchemaValidityScorer({"type": "object", "required": ["answer"]}).score(case, response)
        self.assertTrue(valid.schema_valid)
        registry = ScorerRegistry()
        self.assertEqual(9, len(registry.locks(tuple(registry._scorers))))
        report = ExecutionReport(
            outcome=ExecutionOutcome.PARTIAL,
            tests=(TestCaseResult("critical", False, True), TestCaseResult("other", True)),
        )
        self.assertEqual("partial", report.to_json()["outcome"])
        tool_case = CanonicalCase(
            "tool",
            {},
            None,
            {"expected_tool_calls": (), "expected_tool_state": {}, "observed_tool_state": {}},
        )
        self.assertTrue(ToolStateScorer().score(tool_case, response).success)

    def test_sandbox_policy_failure_taxonomy_and_judge_gate(self) -> None:
        image = SandboxImageLock(
            "calibration/test",
            "sha256:" + "a" * 64,
            scan_lock_hash({"scanner": "unit", "critical": 0}),
        )
        policy = SandboxPolicy(image, SandboxLimits())
        command = policy.docker_command(("python", "-I", "runner.py"))
        self.assertIn("--network=none", command)
        with self.assertRaises(SandboxPolicyError):
            SandboxPolicy(image, network_disabled=False).validate()

        score = assess_outcome(
            (),
            CriticalityPolicy("unit", "qa"),
            provider_failure=FailureClass.PROVIDER,
        )
        self.assertEqual(OutcomeClass.INFRASTRUCTURE_FAILURE, score.outcome)

        config = JudgeConfig("judge", "1.0.0", "judge", "judge-v1")
        report = validate_judge(
            config,
            [True, True, False, False],
            [True, True, False, False],
            subgroups=["a", "a", "b", "b"],
        )
        self.assertTrue(report.passed)
        self.assertLessEqual(judge_uncertainty_interval(0.5, sensitivity=1, specificity=1)[0], 0.5)
        assert_intelligence_curve_safe(("answer_exact_match",))


if __name__ == "__main__":
    unittest.main()
