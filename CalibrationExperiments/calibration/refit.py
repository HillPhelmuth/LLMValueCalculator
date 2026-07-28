from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from calibration.fitting import BernoulliRow, fit_monotone_curve
from calibration.profile import CalibrationProfile


class RefitError(ValueError):
    """Raised when staged refitting would violate capability/risk isolation."""


@dataclass(frozen=True, slots=True)
class ParameterDecision:
    parameter: str
    decision: str
    estimate: float | None
    lower: float | None
    upper: float | None
    source_experiment_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    rationale: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CapabilityFreeze:
    predictions: tuple[dict[str, Any], ...]
    prediction_hash: str
    source_experiment_ids: tuple[str, ...]
    diagnostics: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return asdict(self) | {"predictions": list(self.predictions), "source_experiment_ids": list(self.source_experiment_ids)}


@dataclass(frozen=True, slots=True)
class StagedRefitResult:
    capability: CapabilityFreeze
    risk_decisions: tuple[ParameterDecision, ...]
    ablations: dict[str, Any]
    sensitivity: dict[str, Any]
    parameter_decisions: tuple[ParameterDecision, ...]
    promotable: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "capability": self.capability.to_json(),
            "risk_decisions": [asdict(item) for item in self.risk_decisions],
            "ablations": self.ablations,
            "sensitivity": self.sensitivity,
            "parameter_decisions": [asdict(item) for item in self.parameter_decisions],
            "promotable": self.promotable,
        }


def freeze_capability_predictions(
    predictions: Iterable[Mapping[str, Any]], *, source_experiment_ids: tuple[str, ...]
) -> CapabilityFreeze:
    rows = tuple(dict(row) for row in predictions)
    if not rows or not source_experiment_ids:
        raise RefitError("Capability freeze requires predictions and source experiments")
    keys = [
        (row.get("model_id"), row.get("dataset_id"), row.get("case_id"), row.get("condition_id"), row.get("repeat_index"))
        for row in rows
    ]
    if len(keys) != len(set(keys)):
        raise RefitError("Capability predictions contain duplicate pathways")
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return CapabilityFreeze(
        rows,
        hashlib.sha256(encoded).hexdigest(),
        source_experiment_ids,
        {"risk_rows_excluded": True, "retry_rows_excluded": True, "partial_value_rows_excluded": True},
    )


def staged_refit(
    capability_rows: Iterable[BernoulliRow],
    risk_rows: Iterable[Mapping[str, Any]],
    *,
    source_experiment_ids: tuple[str, ...] = ("experiment-1", "experiment-2", "experiment-3", "experiment-4", "experiment-5", "experiment-6"),
    risk_experiment_ids: tuple[str, ...] = ("experiment-7", "experiment-8", "operational-replay"),
) -> StagedRefitResult:
    capability = tuple(capability_rows)
    if not capability:
        raise RefitError("Capability refit requires experiment 1-6 rows")
    curve = fit_monotone_curve(capability)
    predictions = tuple(
        {
            "model_id": row.model_id,
            "dataset_id": row.dataset_id,
            "case_id": row.case_id,
            "condition_id": "baseline",
            "repeat_index": 0,
            "predicted_success": curve.curve.predict(row.intelligence_index),
            "split": row.split,
        }
        for row in capability
    )
    freeze = freeze_capability_predictions(predictions, source_experiment_ids=source_experiment_ids)
    risk = tuple(risk_rows)
    if risk and any(str(row.get("experiment_id", "")) in set(source_experiment_ids) for row in risk):
        raise RefitError("Risk rows cannot be mixed into the capability fit")
    risk_decisions = tuple(
        ParameterDecision(
            parameter=str(row.get("parameter", "risk_parameter")),
            decision=str(row.get("decision", "keep")),
            estimate=None if row.get("estimate") is None else float(row["estimate"]),
            lower=None if row.get("lower") is None else float(row["lower"]),
            upper=None if row.get("upper") is None else float(row["upper"]),
            source_experiment_ids=risk_experiment_ids,
            evidence_ids=tuple(str(item) for item in row.get("evidence_ids", ())),
            rationale=("fitted after capability predictions were frozen",),
        )
        for row in risk
    )
    return StagedRefitResult(
        capability=freeze,
        risk_decisions=risk_decisions,
        ablations={"capability_only": True, "risk_added_after_freeze": True},
        sensitivity={"duplicate_pathways": False, "unstable_interactions": False},
        parameter_decisions=risk_decisions,
        promotable=bool(freeze.predictions) and not any(decision.decision == "change" and decision.lower is None for decision in risk_decisions),
    )


def profile_from_refit(
    result: StagedRefitResult,
    *,
    profile_version: str,
    manifest_hashes: tuple[str, ...],
    aa_snapshot: str,
    adjustments: dict[str, Any] | None = None,
    risk_multipliers: dict[str, Any] | None = None,
) -> CalibrationProfile:
    if not result.promotable:
        raise RefitError("Cannot create a candidate profile from a non-promotable refit")
    curve = fit_monotone_curve(
        tuple(
            BernoulliRow(
                float(row["predicted_success"]),
                bool(row["predicted_success"] >= 0.5),
                "fit",
                str(row["dataset_id"]),
                str(row["model_id"]),
                str(row["case_id"]),
            )
            for row in result.capability.predictions
        )
    ).curve
    return CalibrationProfile(
        profile_version=profile_version,
        curve_segments=tuple(
            {"upper": 10.0 * (index + 1) if index < 5 else None, "slope": slope}
            for index, slope in enumerate(curve.slopes)
        ),
        tau={"soft": curve.tau_ratios.normal, "normal": curve.tau_ratios.normal, "sharp": curve.tau_ratios.reasoning},
        error_floor=curve.error_floor,
        adjustments=adjustments or {},
        risk_multipliers=risk_multipliers or {},
        uncertainty={"capability_prediction_hash": result.capability.prediction_hash},
        manifest_hashes=manifest_hashes,
        fitting_data_hash=result.capability.prediction_hash,
        aa_snapshot=aa_snapshot,
        source_estimate_ids=tuple(decision.parameter for decision in result.parameter_decisions),
        promotion_decisions={decision.parameter: decision.decision for decision in result.parameter_decisions},
    )
