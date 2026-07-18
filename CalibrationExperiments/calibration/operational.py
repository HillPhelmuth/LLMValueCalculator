from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Iterable


class OperationalReplayError(ValueError):
    """Raised when operational replay data is unsafe or insufficient."""


class ReplaySource(StrEnum):
    SYNTHETIC = "synthetic"
    SHADOW_TRAFFIC = "shadow_traffic"


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    record_id: str
    deidentified_input: str
    gold_decision: str
    severity: str
    exposure: str
    reversibility: str
    reviewer_decision: str | None
    review_time_seconds: float | None
    downstream_cost_band: str
    source: ReplaySource
    fault_id: str | None = None
    oracle_gate: bool = False

    def validate(self) -> None:
        if not self.record_id or not self.deidentified_input:
            raise OperationalReplayError("Replay records require de-identified input and ID")
        if self.source not in {ReplaySource.SYNTHETIC, ReplaySource.SHADOW_TRAFFIC}:
            raise OperationalReplayError("Replay source must be synthetic or shadow traffic")
        if self.review_time_seconds is not None and self.review_time_seconds < 0:
            raise OperationalReplayError("Review time cannot be negative")
        if self.oracle_gate:
            raise OperationalReplayError("Oracle gates cannot enter operational multiplier fitting")

    def to_json(self) -> dict[str, Any]:
        self.validate()
        return asdict(self) | {"source": self.source.value}


@dataclass(frozen=True, slots=True)
class Fault:
    fault_id: str
    failure_class: str
    severity: str
    reversible: bool
    seeded_payload: dict[str, Any]


class FaultLibrary:
    def __init__(self, faults: Iterable[Fault] = ()) -> None:
        self._faults: dict[str, Fault] = {}
        for fault in faults:
            if fault.fault_id in self._faults:
                raise OperationalReplayError(f"Duplicate fault ID: {fault.fault_id}")
            self._faults[fault.fault_id] = fault

    def add(self, fault: Fault) -> None:
        if fault.fault_id in self._faults:
            raise OperationalReplayError(f"Duplicate fault ID: {fault.fault_id}")
        self._faults[fault.fault_id] = fault

    def get(self, fault_id: str) -> Fault:
        try:
            return self._faults[fault_id]
        except KeyError as error:
            raise OperationalReplayError(f"Unknown fault ID: {fault_id}") from error


def deidentify_input(value: Any, *, salt: str) -> str:
    """Replace raw operational content with a deterministic non-reversible token."""
    if not salt:
        raise OperationalReplayError("De-identification requires a non-empty salt")
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256((salt + canonical).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class OperationalFitResult:
    customer_multiplier: float
    approval_multiplier: float
    subgroup_estimates: dict[str, float]
    retained_prior: bool
    privacy_review: str
    minimum_sample_size: int
    diagnostics: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def fit_operational_multipliers(
    records: Iterable[ReplayRecord],
    *,
    minimum_sample_size: int = 100,
    privacy_review: str = "approved",
) -> OperationalFitResult:
    rows = tuple(records)
    for row in rows:
        row.validate()
    if privacy_review != "approved":
        raise OperationalReplayError("Operational replay requires an approved privacy review")
    usable = tuple(row for row in rows if not row.oracle_gate)
    if len(usable) < minimum_sample_size:
        return OperationalFitResult(
            1.0,
            1.0,
            {},
            True,
            privacy_review,
            minimum_sample_size,
            {"usable_rows": len(usable), "reason": "minimum sample size not met"},
        )
    exposed = [row for row in usable if row.exposure not in {"none", "unknown"}]
    critical_exposed = [row for row in exposed if row.severity in {"critical", "high"}]
    reviewed = [row for row in usable if row.reviewer_decision is not None]
    residual_critical = [
        row for row in reviewed
        if row.severity in {"critical", "high"} and row.reviewer_decision != row.gold_decision
    ]
    customer = (len(critical_exposed) / len(exposed)) / max(1e-9, len(critical_exposed) / len(usable)) if exposed else 1.0
    approval = len(residual_critical) / len(reviewed) if reviewed else 0.0
    subgroup: dict[str, list[float]] = {}
    for row in usable:
        subgroup.setdefault(row.severity, []).append(float(row.severity in {"critical", "high"}))
    subgroup_estimates = {key: sum(values) / len(values) for key, values in subgroup.items()}
    return OperationalFitResult(
        max(0.0, customer),
        max(0.0, approval),
        subgroup_estimates,
        False,
        privacy_review,
        minimum_sample_size,
        {"usable_rows": len(usable), "exposed_rows": len(exposed), "reviewed_rows": len(reviewed), "oracle_gates_excluded": True},
    )
