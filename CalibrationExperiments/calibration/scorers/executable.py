from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Callable

from calibration.models import CanonicalCase, ProviderResponse, ScoreResult
from calibration.scorers.base import Scorer


class ExecutionOutcome(StrEnum):
    PASSED = "passed"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMEOUT = "timeout"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    POLICY_VIOLATION = "policy_violation"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


@dataclass(frozen=True, slots=True)
class TestCaseResult:
    test_id: str
    passed: bool
    critical: bool = False
    duration_ms: float = 0.0
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    outcome: ExecutionOutcome
    tests: tuple[TestCaseResult, ...] = ()
    stdout: str = ""
    stderr: str = ""
    resource_usage: dict[str, Any] | None = None
    infrastructure_error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "tests": [asdict(test) for test in self.tests],
            "stdout": self.stdout,
            "stderr": self.stderr,
            "resource_usage": self.resource_usage or {},
            "infrastructure_error": self.infrastructure_error,
        }


Executor = Callable[[CanonicalCase, ProviderResponse], ExecutionReport]


class CodeExecutionScorer(Scorer):
    name = "code_execution"
    version = "1.0.0"

    def __init__(self, executor: Executor) -> None:
        self.executor = executor

    def score(self, case: CanonicalCase, response: ProviderResponse) -> ScoreResult:
        report = self.executor(case, response)
        return _report_to_score(self.name, self.version, report)


class ToolStateScorer(Scorer):
    name = "tool_state"
    version = "1.0.0"

    def score(self, case: CanonicalCase, response: ProviderResponse) -> ScoreResult:
        expected = case.metadata.get("expected_tool_state", {})
        actual = case.metadata.get("observed_tool_state", {})
        expected_calls = case.metadata.get("expected_tool_calls", ())
        actual_calls = tuple(response.tool_calls)
        calls_match = _canonical(expected_calls) == _canonical(actual_calls)
        state_match = _canonical(expected) == _canonical(actual)
        matched = calls_match and state_match
        return ScoreResult(
            scorer_name=self.name,
            scorer_version=self.version,
            success=matched,
            acceptable=matched,
            tool_state_score=float(matched),
            failure_class=None if matched else "tool_or_state_mismatch",
            metrics={
                "calls_match": calls_match,
                "state_match": state_match,
                "expected_calls": expected_calls,
                "actual_calls": list(actual_calls),
            },
        )


def _report_to_score(
    scorer_name: str, scorer_version: str, report: ExecutionReport
) -> ScoreResult:
    total = len(report.tests)
    passed = sum(test.passed for test in report.tests)
    critical_total = sum(test.critical for test in report.tests)
    critical_passed = sum(test.critical and test.passed for test in report.tests)
    fraction = passed / total if total else float(report.outcome == ExecutionOutcome.PASSED)
    critical_fraction = (
        critical_passed / critical_total if critical_total else fraction
    )
    failure = None if report.outcome == ExecutionOutcome.PASSED else report.outcome.value
    return ScoreResult(
        scorer_name=scorer_name,
        scorer_version=scorer_version,
        success=report.outcome == ExecutionOutcome.PASSED,
        acceptable=report.outcome in {ExecutionOutcome.PASSED, ExecutionOutcome.PARTIAL},
        critical=critical_fraction < 1.0,
        semantic_score=fraction,
        failure_class=failure,
        metrics={
            "tests": [asdict(test) for test in report.tests],
            "passed_fraction": fraction,
            "critical_passed_fraction": critical_fraction,
            "outcome": report.outcome.value,
            "resource_usage": report.resource_usage or {},
            "infrastructure_error": report.infrastructure_error,
        },
    )


def _canonical(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
