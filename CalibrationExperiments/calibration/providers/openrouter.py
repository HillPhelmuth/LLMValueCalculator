from __future__ import annotations

import json
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from openai import AsyncOpenAI

from calibration.config import CalibrationSettings
from calibration.models import ProviderRequest, ProviderResponse
from calibration.providers.base import ModelProvider
from calibration.providers.cost import calculate_catalog_cost, reconcile_cost
from calibration.providers.openrouter_catalog import CatalogSnapshot
from calibration.providers.routing import normalize_router_metadata


class OpenRouterProvider(ModelProvider):
    """OpenAI SDK adapter for deterministic OpenRouter batch inference."""

    name = "openrouter"
    max_concurrency = 4

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: AsyncOpenAI | None = None,
        http_referer: str | None = None,
        title: str | None = None,
        requests_per_minute: float | None = None,
        max_concurrency: int = 4,
        catalog: CatalogSnapshot | None = None,
    ) -> None:
        if client is None and not api_key:
            raise ValueError("OpenRouterProvider requires an API key")
        self.client = client or AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        self.extra_headers = {
            key: value
            for key, value in {
                "HTTP-Referer": http_referer,
                "X-OpenRouter-Title": title,
            }.items()
            if value
        }
        self.requests_per_minute = requests_per_minute
        self.max_concurrency = max_concurrency
        self.catalog = catalog
        self._serialized_requests: dict[str, str] = {}

    @classmethod
    def from_settings(
        cls,
        settings: CalibrationSettings | None = None,
        *,
        catalog: CatalogSnapshot | None = None,
    ) -> "OpenRouterProvider":
        config = settings or CalibrationSettings.from_environment()
        return cls(
            api_key=config.require_openrouter(),
            http_referer=config.openrouter_http_referer,
            title=config.openrouter_title,
            catalog=catalog,
        )

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        serialized_request = serialize_openrouter_request(request)
        self._serialized_requests[request.request_hash] = serialized_request
        payload = json.loads(serialized_request)
        started = time.perf_counter()
        try:
            response = await self.client.chat.completions.create(
                **payload,
                extra_headers=self.extra_headers or None,
                stream=False,
            )
        except Exception:
            raise
        latency_ms = (time.perf_counter() - started) * 1000
        raw = _model_to_dict(response)
        choice = _first_choice(raw)
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        if not isinstance(message, dict):
            message = _object_to_dict(message)
        content = message.get("content")
        tool_calls = tuple(_object_to_dict(item) for item in message.get("tool_calls", ()))
        refusal_value = message.get("refusal")
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        usage = dict(usage)
        cached_tokens = _nested_int(usage, "prompt_tokens_details", "cached_tokens")
        reasoning_tokens = _nested_int(usage, "completion_tokens_details", "reasoning_tokens")
        reported_cost = _decimal_or_none(
            usage.get("cost", raw.get("cost"))
        )
        router_raw = raw.get("router_metadata") or raw.get("provider_metadata") or {}
        router = normalize_router_metadata(router_raw)
        resolved_model = raw.get("model") or router.get("resolved_model")
        resolved_provider = raw.get("provider") or router.get("resolved_provider")
        endpoint = raw.get("endpoint") or router.get("endpoint")
        calculated_cost = None
        cost_reconciliation: dict[str, Any] = {}
        if self.catalog is not None:
            try:
                catalog_model = self.catalog.model(
                    str(resolved_model or request.dated_model_version)
                )
            except KeyError:
                catalog_model = self.catalog.model(request.model_id)
            calculated_cost, breakdown = calculate_catalog_cost(catalog_model.pricing, usage)
            cost_reconciliation = reconcile_cost(reported_cost, calculated_cost)
            cost_reconciliation["breakdown"] = breakdown.to_json()
        return ProviderResponse(
            response_id=str(raw.get("id", "")),
            raw_response=raw,
            parsed_answer=content,
            finish_reason=str(choice.get("finish_reason", "unknown")),
            refusal=bool(refusal_value),
            input_tokens=_int_or_none(usage.get("prompt_tokens")),
            cached_tokens=cached_tokens,
            output_tokens=_int_or_none(usage.get("completion_tokens")),
            reasoning_tokens=reasoning_tokens,
            tool_calls=tool_calls,
            latency_ms=latency_ms,
            provider_cost=reported_cost,
            resolved_model=None if resolved_model is None else str(resolved_model),
            resolved_provider=None if resolved_provider is None else str(resolved_provider),
            endpoint=None if endpoint is None else str(endpoint),
            content=content,
            router_metadata=router,
            usage=usage,
            calculated_cost=calculated_cost,
            cost_reconciliation=cost_reconciliation,
        )

    def serialized_request_for(self, request_hash: str) -> str | None:
        return self._serialized_requests.get(request_hash)


def build_openrouter_request(request: ProviderRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": request.dated_model_version,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ],
        "temperature": request.temperature,
        "max_tokens": request.max_output_tokens,
    }
    if request.reasoning_effort is not None:
        payload["reasoning_effort"] = request.reasoning_effort
    if request.tools:
        payload["tools"] = list(request.tools)
    if request.tool_choice is not None:
        payload["tool_choice"] = request.tool_choice
    if request.response_format is not None:
        payload["response_format"] = request.response_format
    if request.provider_routing:
        payload["extra_body"] = {"provider": request.provider_routing}
    return payload


def serialize_openrouter_request(request: ProviderRequest) -> str:
    return json.dumps(
        build_openrouter_request(request),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _model_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dict(dumped)
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    return dict(value)


def _object_to_dict(value: Any) -> dict[str, Any]:
    try:
        return _model_to_dict(value)
    except (TypeError, ValueError):
        return {}


def _first_choice(raw: dict[str, Any]) -> dict[str, Any]:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        return {}
    choice = choices[0]
    return choice if isinstance(choice, dict) else _object_to_dict(choice)


def _nested_int(value: dict[str, Any], parent: str, child: str) -> int | None:
    nested = value.get(parent)
    return _int_or_none(nested.get(child)) if isinstance(nested, dict) else None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None
