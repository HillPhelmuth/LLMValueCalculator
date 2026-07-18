from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable

from calibration.models import ProviderRequest


class ExperimentalRetryError(ValueError):
    """Raised when a retry strategy would lose experimental lineage."""


@dataclass(frozen=True, slots=True)
class RetryStrategy:
    strategy_id: str
    description: str
    changes_prompt: bool = False
    changes_evidence: bool = False
    changes_tool_state: bool = False
    requires_feedback: bool = False


RETRY_STRATEGIES = {
    "same_prompt": RetryStrategy("same_prompt", "resample the same prompt"),
    "repair_feedback": RetryStrategy("repair_feedback", "repair with versioned validator feedback", changes_prompt=True, requires_feedback=True),
    "changed_evidence_or_tool_state": RetryStrategy("changed_evidence_or_tool_state", "change evidence or tool state", changes_evidence=True, changes_tool_state=True),
}


@dataclass(frozen=True, slots=True)
class ExperimentalRetry:
    parent_attempt_id: str | None
    parent_request_hash: str
    strategy_id: str
    repeat_index: int
    feedback: dict[str, Any]
    changed_inputs: dict[str, Any]
    request_hash: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def schedule_experimental_retries(
    request: ProviderRequest,
    *,
    strategy_id: str,
    count: int = 5,
    parent_attempt_id: str | None = None,
    feedback: dict[str, Any] | None = None,
    changed_inputs: dict[str, Any] | None = None,
) -> tuple[tuple[ProviderRequest, ExperimentalRetry], ...]:
    if strategy_id not in RETRY_STRATEGIES:
        raise ExperimentalRetryError(f"Unknown retry strategy: {strategy_id}")
    if count < 1:
        raise ExperimentalRetryError("Experimental retry count must be positive")
    strategy = RETRY_STRATEGIES[strategy_id]
    feedback = dict(feedback or {})
    changed_inputs = dict(changed_inputs or {})
    if strategy.requires_feedback and not feedback:
        raise ExperimentalRetryError(f"Strategy {strategy_id} requires preserved feedback")
    if strategy.changes_evidence or strategy.changes_tool_state:
        if not changed_inputs:
            raise ExperimentalRetryError(f"Strategy {strategy_id} requires changed inputs")
    result: list[tuple[ProviderRequest, ExperimentalRetry]] = []
    for repeat_index in range(1, count + 1):
        request_copy = replace(
            request,
            repeat_index=repeat_index,
            experimental_strategy=strategy_id,
            parent_request_hash=request.request_hash,
            experimental_feedback=feedback,
            changed_inputs=changed_inputs,
        )
        retry = ExperimentalRetry(
            parent_attempt_id=parent_attempt_id,
            parent_request_hash=request.request_hash,
            strategy_id=strategy_id,
            repeat_index=repeat_index,
            feedback=feedback,
            changed_inputs=changed_inputs,
            request_hash=request_copy.request_hash,
        )
        if retry.request_hash == request.request_hash:
            raise ExperimentalRetryError("Experimental retry collapsed into the parent cache key")
        result.append((request_copy, retry))
    return tuple(result)


def validate_retry_lineage(retries: Iterable[ExperimentalRetry]) -> None:
    seen: set[str] = set()
    for retry in retries:
        if retry.request_hash in seen:
            raise ExperimentalRetryError("Duplicate experimental retry request hash")
        seen.add(retry.request_hash)
        if retry.repeat_index < 1 or not retry.parent_request_hash:
            raise ExperimentalRetryError("Retry lineage is incomplete")
