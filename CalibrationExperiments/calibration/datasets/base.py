from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from calibration.models import CanonicalCase, CaseFeatures, Message, ScoreResult


class DatasetAdapter(ABC):
    adapter_version = "contract-1.0.0"

    @abstractmethod
    def prepare(self) -> None:
        """Verify that the pinned dataset is available and unchanged."""

    @abstractmethod
    def cases(self, split: str) -> Iterable[CanonicalCase]:
        """Yield canonical cases with stable IDs."""

    @abstractmethod
    def render(self, case: CanonicalCase, condition: str) -> tuple[Message, ...]:
        """Render provider-neutral messages for a condition."""

    @abstractmethod
    def metadata(self, case: CanonicalCase) -> CaseFeatures:
        """Return fitting features for a case."""

    def score(self, case: CanonicalCase, response: object) -> tuple[ScoreResult, ...]:
        """Return optional dataset-specific deterministic scores."""
        return ()


@dataclass(frozen=True, slots=True)
class AdapterConformanceReport:
    adapter: str
    adapter_version: str
    split: str
    case_count: int
    case_ids_hash: str
    deterministic: bool
    metadata_complete: bool
    rendered_deterministically: bool

    @property
    def passed(self) -> bool:
        return all(
            (
                self.case_count > 0,
                self.deterministic,
                self.metadata_complete,
                self.rendered_deterministically,
            )
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "adapter_version": self.adapter_version,
            "split": self.split,
            "case_count": self.case_count,
            "case_ids_hash": self.case_ids_hash,
            "deterministic": self.deterministic,
            "metadata_complete": self.metadata_complete,
            "rendered_deterministically": self.rendered_deterministically,
            "passed": self.passed,
        }


class AdapterConformanceError(ValueError):
    """Raised when an adapter drifts from the canonical dataset contract."""


def validate_adapter(adapter: DatasetAdapter, split: str) -> AdapterConformanceReport:
    """Exercise the adapter twice and verify stable IDs, metadata, and rendering."""
    first = list(adapter.cases(split))
    second = list(adapter.cases(split))
    if not first:
        raise AdapterConformanceError(f"Adapter returned no cases for split {split}")
    first_ids = [case.case_id for case in first]
    second_ids = [case.case_id for case in second]
    if len(first_ids) != len(set(first_ids)):
        raise AdapterConformanceError("Case IDs must be unique within a split")
    if first_ids != second_ids:
        raise AdapterConformanceError("Case ordering or membership is not deterministic")
    metadata_complete = True
    rendered_deterministically = True
    for case in first:
        if not case.case_id or not isinstance(case.input, dict):
            metadata_complete = False
        features = adapter.metadata(case)
        if features.case_id != case.case_id or not features.dataset_revision:
            metadata_complete = False
        if adapter.render(case, "baseline") != adapter.render(case, "baseline"):
            rendered_deterministically = False
    material = json.dumps(first_ids, separators=(",", ":"), ensure_ascii=False)
    report = AdapterConformanceReport(
        adapter=type(adapter).__name__,
        adapter_version=str(getattr(adapter, "adapter_version", "unknown")),
        split=split,
        case_count=len(first),
        case_ids_hash=hashlib.sha256(material.encode("utf-8")).hexdigest(),
        deterministic=first == second,
        metadata_complete=metadata_complete,
        rendered_deterministically=rendered_deterministically,
    )
    if not report.passed:
        raise AdapterConformanceError(json.dumps(report.to_json(), sort_keys=True))
    return report
