from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from calibration.schema import SCHEMA_VERSION


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: Any


@dataclass(frozen=True, slots=True)
class CanonicalCase:
    case_id: str
    input: dict[str, Any]
    expected: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def label_available(self) -> bool:
        """Whether this case carries a public label usable by fitting code."""
        return bool(self.metadata.get("label_available", self.expected is not None))


@dataclass(frozen=True, slots=True)
class CaseFeatures:
    case_id: str
    dataset_id: str
    dataset_revision: str
    split: str
    category: str | None = None
    base_difficulty_stratum: str | None = None
    context_band: str | None = None
    reasoning_depth: str | None = None
    domain_band: str | None = None
    tool_horizon: str | None = None
    verifiability_band: str | None = None
    output_band: str | None = None
    criticality_band: str | None = None
    feature_json: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    case_id: str
    model_id: str
    dated_model_version: str
    provider: str
    messages: tuple[Message, ...]
    temperature: float
    max_output_tokens: int
    reasoning_effort: str | None
    condition_id: str
    prompt_version: str
    repeat_index: int
    schema_version: str = SCHEMA_VERSION
    tools: tuple[dict[str, Any], ...] = ()
    tool_choice: Any = None
    response_format: dict[str, Any] | None = None
    provider_routing: dict[str, Any] = field(default_factory=dict)
    experimental_strategy: str | None = None
    parent_request_hash: str | None = None
    experimental_feedback: dict[str, Any] = field(default_factory=dict)
    changed_inputs: dict[str, Any] = field(default_factory=dict)

    @property
    def canonical_json(self) -> str:
        return json.dumps(
            asdict(self),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @property
    def request_hash(self) -> str:
        return hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    response_id: str
    raw_response: dict[str, Any]
    parsed_answer: Any
    finish_reason: str
    refusal: bool = False
    input_tokens: int | None = None
    cached_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    tool_calls: tuple[dict[str, Any], ...] = ()
    latency_ms: float = 0
    provider_cost: Decimal | None = None
    created_utc: str = field(default_factory=utc_now_iso)
    schema_version: str = SCHEMA_VERSION
    resolved_model: str | None = None
    resolved_provider: str | None = None
    endpoint: str | None = None
    content: Any = None
    router_metadata: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    calculated_cost: Decimal | None = None
    cost_reconciliation: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("provider_cost", "calculated_cost"):
            amount = value[key]
            value[key] = None if amount is None else format(amount, "f")
        return value

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "ProviderResponse":
        copy = dict(value)
        copy["tool_calls"] = tuple(copy.get("tool_calls", ()))
        for key in ("provider_cost", "calculated_cost"):
            if copy.get(key) is not None:
                copy[key] = Decimal(str(copy[key]))
        return cls(**copy)


@dataclass(frozen=True, slots=True)
class ScoreResult:
    scorer_name: str
    scorer_version: str
    success: bool | None = None
    good: bool | None = None
    acceptable: bool | None = None
    critical: bool | None = None
    schema_valid: bool | None = None
    semantic_score: float | None = None
    grounded_score: float | None = None
    tool_state_score: float | None = None
    failure_class: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
