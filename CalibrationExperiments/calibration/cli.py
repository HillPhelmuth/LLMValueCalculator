from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from calibration.config import CalibrationSettings
from calibration.datasets.jsonl import JsonlDatasetAdapter
from calibration.manifest import load_manifest
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
        )
        summary = asyncio.run(
            runner.run(
                resume_run_id=arguments.resume_run_id,
                max_cases=arguments.max_cases,
            )
        )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
