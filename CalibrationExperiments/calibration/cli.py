from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from calibration.config import CalibrationSettings
from calibration.datasets.base import validate_adapter
from calibration.datasets.jsonl import JsonlDatasetAdapter
from calibration.datasets.registry import DatasetAcquirer, DatasetRegistry
from calibration.experiments import write_experiment_plan_registry
from calibration.manifest import load_manifest
from calibration.monitoring import BudgetLimits, write_run_status
from calibration.pipeline import write_candidate_profile
from calibration.promotion import PromotionStore, check_promotion
from calibration.reports import CalibrationCard, write_calibration_card
from calibration.preflight import run_preflight
from calibration.runner.runner import CalibrationRunner
from calibration.storage.artifacts import ArtifactStore
from calibration.storage.parquet import export_run_to_parquet
from calibration.storage.sqlite import SqliteRunStore
from calibration.providers.openrouter import OpenRouterProvider
from calibration.providers.openrouter_catalog import CatalogSnapshot, OpenRouterCatalogClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-value-calibration",
        description="Run reproducible LLM calibration experiments.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Execute or resume an experiment manifest")
    run.add_argument("manifest", type=Path)
    run.add_argument("--output", type=Path, default=Path(".calibration-runs"))
    run.add_argument("--resume-run-id")
    run.add_argument("--max-cases", type=int)
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--catalog", type=Path, help="Use a frozen catalog snapshot instead of refreshing OpenRouter")
    export = subparsers.add_parser("export", help="Create immutable Parquet exports for a run")
    export.add_argument("run_id")
    export.add_argument("--database", type=Path, default=Path(".calibration-runs/runs.sqlite3"))
    export.add_argument("--output", type=Path, default=Path(".calibration-runs/exports"))
    export.add_argument("--artifacts", type=Path, default=Path(".calibration-runs/objects"))
    audit = subparsers.add_parser("audit", help="Audit provenance and artifacts")
    audit.add_argument("run_id")
    audit.add_argument("--database", type=Path, default=Path(".calibration-runs/runs.sqlite3"))
    audit.add_argument("--artifacts", type=Path, default=Path(".calibration-runs/objects"))
    preflight = subparsers.add_parser("preflight", help="Validate an approved run and estimate spend")
    preflight.add_argument("manifest", type=Path)
    preflight.add_argument("--catalog", type=Path, required=True)
    preflight.add_argument("--output", type=Path, default=Path("preflight.json"))
    preflight.add_argument("--canary", action="store_true")
    preflight.add_argument("--approval-artifact", type=Path)
    prepare = subparsers.add_parser("prepare-dataset", help="Acquire and hash a registry dataset")
    prepare.add_argument("registry", type=Path)
    prepare.add_argument("dataset_id")
    prepare.add_argument("--cache", type=Path, default=Path(".calibration-runs/datasets"))
    prepare.add_argument("--offline", action="store_true")
    check = subparsers.add_parser("check-adapter", help="Run the canonical adapter conformance suite")
    check.add_argument("manifest", type=Path)
    plans = subparsers.add_parser("freeze-plans", help="Freeze the pre-registered Phase 4 experiment plans")
    plans.add_argument("--output", type=Path, default=Path("calibration/data/experiment_plans.json"))
    status = subparsers.add_parser("status", help="Show persisted run metrics and operational alerts")
    status.add_argument("run_id", nargs="?")
    status.add_argument("--database", type=Path, default=Path(".calibration-runs/runs.sqlite3"))
    status.add_argument("--output", type=Path)
    status.add_argument("--expected-cells", type=int)
    cancel = subparsers.add_parser("cancel", help="Request a resumable run cancellation")
    cancel.add_argument("run_id")
    cancel.add_argument("--database", type=Path, default=Path(".calibration-runs/runs.sqlite3"))
    fit = subparsers.add_parser("fit-candidate", help="Fit a candidate profile from locked fitting data")
    fit.add_argument("fitting_data", type=Path)
    fit.add_argument("--output", type=Path, default=Path("candidate-profile.json"))
    fit.add_argument("--manifest-hash", action="append", required=True)
    fit.add_argument("--aa-snapshot", required=True)
    fit.add_argument("--profile-version", default="candidate-1.0.0")
    fit.add_argument("--bootstrap-replicates", type=int, default=20)
    render = subparsers.add_parser("render-report", help="Render a calibration card JSON to Markdown and HTML")
    render.add_argument("card", type=Path)
    render.add_argument("--output", type=Path, required=True)
    promotion_check = subparsers.add_parser("promotion-check", help="Evaluate candidate promotion evidence")
    promotion_check.add_argument("candidate", type=Path)
    promotion_check.add_argument("baseline", type=Path)
    promotion_check.add_argument("evidence", type=Path)
    promote = subparsers.add_parser("promote-candidate", help="Promote a reviewed immutable candidate")
    promote.add_argument("candidate", type=Path)
    promote.add_argument("baseline", type=Path)
    promote.add_argument("evidence", type=Path)
    promote.add_argument("--store", type=Path, default=Path(".calibration-profiles"))
    promote.add_argument("--application-directory", type=Path)
    rollback = subparsers.add_parser("rollback-profile", help="Point the active profile at an immutable prior hash")
    rollback.add_argument("profile_hash")
    rollback.add_argument("--store", type=Path, default=Path(".calibration-profiles"))
    history = subparsers.add_parser("promotion-history", help="Show append-only profile promotion history")
    history.add_argument("--store", type=Path, default=Path(".calibration-profiles"))
    rehearsal = subparsers.add_parser("rehearse", help="Run the offline interruption/resume end-to-end rehearsal")
    rehearsal.add_argument("--output", type=Path, default=Path(".rehearsal-runs"))
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    if arguments.command == "export":
        with SqliteRunStore(arguments.database) as store:
            result = export_run_to_parquet(
                store,
                arguments.run_id,
                arguments.output,
                artifacts=ArtifactStore(arguments.artifacts),
            )
        print(json.dumps({"run_id": result.run_id, "files": result.files, "row_counts": result.row_counts}, indent=2, sort_keys=True))
        return

    if arguments.command == "audit":
        with SqliteRunStore(arguments.database) as store:
            report = {
                "run_id": arguments.run_id,
                "provenance_errors": store.audit_provenance(arguments.run_id),
                "artifact_errors": ArtifactStore(arguments.artifacts).audit_integrity(),
            }
        print(json.dumps(report, indent=2, sort_keys=True))
        if report["provenance_errors"] or report["artifact_errors"]:
            raise SystemExit(1)
        return

    if arguments.command == "status":
        with SqliteRunStore(arguments.database) as store:
            run_id = arguments.run_id or store.latest_run_id()
            summary = store.run_summary(run_id)
            manifest = load_manifest_from_summary(summary)
            status_path = arguments.output or arguments.database.parent / f"{run_id}-status.json"
            write_run_status(
                store,
                run_id,
                status_path,
                limits=BudgetLimits.from_manifest(manifest),
                expected_cells=arguments.expected_cells,
            )
            result = json.loads(status_path.read_text(encoding="utf-8"))
            result["summary"] = summary
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    if arguments.command == "cancel":
        with SqliteRunStore(arguments.database) as store:
            store.cancel_run(arguments.run_id)
            result = store.run_summary(arguments.run_id)
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    if arguments.command == "fit-candidate":
        if arguments.bootstrap_replicates < 1:
            raise SystemExit("--bootstrap-replicates must be positive")
        result = write_candidate_profile(
            arguments.fitting_data,
            arguments.output,
            manifest_hashes=tuple(arguments.manifest_hash),
            aa_snapshot=arguments.aa_snapshot,
            profile_version=arguments.profile_version,
            bootstrap_replicates=arguments.bootstrap_replicates,
        )
        print(json.dumps({"candidate_profile": str(result)}, indent=2, sort_keys=True))
        return

    if arguments.command == "render-report":
        card = CalibrationCard(**json.loads(arguments.card.read_text(encoding="utf-8")))
        files = write_calibration_card(card, arguments.output)
        print(json.dumps({"files": [str(path) for path in files]}, indent=2, sort_keys=True))
        return

    if arguments.command == "promotion-check":
        result = check_promotion(arguments.candidate, arguments.baseline, arguments.evidence)
        print(json.dumps(result.to_json(), indent=2, sort_keys=True))
        if not result.passed:
            raise SystemExit(1)
        return

    if arguments.command == "promote-candidate":
        result = PromotionStore(arguments.store).promote(
            arguments.candidate,
            arguments.baseline,
            arguments.evidence,
            application_directory=arguments.application_directory,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    if arguments.command == "rollback-profile":
        print(json.dumps(PromotionStore(arguments.store).rollback(arguments.profile_hash), indent=2, sort_keys=True))
        return

    if arguments.command == "promotion-history":
        print(json.dumps(PromotionStore(arguments.store).history(), indent=2, sort_keys=True))
        return

    if arguments.command == "rehearse":
        from calibration.rehearsal import run_rehearsal

        result = run_rehearsal(arguments.output)
        print(result.read_text(encoding="utf-8"))
        return

    if arguments.command == "preflight":
        manifest = load_manifest(arguments.manifest)
        catalog = CatalogSnapshot.from_json(
            json.loads(arguments.catalog.read_text(encoding="utf-8"))
        )
        dataset = JsonlDatasetAdapter(
            manifest.dataset, manifest_directory=arguments.manifest.resolve().parent
        )
        provider = (
            OpenRouterProvider.from_settings(catalog=catalog)
            if arguments.canary
            else None
        )
        report = asyncio.run(
            run_preflight(
                manifest,
                dataset,
                catalog,
                settings=CalibrationSettings.from_environment(),
                canary_provider=provider,
                run_canary=arguments.canary,
                approval_artifact=arguments.approval_artifact,
            )
        )
        report.write(arguments.output)
        print(json.dumps(report.to_json(), indent=2, sort_keys=True))
        if not report.passed:
            raise SystemExit(1)
        return

    if arguments.command == "prepare-dataset":
        spec = DatasetRegistry.from_file(arguments.registry).get(arguments.dataset_id)
        prepared = DatasetAcquirer(arguments.cache).prepare(spec, offline=arguments.offline)
        print(json.dumps(prepared.to_json(), indent=2, sort_keys=True))
        return

    if arguments.command == "check-adapter":
        manifest = load_manifest(arguments.manifest)
        if manifest.dataset.adapter != "jsonl":
            raise SystemExit(f"No built-in adapter for {manifest.dataset.adapter}")
        adapter = JsonlDatasetAdapter(
            manifest.dataset, manifest_directory=arguments.manifest.resolve().parent
        )
        adapter.prepare()
        print(json.dumps(validate_adapter(adapter, manifest.dataset.split).to_json(), indent=2, sort_keys=True))
        return

    if arguments.command == "freeze-plans":
        registry_hash = write_experiment_plan_registry(arguments.output)
        print(json.dumps({"output": str(arguments.output), "registry_hash": registry_hash}, indent=2, sort_keys=True))
        return

    if arguments.command != "run":
        raise RuntimeError(f"Unsupported command: {arguments.command}")

    if arguments.max_cases is not None and arguments.max_cases < 1:
        raise SystemExit("--max-cases must be at least 1")
    if arguments.workers < 1:
        raise SystemExit("--workers must be at least 1")

    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(arguments.manifest)
    catalog = None
    if any(model.provider == "openrouter" for model in manifest.models):
        if arguments.catalog:
            catalog = CatalogSnapshot.from_json(
                json.loads(arguments.catalog.read_text(encoding="utf-8"))
            )
        else:
            settings = CalibrationSettings.from_environment()
            catalog = asyncio.run(
                OpenRouterCatalogClient(settings.require_openrouter()).fetch_snapshot()
            )
            catalog.persist(ArtifactStore(output / "objects"))
        preflight_report = asyncio.run(
            run_preflight(
                manifest,
                JsonlDatasetAdapter(
                    manifest.dataset, manifest_directory=arguments.manifest.resolve().parent
                ),
                catalog,
                settings=CalibrationSettings.from_environment(),
            )
        )
        preflight_report.write(output / "preflight.json")
        preflight_report.require_pass()
    with SqliteRunStore(output / "runs.sqlite3") as store:
        runner = CalibrationRunner(
            manifest=manifest,
            manifest_path=arguments.manifest,
            store=store,
            artifacts=ArtifactStore(output / "objects"),
            catalog=catalog,
            max_workers=arguments.workers,
            budget_limits=BudgetLimits.from_manifest(manifest),
        )
        summary = asyncio.run(
            runner.run(
                resume_run_id=arguments.resume_run_id,
                max_cases=arguments.max_cases,
            )
        )
    print(json.dumps(summary, indent=2, sort_keys=True))


def load_manifest_from_summary(summary: dict[str, object]):
    """Rehydrate the manifest stored with a run for status-only operators."""
    from calibration.manifest import ExperimentManifest

    # run_summary intentionally exposes the immutable manifest only through the
    # provenance/run record; status can still use conservative defaults when a
    # legacy database does not include it in the summary.
    raw = summary.get("manifest_json")
    if isinstance(raw, str):
        return ExperimentManifest.model_validate(json.loads(raw))
    return ExperimentManifest.model_validate(
        {
            "experiment_id": str(summary["experiment_id"]),
            "dataset": {"adapter": "jsonl", "revision": "unknown", "split": "validation", "sample_seed": 0},
            "models": [{"catalog_id": "unknown", "provider": "unknown", "provider_model": "unknown", "aa_snapshot": "unknown"}],
            "generation": {"temperature": 0, "max_output_tokens": 1},
            "prompt_version": "unknown",
            "conditions": ["unknown"],
            "scorers": ["unknown"],
        }
    )


if __name__ == "__main__":
    main()
