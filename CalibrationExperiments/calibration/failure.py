from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable

from calibration.models import ScoreResult


class FailureClass(StrEnum):
    PROVIDER = "provider"
    TRUNCATION = "truncation"
    REFUSAL = "refusal"
    PARSE = "parse"
    SCHEMA = "schema"
    SEMANTIC = "semantic"
    GROUNDING = "grounding"
    TOOL = "tool"
    POLICY = "policy"
    STATE = "state"
    INFRASTRUCTURE = "infrastructure"


class OutcomeClass(StrEnum):
    SUCCESS = "success"
    GOOD = "good"
    ACCEPTABLE = "acceptable"
    BENIGN_FAILURE = "benign_failure"
    CRITICAL_FAILURE = "critical_failure"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


@dataclass(frozen=True, slots=True)
class CriticalityPolicy:
    dataset_id: str
    task_family: str
    critical_fields: tuple[str, ...] = ()
    precedence: tuple[FailureClass, ...] = (
        FailureClass.INFRASTRUCTURE,
        FailureClass.POLICY,
        FailureClass.PROVIDER,
        FailureClass.TRUNCATION,
        FailureClass.REFUSAL,
        FailureClass.PARSE,
        FailureClass.SCHEMA,
        FailureClass.TOOL,
        FailureClass.STATE,
        FailureClass.GROUNDING,
        FailureClass.SEMANTIC,
    )

    def validate(self) -> None:
        if not self.dataset_id or not self.task_family:
            raise ValueError("Criticality policies require dataset and task family")
        if len(self.precedence) != len(set(self.precedence)):
            raise ValueError("Failure precedence entries must be unique")


@dataclass(frozen=True, slots=True)
class OutcomeAssessment:
    outcome: OutcomeClass
    failure_class: FailureClass | None
    critical: bool
    detailed_scores: tuple[ScoreResult, ...]
    validator_metadata: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "outcome": self.outcome.value,
            "failure_class": None if self.failure_class is None else self.failure_class.value,
            "critical": self.critical,
            "detailed_scores": [
                {
                    "scorer_name": score.scorer_name,
                    "scorer_version": score.scorer_version,
                    "success": score.success,
                    "semantic_score": score.semantic_score,
                    "failure_class": score.failure_class,
                    "metrics": score.metrics,
                }
                for score in self.detailed_scores
            ],
            "validator_metadata": self.validator_metadata,
        }


def assess_outcome(
    scores: Iterable[ScoreResult],
    policy: CriticalityPolicy,
    *,
    provider_failure: FailureClass | None = None,
    validator_metadata: dict[str, object] | None = None,
) -> OutcomeAssessment:
    policy.validate()
    detailed = tuple(scores)
    failures = set(
        FailureClass(score.failure_class)
        for score in detailed
        if score.failure_class in {item.value for item in FailureClass}
    )
    if provider_failure:
        failures.add(provider_failure)
    failure = next((item for item in policy.precedence if item in failures), None)
    critical = bool(failure in {FailureClass.POLICY, FailureClass.INFRASTRUCTURE, FailureClass.PROVIDER})
    if any(score.critical for score in detailed):
        critical = True
    if failure is None and all(score.success is True for score in detailed if score.success is not None):
        outcome = OutcomeClass.SUCCESS
    elif failure in {FailureClass.INFRASTRUCTURE, FailureClass.PROVIDER}:
        outcome = OutcomeClass.INFRASTRUCTURE_FAILURE
    elif critical:
        outcome = OutcomeClass.CRITICAL_FAILURE
    elif failure is None:
        outcome = OutcomeClass.ACCEPTABLE
    elif any(score.semantic_score is not None and score.semantic_score >= 0.8 for score in detailed):
        outcome = OutcomeClass.GOOD
    else:
        outcome = OutcomeClass.BENIGN_FAILURE
    return OutcomeAssessment(
        outcome=outcome,
        failure_class=failure,
        critical=critical,
        detailed_scores=detailed,
        validator_metadata=validator_metadata or {},
    )


def validate_assessment(assessment: OutcomeAssessment) -> None:
    """Ensure the persisted final label is unambiguous while metrics remain detailed."""
    if assessment.critical and assessment.outcome not in {
        OutcomeClass.CRITICAL_FAILURE,
        OutcomeClass.INFRASTRUCTURE_FAILURE,
    }:
        raise ValueError("Critical assessment must have a critical or infrastructure outcome")
    if assessment.outcome == OutcomeClass.SUCCESS and assessment.failure_class is not None:
        raise ValueError("Successful assessment cannot carry a failure class")
