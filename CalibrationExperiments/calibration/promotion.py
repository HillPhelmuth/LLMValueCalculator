from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from calibration.profile import CalibrationProfile, ProfileError
from calibration.profile_codegen import write_application_artifacts


class PromotionError(ValueError):
    """Raised when a candidate does not satisfy the explicit promotion contract."""


@dataclass(frozen=True, slots=True)
class PromotionCriteria:
    min_sign_agreement: float = 0.8
    min_held_out_improvement: float = 0.02
    min_held_out_brier_improvement: float = 0.01
    max_material_recommendation_fraction: float = 0.25
    require_no_duplication: bool = True
    required_approvals: int = 1

    def validate(self) -> None:
        if not 0 <= self.min_sign_agreement <= 1:
            raise PromotionError("Sign-agreement threshold must be in [0, 1]")
        if self.min_held_out_improvement < 0:
            raise PromotionError("Held-out improvement threshold cannot be negative")
        if self.min_held_out_brier_improvement < 0:
            raise PromotionError(
                "Held-out Brier improvement threshold cannot be negative"
            )
        if not 0 <= self.max_material_recommendation_fraction <= 1:
            raise PromotionError("Material-change threshold must be in [0, 1]")
        if self.required_approvals < 1:
            raise PromotionError("At least one explicit reviewer approval is required")


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
    candidate_profile_hash: str
    baseline_profile_hash: str
    sign_agreement: float
    held_out_improvement: float
    duplicate_pathways: int
    material_recommendation_fraction: float
    intervals: dict[str, Any] = field(default_factory=dict)
    calibration_cards: tuple[str, ...] = ()
    scenario_diff: str | None = None
    provenance_ids: tuple[str, ...] = ()
    cost_usd: float = 0.0
    limitations: tuple[str, ...] = ()
    reviewers: tuple[dict[str, str], ...] = ()
    held_out_brier_improvement: float = 0.0

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "PromotionEvidence":
        reviewers = tuple(
            {str(key): str(item) for key, item in dict(row).items()}
            for row in value.get("reviewers", value.get("reviewer_approvals", ()))
        )
        return cls(
            candidate_profile_hash=str(value["candidate_profile_hash"]),
            baseline_profile_hash=str(value["baseline_profile_hash"]),
            sign_agreement=float(value["sign_agreement"]),
            held_out_improvement=float(value["held_out_improvement"]),
            duplicate_pathways=int(value.get("duplicate_pathways", 0)),
            material_recommendation_fraction=float(
                value["material_recommendation_fraction"]
            ),
            intervals=dict(value.get("intervals", {})),
            calibration_cards=tuple(
                str(item) for item in value.get("calibration_cards", ())
            ),
            scenario_diff=None
            if value.get("scenario_diff") is None
            else str(value["scenario_diff"]),
            provenance_ids=tuple(str(item) for item in value.get("provenance_ids", ())),
            cost_usd=float(value.get("cost_usd", 0.0)),
            limitations=tuple(str(item) for item in value.get("limitations", ())),
            reviewers=reviewers,
            held_out_brier_improvement=float(
                value.get("held_out_brier_improvement", 0.0)
            ),
        )

    @classmethod
    def load(cls, path: str | Path) -> "PromotionEvidence":
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_json(self) -> dict[str, Any]:
        return asdict(self) | {
            "calibration_cards": list(self.calibration_cards),
            "provenance_ids": list(self.provenance_ids),
            "limitations": list(self.limitations),
            "reviewers": [dict(row) for row in self.reviewers],
        }


@dataclass(frozen=True, slots=True)
class PromotionCheck:
    passed: bool
    checks: dict[str, bool]
    evidence: PromotionEvidence
    candidate_profile_hash: str
    baseline_profile_hash: str

    def to_json(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "evidence": self.evidence.to_json(),
            "candidate_profile_hash": self.candidate_profile_hash,
            "baseline_profile_hash": self.baseline_profile_hash,
        }


def check_promotion(
    candidate: str | Path | CalibrationProfile,
    baseline: str | Path | CalibrationProfile,
    evidence: str | Path | Mapping[str, Any] | PromotionEvidence,
    *,
    criteria: PromotionCriteria | None = None,
) -> PromotionCheck:
    selected = criteria or PromotionCriteria()
    selected.validate()
    candidate_profile = _profile(candidate)
    baseline_profile = _profile(baseline)
    selected_evidence = _evidence(evidence)
    if selected_evidence.candidate_profile_hash != candidate_profile.profile_hash:
        raise PromotionError("Evidence is bound to a different candidate profile hash")
    if selected_evidence.baseline_profile_hash != baseline_profile.profile_hash:
        raise PromotionError("Evidence is bound to a different baseline profile hash")
    approvals = _approved_reviewers(selected_evidence.reviewers)
    checks = {
        "immutable_candidate": bool(candidate_profile.profile_hash),
        "sign_agreement": selected_evidence.sign_agreement
        >= selected.min_sign_agreement,
        "no_duplication": (not selected.require_no_duplication)
        or selected_evidence.duplicate_pathways == 0,
        "held_out_improvement": selected_evidence.held_out_improvement
        >= selected.min_held_out_improvement,
        "held_out_brier_improvement": selected_evidence.held_out_brier_improvement
        >= selected.min_held_out_brier_improvement,
        "candidate_decision": candidate_profile.promotion_decisions.get(
            "curve", "change"
        )
        == "change",
        "material_recommendation_impact": selected_evidence.material_recommendation_fraction
        <= selected.max_material_recommendation_fraction,
        "reviewer_approval": len(approvals) >= selected.required_approvals,
        "review_evidence": bool(
            selected_evidence.intervals
            and selected_evidence.calibration_cards
            and selected_evidence.scenario_diff
            and selected_evidence.provenance_ids
            and selected_evidence.limitations
        ),
    }
    return PromotionCheck(
        passed=all(checks.values()),
        checks=checks,
        evidence=selected_evidence,
        candidate_profile_hash=candidate_profile.profile_hash,
        baseline_profile_hash=baseline_profile.profile_hash,
    )


class PromotionStore:
    """Append-only promotion history with immutable profile versions."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"

    def promote(
        self,
        candidate: str | Path | CalibrationProfile,
        baseline: str | Path | CalibrationProfile,
        evidence: str | Path | Mapping[str, Any] | PromotionEvidence,
        *,
        application_directory: str | Path | None = None,
        criteria: PromotionCriteria | None = None,
    ) -> dict[str, Any]:
        profile = _profile(candidate)
        baseline_profile = _profile(baseline)
        check = check_promotion(profile, baseline_profile, evidence, criteria=criteria)
        if not check.passed:
            raise PromotionError(f"Candidate promotion checks failed: {check.checks}")
        baseline_profile.write_immutable(self.root / "profiles")
        profile_path = profile.write_immutable(self.root / "profiles")
        generated: list[str] = []
        if application_directory is not None:
            generated = [
                str(path)
                for path in write_application_artifacts(profile, application_directory)
            ]
        index = self._read_index()
        event = {
            "event": "promote",
            "profile_hash": profile.profile_hash,
            "profile_path": str(profile_path),
            "baseline_profile_hash": check.baseline_profile_hash,
            "evidence": check.evidence.to_json(),
            "generated_application_artifacts": generated,
        }
        index["active_profile_hash"] = profile.profile_hash
        index["active_profile_path"] = str(profile_path)
        index["history"].append(event)
        self._write_index(index)
        return {"check": check.to_json(), "event": event, "index": index}

    def rollback(self, profile_hash: str) -> dict[str, Any]:
        profile_path = next(
            self.root.glob(f"profiles/*/{profile_hash}/profile.json"), None
        )
        if profile_path is None:
            raise PromotionError(f"No immutable profile exists for hash {profile_hash}")
        profile = CalibrationProfile.load(profile_path)
        index = self._read_index()
        event = {
            "event": "rollback",
            "profile_hash": profile.profile_hash,
            "profile_path": str(profile_path),
            "previous_active_profile_hash": index.get("active_profile_hash"),
        }
        index["active_profile_hash"] = profile.profile_hash
        index["active_profile_path"] = str(profile_path)
        index["history"].append(event)
        self._write_index(index)
        return {"event": event, "index": index}

    def history(self) -> dict[str, Any]:
        return self._read_index()

    def _read_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {
                "schema_version": 1,
                "active_profile_hash": None,
                "active_profile_path": None,
                "history": [],
            }
        value = json.loads(self.index_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("history"), list):
            raise PromotionError("Promotion index is malformed")
        return value

    def _write_index(self, value: dict[str, Any]) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.root, prefix=".index.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, sort_keys=True, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.index_path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)


def _profile(value: str | Path | CalibrationProfile) -> CalibrationProfile:
    if isinstance(value, CalibrationProfile):
        return value
    try:
        return CalibrationProfile.load(value)
    except (OSError, ProfileError, ValueError, KeyError, TypeError) as error:
        raise PromotionError(f"Invalid profile: {value}") from error


def _evidence(
    value: str | Path | Mapping[str, Any] | PromotionEvidence,
) -> PromotionEvidence:
    if isinstance(value, PromotionEvidence):
        return value
    if isinstance(value, (str, Path)):
        return PromotionEvidence.load(value)
    return PromotionEvidence.from_json(value)


def _approved_reviewers(reviewers: tuple[dict[str, str], ...]) -> set[str]:
    return {
        row.get("id", row.get("reviewer", ""))
        for row in reviewers
        if row.get("decision", "").casefold() in {"approve", "approved", "pass"}
    } - {""}
