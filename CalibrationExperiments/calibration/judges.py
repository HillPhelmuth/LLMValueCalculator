from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Iterable

from calibration.models import Message, ProviderRequest


class JudgeValidationError(ValueError):
    """Raised when a model-based judge is not safe to use."""


@dataclass(frozen=True, slots=True)
class JudgeConfig:
    prompt_id: str
    prompt_version: str
    model_id: str
    model_version: str
    repeats: int = 1
    sensitivity_threshold: float = 0.9
    specificity_threshold: float = 0.9
    max_calibration_error: float = 0.1
    subgroup_tolerance: float = 0.1

    def validate(self) -> None:
        if self.repeats < 1:
            raise JudgeValidationError("Judge repeats must be positive")
        if not 0 <= self.sensitivity_threshold <= 1 or not 0 <= self.specificity_threshold <= 1:
            raise JudgeValidationError("Judge thresholds must be probabilities")


@dataclass(frozen=True, slots=True)
class JudgeValidationReport:
    config: JudgeConfig
    sample_count: int
    sensitivity: float
    specificity: float
    calibration_error: float
    subgroup_errors: dict[str, float]
    passed: bool
    judge_lock_hash: str
    diagnostics: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "config": asdict(self.config),
            "sample_count": self.sample_count,
            "sensitivity": self.sensitivity,
            "specificity": self.specificity,
            "calibration_error": self.calibration_error,
            "subgroup_errors": self.subgroup_errors,
            "passed": self.passed,
            "judge_lock_hash": self.judge_lock_hash,
            "diagnostics": self.diagnostics,
        }


def validate_judge(
    config: JudgeConfig,
    human_labels: Iterable[bool],
    judge_labels: Iterable[bool],
    *,
    judge_probabilities: Iterable[float] | None = None,
    subgroups: Iterable[str] | None = None,
) -> JudgeValidationReport:
    config.validate()
    human = tuple(bool(value) for value in human_labels)
    judged = tuple(bool(value) for value in judge_labels)
    if len(human) != len(judged) or not human:
        raise JudgeValidationError("Human and judge labels must be non-empty and aligned")
    probabilities = tuple(judge_probabilities or (float(value) for value in judged))
    if len(probabilities) != len(human):
        raise JudgeValidationError("Judge probabilities must align with labels")
    positives = sum(human)
    negatives = len(human) - positives
    true_positive = sum(expected and actual for expected, actual in zip(human, judged))
    true_negative = sum(not expected and not actual for expected, actual in zip(human, judged))
    sensitivity = true_positive / positives if positives else 1.0
    specificity = true_negative / negatives if negatives else 1.0
    calibration_error = _calibration_error(human, probabilities)
    subgroup_errors = _subgroup_errors(human, judged, tuple(subgroups or ()))
    passed = (
        sensitivity >= config.sensitivity_threshold
        and specificity >= config.specificity_threshold
        and calibration_error <= config.max_calibration_error
        and all(error <= config.subgroup_tolerance for error in subgroup_errors.values())
    )
    return JudgeValidationReport(
        config=config,
        sample_count=len(human),
        sensitivity=sensitivity,
        specificity=specificity,
        calibration_error=calibration_error,
        subgroup_errors=subgroup_errors,
        passed=passed,
        judge_lock_hash=_judge_lock_hash(config),
        diagnostics={"true_positive": true_positive, "true_negative": true_negative},
    )


def require_validated_judge(report: JudgeValidationReport) -> None:
    if not report.passed:
        raise JudgeValidationError("Judge validation thresholds were not met")


def judge_uncertainty_interval(
    observed_probability: float,
    *,
    sensitivity: float,
    specificity: float,
) -> tuple[float, float]:
    """Conservatively propagate judge error into a bounded interval."""
    observed_probability = min(1.0, max(0.0, observed_probability))
    denominator = sensitivity + specificity - 1
    if denominator <= 0:
        return 0.0, 1.0
    corrected = (observed_probability + specificity - 1) / denominator
    margin = (1 - min(sensitivity, specificity)) * 0.5
    return max(0.0, corrected - margin), min(1.0, corrected + margin)


def build_judge_request(
    config: JudgeConfig,
    messages: tuple[Message, ...],
    *,
    repeat_index: int,
) -> ProviderRequest:
    config.validate()
    return ProviderRequest(
        case_id="judge-validation",
        model_id=config.model_id,
        dated_model_version=config.model_version,
        provider="openrouter",
        messages=messages,
        temperature=0.0,
        max_output_tokens=128,
        reasoning_effort=None,
        condition_id="judge",
        prompt_version=config.prompt_version,
        repeat_index=repeat_index,
        response_format={"type": "json_object"},
    )


def assert_intelligence_curve_safe(scorer_names: Iterable[str]) -> None:
    names = set(scorer_names)
    deterministic = names & {
        "answer_exact_match",
        "answer_token_f1",
        "classification_accuracy",
        "schema_validity",
        "semantic_structured_value",
    }
    if not deterministic:
        raise JudgeValidationError(
            "An intelligence-curve fit requires at least one deterministic scorer"
        )


def _judge_lock_hash(config: JudgeConfig) -> str:
    return hashlib.sha256(
        json.dumps(asdict(config), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _calibration_error(labels: tuple[bool, ...], probabilities: tuple[float, ...]) -> float:
    bins: dict[int, list[tuple[bool, float]]] = {}
    for label, probability in zip(labels, probabilities):
        bucket = min(9, max(0, int(probability * 10)))
        bins.setdefault(bucket, []).append((label, probability))
    return sum(
        len(values) / len(labels)
        * abs(sum(label for label, _ in values) / len(values) - sum(probability for _, probability in values) / len(values))
        for values in bins.values()
    )


def _subgroup_errors(
    labels: tuple[bool, ...], judged: tuple[bool, ...], subgroups: tuple[str, ...]
) -> dict[str, float]:
    if not subgroups:
        return {}
    if len(subgroups) != len(labels):
        raise JudgeValidationError("Subgroup labels must align with validation rows")
    result: dict[str, float] = {}
    for subgroup in sorted(set(subgroups)):
        rows = [index for index, value in enumerate(subgroups) if value == subgroup]
        result[subgroup] = sum(labels[index] != judged[index] for index in rows) / len(rows)
    return result
