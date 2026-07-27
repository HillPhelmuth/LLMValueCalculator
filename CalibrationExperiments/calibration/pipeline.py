from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class PipelineError(ValueError):
    """Raised when a CI or calibration pipeline would violate a safety gate."""


class PipelineMode(StrEnum):
    PULL_REQUEST = "pull_request"
    NIGHTLY = "nightly"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class PipelineBudget:
    max_requests: int
    max_tokens: int
    max_usd: float
    timeout_minutes: int

    def validate(self) -> None:
        if (
            min(self.max_requests, self.max_tokens, self.timeout_minutes) < 1
            or self.max_usd < 0
        ):
            raise PipelineError("Pipeline budgets must be positive and bounded")


@dataclass(frozen=True, slots=True)
class PipelineSpec:
    name: str
    mode: PipelineMode
    manifests: tuple[str, ...]
    budget: PipelineBudget
    max_cases: int
    allow_provider_calls: bool = False
    requires_approval: bool = False
    writes_profile: bool = False

    def validate(self) -> None:
        self.budget.validate()
        if not self.name or not self.manifests or self.max_cases < 1:
            raise PipelineError(
                "Pipeline specs require a name, manifest, and positive case limit"
            )
        if self.mode is PipelineMode.PULL_REQUEST and self.writes_profile:
            raise PipelineError(
                "Pull-request pipelines cannot write calibration profiles"
            )
        if self.mode is PipelineMode.NIGHTLY and self.writes_profile:
            raise PipelineError("Nightly pipelines cannot publish calibration profiles")
        if self.mode is PipelineMode.FULL and (
            not self.requires_approval or not self.writes_profile
        ):
            raise PipelineError(
                "Full pipelines require approval and candidate-profile output"
            )
        if (
            self.allow_provider_calls
            and self.mode is PipelineMode.PULL_REQUEST
            and not self.requires_approval
        ):
            raise PipelineError(
                "Provider calls on pull requests require a protected approval gate"
            )

    def to_json(self) -> dict[str, Any]:
        self.validate()
        return asdict(self) | {"mode": self.mode.value}


@dataclass(frozen=True, slots=True)
class NightlyDriftReport:
    run_id: str
    baseline_run_id: str | None
    coverage: dict[str, Any]
    failures: dict[str, int]
    score_drift: dict[str, float]
    latency: dict[str, float]
    spend: dict[str, float]
    metrics: dict[str, float] = field(default_factory=dict)
    alerts: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return asdict(self) | {"alerts": list(self.alerts)}


def classify_nightly_drift(
    *,
    run_id: str,
    baseline_run_id: str | None,
    current: dict[str, float],
    baseline: dict[str, float] | None,
    failures: dict[str, int],
    latency: dict[str, float],
    spend: dict[str, float],
    score_threshold: float = 0.05,
    latency_threshold: float = 0.25,
) -> NightlyDriftReport:
    baseline = baseline or {}
    drift = {
        key: current[key] - baseline[key] for key in current.keys() & baseline.keys()
    }
    alerts: list[str] = []
    if any(abs(value) >= score_threshold for value in drift.values()):
        alerts.append("model_behavior_drift")
    if failures.get("provider", 0) or failures.get("infrastructure", 0):
        alerts.append("infrastructure_drift")
    if (
        baseline.get("latency", 0)
        and latency.get("latency", 0) / baseline["latency"] - 1 >= latency_threshold
    ):
        alerts.append("latency_drift")
    return NightlyDriftReport(
        run_id=run_id,
        baseline_run_id=baseline_run_id,
        coverage={"current_metrics": len(current), "baseline_metrics": len(baseline)},
        failures=failures,
        score_drift=drift,
        latency=latency,
        spend=spend,
        metrics=current,
        alerts=tuple(dict.fromkeys(alerts)),
    )


@dataclass(frozen=True, slots=True)
class FullRunApproval:
    approved_by: str
    approved_utc: str
    manifest_set_hash: str
    model_snapshot_hash: str
    code_commit: str
    budget: PipelineBudget
    candidate_only: bool = True

    def validate(self) -> None:
        self.budget.validate()
        if not all(
            (
                self.approved_by,
                self.approved_utc,
                self.manifest_set_hash,
                self.model_snapshot_hash,
                self.code_commit,
            )
        ):
            raise PipelineError(
                "Full-run approval requires reviewer, hashes, and code commit"
            )
        if not self.candidate_only:
            raise PipelineError(
                "Full pipeline cannot approve automatic production promotion"
            )

    def to_json(self) -> dict[str, Any]:
        self.validate()
        return asdict(self) | {"budget": asdict(self.budget)}


def manifest_set_hash(paths: tuple[str, ...] | list[str]) -> str:
    """Hash manifest names and bytes so approvals bind to the exact input set."""
    entries = []
    for raw_path in sorted(str(path) for path in paths):
        path = Path(raw_path)
        if not path.is_file():
            raise PipelineError(f"Manifest does not exist: {path}")
        entries.append(
            {
                "path": path.as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    if not entries:
        raise PipelineError("A full run requires at least one manifest")
    return hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def write_nightly_report(
    run_root: str | Path,
    run_id: str,
    output: str | Path,
    expected_cases: int,
    mode: str,
    baseline: str | Path | None = None,
) -> Path:
    """Summarize a run and compare it with an optional prior successful report."""
    from calibration.storage.sqlite import SqliteRunStore

    database = Path(run_root) / "runs.sqlite3"
    with SqliteRunStore(database) as store:
        summary = store.run_summary(run_id)
        attempts = store.rows_for_export("attempts", run_id)
        scores = store.rows_for_export("scores", run_id)
        transport = store.rows_for_export("transport_events", run_id)
    successes = [
        float(row["success"]) for row in scores if row.get("success") is not None
    ]
    current = {
        "success_rate": sum(successes) / len(successes) if successes else 0.0,
    }
    latency_values = [float(row["latency_ms"]) for row in attempts]
    latency = {
        "latency": sum(latency_values) / len(latency_values) if latency_values else 0.0
    }
    raw_spend = summary.get("provider_cost")
    spend = {"usd": 0.0 if raw_spend is None else float(str(raw_spend))}
    failures = {
        "provider": sum(row.get("event_type") == "failed" for row in transport),
        "infrastructure": 0,
    }
    baseline_metrics: dict[str, float] | None = None
    baseline_run_id: str | None = None
    if baseline and Path(baseline).is_file():
        previous = json.loads(Path(baseline).read_text(encoding="utf-8"))
        baseline_metrics = {
            str(key): float(value) for key, value in previous.get("metrics", {}).items()
        }
        baseline_run_id = previous.get("run_id")
    report = classify_nightly_drift(
        run_id=run_id,
        baseline_run_id=baseline_run_id,
        current=current,
        baseline=baseline_metrics,
        failures=failures,
        latency=latency,
        spend=spend,
    )
    report = NightlyDriftReport(
        run_id=report.run_id,
        baseline_run_id=report.baseline_run_id,
        coverage={
            "expected_cases": expected_cases,
            "observed_attempts": len(attempts),
            "missing_cases": max(expected_cases - len(attempts), 0),
            "mode": mode,
        },
        failures=report.failures,
        score_drift=report.score_drift,
        latency=report.latency,
        spend=report.spend,
        metrics=report.metrics,
        alerts=report.alerts,
    )
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report.to_json(), sort_keys=True, indent=2), encoding="utf-8"
    )
    return destination


def validate_fitting_gate(
    database: str | Path,
    artifacts: str | Path,
    manifest_path: str | Path,
    max_cases: int,
    *,
    max_missing_fraction: float = 0.02,
) -> dict[str, Any]:
    """Require completed, intact, sufficiently covered data before fitting."""
    from calibration.datasets.jsonl import JsonlDatasetAdapter
    from calibration.manifest import load_manifest
    from calibration.storage.artifacts import ArtifactStore
    from calibration.storage.sqlite import SqliteRunStore

    manifest = load_manifest(manifest_path)
    dataset = JsonlDatasetAdapter(
        manifest.dataset, manifest_directory=Path(manifest_path).resolve().parent
    )
    dataset.prepare()
    cases = tuple(dataset.cases(manifest.dataset.split))
    expected = (
        min(max_cases, len(cases))
        * len(manifest.models)
        * len(manifest.conditions)
        * manifest.generation.repeats
    )
    with SqliteRunStore(database) as store:
        run_id = store.latest_run_id()
        summary = store.run_summary(run_id)
        provenance_errors = store.audit_provenance(run_id)
        attempts = store.rows_for_export("attempts", run_id)
        scores = store.rows_for_export("scores", run_id)
    artifact_errors = ArtifactStore(artifacts).audit_integrity()
    observed = len(attempts)
    missing_fraction = max(expected - observed, 0) / expected if expected else 1.0
    gate = {
        "run_id": run_id,
        "status": summary["status"],
        "expected_cells": expected,
        "observed_attempts": observed,
        "score_rows": len(scores),
        "missing_fraction": missing_fraction,
        "provenance_errors": provenance_errors,
        "artifact_errors": artifact_errors,
    }
    if summary["status"] != "completed":
        raise PipelineError(
            f"Fitting requires a completed run, got {summary['status']}"
        )
    if (
        provenance_errors
        or artifact_errors
        or not scores
        or missing_fraction > max_missing_fraction
    ):
        raise PipelineError(f"Fitting integrity/coverage gate failed: {gate}")
    return gate


def write_candidate_profile(
    fitting_data: str | Path,
    output: str | Path,
    *,
    manifest_hashes: tuple[str, ...],
    aa_snapshot: str,
    profile_version: str = "candidate-1.0.0",
    bootstrap_replicates: int = 20,
) -> Path:
    """Fit and write a candidate-only profile after the full-run gate passes."""
    from calibration.fitting import BernoulliRow
    from calibration.profile import CalibrationProfile
    from calibration.statistical import StatisticalModel, fit_statistical_model

    source = Path(fitting_data)
    if not source.is_file():
        raise PipelineError(f"Fitting data does not exist: {source}")
    rows = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            rows.append(
                BernoulliRow(
                    intelligence_index=float(value["intelligence_index"]),
                    success=bool(value["success"]),
                    split=str(value.get("split", "fit")),
                    dataset_id=str(value.get("dataset_id", "unknown")),
                    model_id=str(value.get("model_id", "unknown")),
                    case_id=str(value.get("case_id", "unknown")),
                    prompt_id=str(value.get("prompt_id", "unknown")),
                    category=value.get("category"),
                    difficulty=None
                    if value.get("difficulty") is None
                    else float(value["difficulty"]),
                    tau_key=str(value.get("tau_key", "normal")),
                )
            )
    fit = fit_statistical_model(
        StatisticalModel.BERNOULLI,
        rows,
        bootstrap_replicates=bootstrap_replicates,
        error_floor=0.01,
    )
    if not fit.diagnostics.promotable:
        raise PipelineError(
            f"Candidate fit failed promotion-readiness diagnostics: {fit.diagnostics.to_json()}"
        )
    curve = fit.estimates["curve"]
    projected_slopes: list[float] = []
    for index, slope in enumerate(curve["slopes"]):
        value = 1.0 if index == 0 else max(float(slope), projected_slopes[-1])
        projected_slopes.append(value)
    curve_segments = tuple(
        {
            "upper": 10.0 * (index + 1) if index < 5 else None,
            "slope": slope,
        }
        for index, slope in enumerate(projected_slopes)
    )
    profile = CalibrationProfile(
        profile_version=profile_version,
        curve_segments=curve_segments,
        tau={
            "soft": curve["tau_ratios"]["normal"],
            "normal": curve["tau_ratios"]["domain"],
            "sharp": curve["tau_ratios"]["reasoning"],
        },
        error_floor=0.01,
        adjustments={},
        # Experiment 1 changes only the capability curve and tau.  Empty maps
        # mean the compiled adjustment and risk priors remain authoritative.
        risk_multipliers={},
        uncertainty={
            "fit": fit.to_json(),
            "candidate_only": True,
            "error_floor_status": "provisional-not-promoted",
        },
        manifest_hashes=manifest_hashes,
        fitting_data_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
        aa_snapshot=aa_snapshot,
    )
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(profile.to_json(), sort_keys=True, indent=2), encoding="utf-8"
    )
    return destination


def write_pipeline_report(
    report: NightlyDriftReport | PipelineSpec, path: str | Path
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    value = report.to_json()
    destination.write_text(
        json.dumps(value, sort_keys=True, indent=2), encoding="utf-8"
    )
    return destination
