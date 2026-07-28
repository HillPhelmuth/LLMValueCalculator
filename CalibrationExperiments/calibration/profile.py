from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from calibration.schema import SCHEMA_VERSION, validate_record


class ProfileError(ValueError):
    """Raised when a calibration profile is invalid or immutable storage is violated."""


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    profile_version: str
    curve_segments: tuple[dict[str, Any], ...]
    tau: dict[str, float]
    error_floor: float
    adjustments: dict[str, Any]
    risk_multipliers: dict[str, Any]
    uncertainty: dict[str, Any]
    manifest_hashes: tuple[str, ...]
    fitting_data_hash: str
    aa_snapshot: str
    source_estimate_ids: tuple[str, ...] = ()
    promotion_decisions: dict[str, str] = field(default_factory=dict)
    profile_hash: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.validate_semantics()
        calculated = _hash_document(self._document_without_hash())
        if self.profile_hash and self.profile_hash != calculated:
            raise ProfileError(
                f"Profile hash mismatch: expected {self.profile_hash}, calculated {calculated}"
            )
        if not self.profile_hash:
            object.__setattr__(self, "profile_hash", calculated)

    def _document_without_hash(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile_version": self.profile_version,
            "curve_segments": list(self.curve_segments),
            "tau": self.tau,
            "error_floor": self.error_floor,
            "adjustments": self.adjustments,
            "risk_multipliers": self.risk_multipliers,
            "uncertainty": self.uncertainty,
            "manifest_hashes": list(self.manifest_hashes),
            "fitting_data_hash": self.fitting_data_hash,
            "aa_snapshot": self.aa_snapshot,
            "source_estimate_ids": list(self.source_estimate_ids),
            "promotion_decisions": self.promotion_decisions,
        }

    def to_json(self) -> dict[str, Any]:
        document = self._document_without_hash() | {"profile_hash": self.profile_hash}
        validate_record("calibration_profile", document)
        return document

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "CalibrationProfile":
        document = dict(value)
        validate_record("calibration_profile", document)
        return cls(
            profile_version=str(document["profile_version"]),
            curve_segments=tuple(dict(segment) for segment in document["curve_segments"]),
            tau={str(key): float(item) for key, item in document["tau"].items()},
            error_floor=float(document["error_floor"]),
            adjustments=dict(document["adjustments"]),
            risk_multipliers=dict(document["risk_multipliers"]),
            uncertainty=dict(document["uncertainty"]),
            manifest_hashes=tuple(str(item) for item in document["manifest_hashes"]),
            fitting_data_hash=str(document["fitting_data_hash"]),
            aa_snapshot=str(document["aa_snapshot"]),
            source_estimate_ids=tuple(str(item) for item in document.get("source_estimate_ids", ())),
            promotion_decisions={str(key): str(item) for key, item in document.get("promotion_decisions", {}).items()},
            profile_hash=str(document["profile_hash"]),
            schema_version=str(document["schema_version"]),
        )

    @classmethod
    def load(cls, path: str | Path) -> "CalibrationProfile":
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))

    def write_immutable(self, root: str | Path) -> Path:
        destination = Path(root) / self.profile_version / self.profile_hash / "profile.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(self.to_json(), sort_keys=True, indent=2, ensure_ascii=False)
        if destination.exists() and destination.read_text(encoding="utf-8") != encoded:
            raise ProfileError(f"Immutable profile cannot be overwritten: {destination}")
        destination.write_text(encoded, encoding="utf-8")
        return destination

    def validate_semantics(self) -> None:
        if not self.profile_version or not self.fitting_data_hash or not self.aa_snapshot:
            raise ProfileError("Profile version, fitting-data hash, and AA snapshot are required")
        if not self.manifest_hashes or any(len(item) < 16 for item in self.manifest_hashes):
            raise ProfileError("Profile must reference at least one manifest hash")
        if not 0 <= self.error_floor < 1:
            raise ProfileError("Profile error floor must be in [0, 1)")
        if not self.curve_segments:
            raise ProfileError("Profile requires at least one curve segment")
        slopes = [float(segment["slope"]) for segment in self.curve_segments]
        if abs(slopes[0] - 1.0) > 1e-9 or any(slope <= 0 for slope in slopes[1:]):
            raise ProfileError("Profile curve slopes must start at 1.0 and remain positive")
        if any(left > right for left, right in zip(slopes, slopes[1:])):
            raise ProfileError("Profile curve slopes must be nondecreasing")
        uppers = [segment["upper"] for segment in self.curve_segments]
        if any(value is None for value in uppers[:-1]) or uppers[-1] is not None and not isinstance(uppers[-1], (int, float)):
            raise ProfileError("Only the final profile curve segment may be unbounded")
        finite_uppers = [float(value) for value in uppers if value is not None]
        if any(left >= right for left, right in zip(finite_uppers, finite_uppers[1:])):
            raise ProfileError("Profile curve upper bounds must be strictly increasing")
        if any(value <= 0 for value in self.tau.values()):
            raise ProfileError("Profile tau values must be positive")


def baseline_profile(*, profile_version: str = "baseline-1.0.0") -> CalibrationProfile:
    return CalibrationProfile(
        profile_version=profile_version,
        curve_segments=(
            {"upper": 10.0, "slope": 1.0},
            {"upper": 20.0, "slope": 1.4},
            {"upper": 30.0, "slope": 1.8},
            {"upper": 40.0, "slope": 2.2},
            {"upper": 50.0, "slope": 2.6},
            {"upper": None, "slope": 3.0},
        ),
        tau={"soft": 8.0, "normal": 5.0, "sharp": 3.0},
        error_floor=0.01,
        adjustments={},
        risk_multipliers={},
        uncertainty={},
        manifest_hashes=("baseline-manifest-1.0.0",),
        fitting_data_hash="baseline-fitting-data-1.0.0",
        aa_snapshot="baseline-aa-snapshot-1.0.0",
    )


def _hash_document(document: Mapping[str, Any]) -> str:
    normalized = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
