from __future__ import annotations

import asyncio
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from calibration.fitting import BernoulliRow
from calibration.fitting_dataset import build_fitting_dataset, read_parquet_rows
from calibration.manifest import load_manifest
from calibration.model_mapping import (
    ArtificialAnalysisMapping,
    ArtificialAnalysisSnapshot,
)
from calibration.model_panel import ModelEligibilityRules, select_model_panel
from calibration.monitoring import BudgetLimits, collect_run_metrics, write_run_status
from calibration.pipeline import validate_fitting_gate
from calibration.profile import CalibrationProfile, baseline_profile
from calibration.promotion import check_promotion
from calibration.providers.fake import FakeProvider
from calibration.providers.openrouter_catalog import CatalogSnapshot
from calibration.regression import (
    RecommendationSnapshot,
    build_scenario_matrix,
    compare_profiles,
    write_regression_report,
)
from calibration.reports import CalibrationCard, write_calibration_card
from calibration.runner.runner import CalibrationRunner
from calibration.statistical import StatisticalModel, fit_statistical_model
from calibration.storage.artifacts import ArtifactStore
from calibration.storage.parquet import export_run_to_parquet
from calibration.storage.sqlite import SqliteRunStore


class RehearsalError(RuntimeError):
    """Raised when the offline end-to-end rehearsal misses a gate."""


class InterruptingFakeProvider(FakeProvider):
    def __init__(self, succeed_before_interrupt: int) -> None:
        super().__init__()
        self.succeed_before_interrupt = succeed_before_interrupt

    async def complete(self, request):
        if self.call_count >= self.succeed_before_interrupt:
            raise RuntimeError("intentional rehearsal interruption")
        return await super().complete(request)


def run_rehearsal(output: str | Path) -> Path:
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parents[1]
    manifest_path = project / "manifests/pr-smoke.yaml"
    manifest = load_manifest(manifest_path)
    artifacts = ArtifactStore(root / "objects")

    catalog = CatalogSnapshot.from_pages(
        (
            json.loads(
                (project / "tests/fixtures/openrouter/models-page-0.json").read_text(
                    encoding="utf-8"
                )
            ),
        ),
        captured_utc="2026-07-18T00:00:00+00:00",
    )
    catalog_uri = catalog.persist(artifacts)
    mapping = ArtificialAnalysisMapping(
        stable_catalog_id="rehearsal-model",
        openrouter_id="provider/model-2026-01",
        aa_model_id="aa-model",
        aa_model_version="2026-01",
        snapshot_date="2026-07-18",
        intelligence_index=Decimal("60"),
        coding_index=Decimal("60"),
        agentic_index=Decimal("60"),
        cost_index=Decimal("60"),
        source_citations=("recorded-fixture",),
    )
    aa_snapshot = ArtificialAnalysisSnapshot.from_mappings(
        (mapping,),
        snapshot_date="2026-07-18",
        source_citations=("recorded-fixture",),
        catalog=catalog,
    )
    aa_uri = aa_snapshot.persist(artifacts)
    panel = select_model_panel(
        catalog,
        aa_snapshot,
        ModelEligibilityRules(
            experiment_id="rehearsal",
            as_of_date="2026-07-18",
            required_context_length=100,
            required_output_tokens=32,
            required_parameters=("temperature",),
            required_modalities=("text",),
            max_models=1,
        ),
    )
    panel_uri = panel.persist(artifacts)

    database = root / "runs.sqlite3"
    with SqliteRunStore(database) as store:
        first_provider = InterruptingFakeProvider(succeed_before_interrupt=5)
        interrupted = CalibrationRunner(
            manifest,
            manifest_path,
            store,
            artifacts,
            providers={"fake": first_provider},
            max_workers=1,
            budget_limits=BudgetLimits.from_manifest(manifest),
        )
        try:
            interrupted_summary = asyncio.run(interrupted.run(code_commit="rehearsal"))
        except RuntimeError as error:
            if "intentional rehearsal interruption" not in str(error):
                raise
        else:
            if interrupted_summary["status"] != "failed":
                raise RehearsalError(
                    "Interruption injection did not stop the first pass"
                )
        run_id = store.latest_run_id()
        if store.run_summary(run_id)["status"] != "failed":
            raise RehearsalError(
                "Interrupted pass did not persist a failed resumable run"
            )

        resumed = CalibrationRunner(
            manifest,
            manifest_path,
            store,
            artifacts,
            providers={"fake": FakeProvider()},
            max_workers=1,
            budget_limits=BudgetLimits.from_manifest(manifest),
        )
        summary = asyncio.run(
            resumed.run(resume_run_id=run_id, code_commit="rehearsal")
        )
        if summary["status"] != "completed":
            raise RehearsalError(f"Rehearsal resume did not complete: {summary}")
        attempts = store.rows_for_export("attempts", run_id)
        if len(attempts) != 20 or len({row["request_hash"] for row in attempts}) != 20:
            raise RehearsalError(
                "Resume produced duplicate or missing request attempts"
            )
        status_path = write_run_status(
            store,
            run_id,
            root / "run-status.json",
            limits=BudgetLimits.from_manifest(manifest),
            expected_cells=20,
        )
        transport_events = store.rows_for_export("transport_events", run_id)
        export = export_run_to_parquet(
            store, run_id, root / "exports", artifacts=artifacts
        )
        parquet_root = export.output_directory
        fitting = build_fitting_dataset(
            read_parquet_rows(parquet_root / "attempts.parquet"),
            tuple(
                row
                for row in read_parquet_rows(parquet_root / "scores.parquet")
                if row.get("scorer_name") == "answer_exact_match"
            ),
            read_parquet_rows(parquet_root / "case_features.parquet"),
        )
        fitting_path, fitting_lock = fitting.write(root / "fitting")
        profile_rows = []
        for index, row in enumerate(fitting.rows):
            profile_rows.append(
                {
                    "intelligence_index": float((index % 6 + 1) * 10),
                    "success": bool(row.success),
                    "split": "held_out" if index == len(fitting.rows) - 1 else "fit",
                    "dataset_id": row.dataset_id,
                    "model_id": row.model_id,
                    "case_id": row.case_id,
                    "prompt_id": row.prompt_id,
                    "category": row.features.get("category"),
                }
            )
        profile_input = root / "fitting" / "profile-input.jsonl"
        profile_input.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in profile_rows),
            encoding="utf-8",
        )
        fit = fit_statistical_model(
            StatisticalModel.BERNOULLI,
            tuple(
                BernoulliRow(
                    float(row["intelligence_index"]),
                    bool(row["success"]),
                    str(row["split"]),
                    str(row["dataset_id"]),
                    str(row["model_id"]),
                    str(row["case_id"]),
                    str(row["prompt_id"]),
                    row.get("category"),
                )
                for row in profile_rows
            ),
            bootstrap_replicates=3,
        )
        candidate = _candidate_from_fit(
            fit, manifest, fitting_path, root / "candidate-profile.json"
        )
        baseline = baseline_profile()
        scenarios = build_scenario_matrix(
            ("general",), ("normal",), ("none",), ("low",), ("one",), ("normal",)
        )

        def evaluate(
            scenario, model_id: str, profile_hash: str
        ) -> RecommendationSnapshot:
            candidate_value = profile_hash == candidate.profile_hash
            return RecommendationSnapshot(
                model_id=model_id,
                eligible=True,
                rank=1,
                success_rate=0.81 if candidate_value else 0.80,
                critical_risk=0.05,
                cost_usd=0.0,
                expected_value_usd=0.81 if candidate_value else 0.80,
                profile_hash=profile_hash,
            )

        diffs = compare_profiles(
            scenarios,
            ("fake-echo-v1",),
            baseline_hash=baseline.profile_hash,
            candidate_hash=candidate.profile_hash,
            evaluator=evaluate,
        )
        diff_path = write_regression_report(
            diffs, root / "reports" / "scenario-diff.json"
        )
        metrics = collect_run_metrics(store, run_id, expected_cells=20).to_json()
        exact_scores = [
            row
            for row in store.rows_for_export("scores", run_id)
            if row["scorer_name"] == "answer_exact_match"
        ]
        success_rate = sum(bool(row["success"]) for row in exact_scores) / max(
            1, len(exact_scores)
        )
        run_provenance = store.run_summary(run_id).get("provenance")
        if not isinstance(run_provenance, dict) or not run_provenance.get(
            "provenance_id"
        ):
            raise RehearsalError("Rehearsal run has no provenance ID")
        card = CalibrationCard(
            title="Phase 6 offline end-to-end rehearsal",
            experiment_id=manifest.experiment_id,
            coverage={
                "expected_cells": 20,
                "observed_cells": len(attempts),
                "fitting_rows": len(fitting.rows),
            },
            exclusions=fitting.quality.exclusions,
            costs={
                "estimated_usd": metrics["estimated_cost_usd"],
                "actual_usd": metrics["actual_cost_usd"],
            },
            fit_diagnostics=fit.diagnostics.to_json(),
            holdout_metrics={
                "success_rate": success_rate,
                "split_values": {"fit": success_rate, "model_holdout": success_rate},
            },
            intervals=fit.to_json()["intervals"],
            sensitivity={
                "resume_duplicate_request_hashes": 0,
                "transport_events": len(transport_events),
            },
            decisions=(
                {
                    "parameter": "promotion",
                    "decision": "review",
                    "rationale": "candidate remains unpromoted until explicit review",
                },
            ),
            provenance_ids=(str(run_provenance["provenance_id"]),),
        )
        card_paths = write_calibration_card(card, root / "reports")
        evidence = {
            "candidate_profile_hash": candidate.profile_hash,
            "baseline_profile_hash": baseline.profile_hash,
            "sign_agreement": 1.0,
            "held_out_improvement": 0.01,
            "duplicate_pathways": 0,
            "material_recommendation_fraction": sum(item.material for item in diffs)
            / len(diffs),
            "intervals": fit.to_json()["intervals"],
            "calibration_cards": [str(path) for path in card_paths],
            "scenario_diff": str(diff_path),
            "provenance_ids": list(card.provenance_ids),
            "cost_usd": metrics["actual_cost_usd"],
            "limitations": ["offline fake provider", "recorded catalog fixture"],
            "reviewers": [],
        }
        evidence_path = root / "promotion-evidence.json"
        evidence_path.write_text(
            json.dumps(evidence, sort_keys=True, indent=2), encoding="utf-8"
        )
        promotion_gate = check_promotion(candidate, baseline, evidence)
        if promotion_gate.passed:
            raise RehearsalError(
                "Rehearsal candidate unexpectedly passed without explicit review"
            )
        (root / "promotion-check.json").write_text(
            json.dumps(promotion_gate.to_json(), sort_keys=True, indent=2),
            encoding="utf-8",
        )
        gate = validate_fitting_gate(database, root / "objects", manifest_path, 20)
        artifact_errors = artifacts.audit_integrity()
        provenance_errors = store.audit_provenance(run_id)
        if artifact_errors or provenance_errors or gate["status"] != "completed":
            raise RehearsalError(
                f"Rehearsal gates failed: {artifact_errors}, {provenance_errors}, {gate}"
            )

    report = {
        "status": "passed",
        "run_id": run_id,
        "run_status": str(status_path),
        "catalog_snapshot_uri": catalog_uri,
        "artificial_analysis_snapshot_uri": aa_uri,
        "panel_uri": panel_uri,
        "fitting_data": str(fitting_path),
        "fitting_lock": str(fitting_lock),
        "candidate_profile": str(root / "candidate-profile.json"),
        "promotion_status": "review_required_unpromoted",
        "checks": {
            "interruption_resume": True,
            "no_duplicate_request_hashes": True,
            "automatic_gates": True,
            "candidate_unpromoted": True,
            "promotion_gate_executed": True,
        },
    }
    destination = root / "rehearsal-report.json"
    destination.write_text(
        json.dumps(report, sort_keys=True, indent=2), encoding="utf-8"
    )
    return destination


def _candidate_from_fit(fit, manifest, fitting_path: Path, output: Path):
    curve = fit.estimates["curve"]
    slopes = tuple(max(1.0, float(value)) for value in curve["slopes"])
    profile = CalibrationProfile(
        profile_version="rehearsal-candidate-1.0.0",
        curve_segments=tuple(
            {"upper": 10.0 * (index + 1) if index < 5 else None, "slope": value}
            for index, value in enumerate(slopes)
        ),
        tau={
            "soft": curve["tau_ratios"]["normal"],
            "normal": curve["tau_ratios"]["normal"],
            "sharp": curve["tau_ratios"]["reasoning"],
        },
        error_floor=float(curve["error_floor"]),
        adjustments={},
        risk_multipliers={},
        uncertainty={"fit": fit.to_json(), "candidate_only": True},
        manifest_hashes=(manifest.manifest_hash,),
        fitting_data_hash=hashlib.sha256(fitting_path.read_bytes()).hexdigest(),
        aa_snapshot="rehearsal-aa-snapshot",
    )
    output.write_text(
        json.dumps(profile.to_json(), sort_keys=True, indent=2), encoding="utf-8"
    )
    return profile
