from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from calibration.models import ProviderRequest
from calibration.providers.openrouter_catalog import CatalogSnapshot, ModelCatalogEntry
from calibration.schema import SCHEMA_VERSION


class CompatibilityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CompatibilityResult:
    model_id: str
    compatible: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    input_tokens: int
    requested_output_tokens: int
    context_length: int | None
    max_completion_tokens: int | None
    supported_parameters: tuple[str, ...]
    schema_version: str = SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model_id": self.model_id,
            "compatible": self.compatible,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "input_tokens": self.input_tokens,
            "requested_output_tokens": self.requested_output_tokens,
            "context_length": self.context_length,
            "max_completion_tokens": self.max_completion_tokens,
            "supported_parameters": list(self.supported_parameters),
        }


def validate_request_compatibility(
    request: ProviderRequest,
    model: ModelCatalogEntry,
    *,
    allow_catalog_override: bool = False,
) -> CompatibilityResult:
    errors: list[str] = []
    warnings: list[str] = []
    input_tokens = estimate_request_tokens(request)
    max_completion = model.max_completion_tokens
    if model.context_length is None:
        warnings.append("catalog did not publish a context length")
    elif input_tokens + request.max_output_tokens > model.context_length:
        errors.append(
            f"requested context ({input_tokens} input + {request.max_output_tokens} output) exceeds model context length {model.context_length}"
        )
    if max_completion is not None and request.max_output_tokens > max_completion:
        errors.append(
            f"requested output {request.max_output_tokens} exceeds provider maximum {max_completion}"
        )
    parameters = set(model.supported_parameters)
    if parameters:
        if request.temperature is not None and "temperature" not in parameters:
            errors.append("temperature is not listed in supported_parameters")
        if request.reasoning_effort is not None and not parameters.intersection(
            {"reasoning", "reasoning_effort", "include_reasoning"}
        ):
            errors.append("reasoning settings are not supported")
        if request.tools and not parameters.intersection({"tools", "tool_choice"}):
            errors.append("tools are not supported")
        if request.tool_choice is not None and "tool_choice" not in parameters:
            errors.append("tool_choice is not supported")
        if request.response_format:
            response_type = str(request.response_format.get("type", ""))
            if response_type in {"json_schema", "json_object"} and not parameters.intersection(
                {"structured_outputs", "response_format", "json_schema"}
            ):
                errors.append("structured response formats are not supported")
    if request.provider_routing.get("require_parameters"):
        required = set(request.provider_routing.get("required_parameters", ()))
        missing = sorted(required - parameters)
        if missing:
            errors.append(f"routing requires unsupported parameters: {', '.join(missing)}")
    if allow_catalog_override and errors:
        warnings.extend(errors)
        errors = []
    return CompatibilityResult(
        model_id=model.id,
        compatible=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        input_tokens=input_tokens,
        requested_output_tokens=request.max_output_tokens,
        context_length=model.context_length,
        max_completion_tokens=max_completion,
        supported_parameters=model.supported_parameters,
    )


def validate_requests_against_catalog(
    requests: Iterable[ProviderRequest], catalog: CatalogSnapshot, *, allow_catalog_override: bool = False
) -> tuple[CompatibilityResult, ...]:
    results: list[CompatibilityResult] = []
    for request in requests:
        try:
            model = catalog.model(request.dated_model_version)
        except KeyError:
            try:
                model = catalog.model(request.model_id)
            except KeyError as error:
                raise CompatibilityError(
                    f"Model {request.model_id} ({request.dated_model_version}) is absent from the locked catalog snapshot"
                ) from error
        result = validate_request_compatibility(
            request, model, allow_catalog_override=allow_catalog_override
        )
        results.append(result)
        if not result.compatible:
            raise CompatibilityError(
                f"Model {result.model_id} is incompatible: {'; '.join(result.errors)}"
            )
    return tuple(results)


def compatibility_hash(results: Iterable[CompatibilityResult]) -> str:
    value = [result.to_json() for result in results]
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def estimate_request_tokens(request: ProviderRequest) -> int:
    total = 0
    for message in request.messages:
        total += _count_content_tokens(message.content) + 4
    for tool in request.tools:
        total += _count_content_tokens(tool) + 8
    if request.response_format:
        total += _count_content_tokens(request.response_format) + 4
    return total


def _count_content_tokens(content: Any) -> int:
    if isinstance(content, str):
        return max(1, len(content.split()))
    if isinstance(content, dict):
        return max(1, len(json.dumps(content, sort_keys=True, ensure_ascii=False).split()))
    if isinstance(content, (list, tuple)):
        return sum(_count_content_tokens(item) for item in content)
    return max(1, len(str(content).split()))
