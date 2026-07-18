from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from calibration.schema import SCHEMA_VERSION


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str


@dataclass(frozen=True, slots=True)
class CanonicalCase:
    case_id: str
    input: dict[str, Any]
    expected: Any
    metadata: dict[str, Any] = field(default_factory=dict)


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
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    tool_calls: tuple[dict[str, Any], ...] = ()
    latency_ms: float = 0
    provider_cost: float = 0
    created_utc: str = field(default_factory=utc_now_iso)
    schema_version: str = SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "ProviderResponse":
        copy = dict(value)
        copy["tool_calls"] = tuple(copy.get("tool_calls", ()))
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
