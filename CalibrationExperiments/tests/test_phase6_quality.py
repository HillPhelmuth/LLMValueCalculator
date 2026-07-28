from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from calibration.pipeline import (
    FullRunApproval,
    PipelineBudget,
    PipelineError,
    PipelineMode,
    PipelineSpec,
    classify_nightly_drift,
    manifest_set_hash,
    write_candidate_profile,
)
from calibration.profile import CalibrationProfile, baseline_profile
from calibration.providers.openrouter_catalog import normalize_model
from calibration.schema import validate_record
from calibration.storage.artifacts import ArtifactIntegrityError, ArtifactStore
from calibration.storage.sqlite import SqliteRunStore


class Phase6QualityTests(unittest.TestCase):
    def test_fault_injection_detects_partial_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(directory)
            uri = store.put_json({"value": "immutable"})
            (Path(directory) / uri).unlink()
            with self.assertRaises(ArtifactIntegrityError):
                store.get_json(uri)

    def test_fault_injection_surfaces_database_lock_without_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "runs.sqlite3"
            with SqliteRunStore(database) as store:
                blocker = sqlite3.connect(database, timeout=0.05)
                try:
                    blocker.execute("BEGIN EXCLUSIVE")
                    store._connection.execute("PRAGMA busy_timeout=50")
                    with self.assertRaises(sqlite3.OperationalError):
                        store._connection.execute(
                            "INSERT INTO schema_migrations(version, applied_utc) VALUES (99, 'test')"
                        )
                        store._connection.commit()
                finally:
                    blocker.rollback()
                    blocker.close()

    def test_pipeline_gates_separate_pr_nightly_and_full_modes(self) -> None:
        budget = PipelineBudget(50, 10000, 2.0, 20)
        PipelineSpec("pr", PipelineMode.PULL_REQUEST, ("manifests/smoke.yaml",), budget, 20).validate()
        PipelineSpec("nightly", PipelineMode.NIGHTLY, ("manifests/smoke.yaml",), budget, 20).validate()
        with self.assertRaises(PipelineError):
            PipelineSpec("unsafe", PipelineMode.PULL_REQUEST, ("manifest.yaml",), budget, 20, allow_provider_calls=True).validate()
        FullRunApproval("reviewer", "2026-07-18T00:00:00Z", "m" * 64, "s" * 64, "c" * 40, budget).validate()
        with self.assertRaises(PipelineError):
            FullRunApproval("reviewer", "now", "m" * 64, "s" * 64, "c" * 40, budget, candidate_only=False).validate()

    def test_nightly_drift_classifies_behavior_and_infrastructure(self) -> None:
        report = classify_nightly_drift(
            run_id="current",
            baseline_run_id="previous",
            current={"exact_match": 0.70, "latency": 2.0},
            baseline={"exact_match": 0.90, "latency": 1.0},
            failures={"provider": 2, "infrastructure": 1},
            latency={"latency": 2.0},
            spend={"usd": 0.4},
        )
        self.assertIn("model_behavior_drift", report.alerts)
        self.assertIn("infrastructure_drift", report.alerts)
        self.assertIn("latency_drift", report.alerts)

    def test_profile_schema_and_all_persisted_schema_files_parse(self) -> None:
        profile = baseline_profile()
        validate_record("calibration_profile", profile.to_json())
        schema_directory = Path(__file__).parents[1] / "calibration" / "schemas"
        for schema_path in schema_directory.glob("*.schema.json"):
            document = json.loads(schema_path.read_text(encoding="utf-8"))
            self.assertEqual("object", document["type"], schema_path.name)

    def test_openrouter_unknown_pricing_sentinel_is_missing_not_negative(self) -> None:
        model = normalize_model({"id": "model", "pricing": {"prompt": "-1"}})
        self.assertIsNone(model.pricing["prompt"])

    def test_candidate_profile_requires_holdout_fit_and_preserves_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fitting_data = Path(directory) / "fitting-data.jsonl"
            fitting_data.write_text(
                "".join(
                    json.dumps(
                        {
                            "intelligence_index": float(index * 10),
                            "success": index % 3 != 0,
                            "split": "held_out" if index >= 10 else "fit",
                            "model_id": f"model-{index % 2}",
                            "dataset_id": "dataset",
                            "case_id": f"case-{index}",
                            "prompt_id": "prompt",
                        }
                    )
                    + "\n"
                    for index in range(12)
                ),
                encoding="utf-8",
            )
            manifest = Path(directory) / "manifest.yaml"
            manifest.write_text("manifest: frozen\n", encoding="utf-8")
            manifest_hash = manifest_set_hash((str(manifest),))
            output = write_candidate_profile(
                fitting_data,
                Path(directory) / "candidate-profile.json",
                manifest_hashes=(manifest_hash,),
                aa_snapshot="catalog-snapshot-hash",
                bootstrap_replicates=3,
            )
            profile = CalibrationProfile.load(output)
            self.assertEqual((manifest_hash,), profile.manifest_hashes)
            self.assertEqual("catalog-snapshot-hash", profile.aa_snapshot)
            self.assertTrue(profile.to_json()["uncertainty"]["candidate_only"])

    def test_workflows_keep_live_calls_and_full_runs_behind_explicit_gates(self) -> None:
        workflow_directory = Path(__file__).parents[2] / ".github" / "workflows"
        pull_request = (workflow_directory / "calibration-foundation.yml").read_text(encoding="utf-8")
        nightly = (workflow_directory / "calibration-nightly.yml").read_text(encoding="utf-8")
        full = (workflow_directory / "calibration-full.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", pull_request)
        self.assertIn("github.event_name == 'workflow_dispatch'", pull_request)
        self.assertIn("CALIBRATION_LIVE_APPROVED", pull_request)
        self.assertIn("schedule:", nightly)
        self.assertIn("CALIBRATION_NIGHTLY_LIVE", nightly)
        self.assertIn("environment: calibration-full-run", full)
        self.assertIn("CALIBRATION_FULL_APPROVED", full)
        self.assertIn("candidate-profile.json", full)


if __name__ == "__main__":
    unittest.main()
