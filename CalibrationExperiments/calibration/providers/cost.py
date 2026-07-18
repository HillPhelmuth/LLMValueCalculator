from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    prompt: Decimal
    cached: Decimal
    completion: Decimal
    reasoning: Decimal
    image: Decimal
    web_search: Decimal
    request: Decimal
    missing_fields: tuple[str, ...] = ()

    @property
    def total(self) -> Decimal:
        return sum(
            (
                self.prompt,
                self.cached,
                self.completion,
                self.reasoning,
                self.image,
                self.web_search,
                self.request,
            ),
            Decimal("0"),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "prompt": format(self.prompt, "f"),
            "cached": format(self.cached, "f"),
            "completion": format(self.completion, "f"),
            "reasoning": format(self.reasoning, "f"),
            "image": format(self.image, "f"),
            "web_search": format(self.web_search, "f"),
            "request": format(self.request, "f"),
            "total": format(self.total, "f"),
            "missing_fields": list(self.missing_fields),
        }


def calculate_catalog_cost(
    pricing: Mapping[str, Decimal | None],
    usage: Mapping[str, Any],
) -> tuple[Decimal | None, CostBreakdown]:
    """Calculate cost using Decimal and preserve missing accounting as missing."""
    prompt_tokens = _int(usage.get("prompt_tokens"))
    completion_tokens = _int(usage.get("completion_tokens"))
    cached_tokens = _nested_int(usage, "prompt_tokens_details", "cached_tokens")
    reasoning_tokens = _nested_int(usage, "completion_tokens_details", "reasoning_tokens")
    image_tokens = _int(usage.get("image_tokens"))
    web_search_queries = _int(usage.get("web_search_queries"))
    request_count = _int(usage.get("request_count"))
    missing_fields: list[str] = []
    for field_name, value in (
        ("prompt_tokens", prompt_tokens),
        ("completion_tokens", completion_tokens),
    ):
        if value is None and pricing.get("prompt" if field_name == "prompt_tokens" else "completion") is not None:
            missing_fields.append(field_name)
    if cached_tokens is None and pricing.get("input_cache_read", pricing.get("cache_read")) is not None:
        missing_fields.append("cached_tokens")
    if reasoning_tokens is None and pricing.get("internal_reasoning", pricing.get("reasoning")) is not None:
        missing_fields.append("reasoning_tokens")
    values = {
        "prompt": _charge(pricing.get("prompt"), max(0, (prompt_tokens or 0) - (cached_tokens or 0))),
        "cached": _charge(
            pricing.get("input_cache_read", pricing.get("cache_read")), cached_tokens
        ),
        "completion": _charge(pricing.get("completion"), completion_tokens),
        "reasoning": _charge(
            pricing.get("internal_reasoning", pricing.get("reasoning")), reasoning_tokens
        ),
        "image": _charge(pricing.get("image"), image_tokens),
        "web_search": _charge(pricing.get("web_search"), web_search_queries),
        "request": _charge(pricing.get("request"), request_count or 1),
        "missing_fields": tuple(missing_fields),
    }
    breakdown = CostBreakdown(**values)
    if not any(value is not None for value in pricing.values()):
        return None, breakdown
    return breakdown.total, breakdown


def reconcile_cost(
    reported: Decimal | None,
    calculated: Decimal | None,
    *,
    tolerance: Decimal = Decimal("0.000001"),
) -> dict[str, Any]:
    if reported is None:
        return {"status": "reported_missing", "reported": None, "calculated": _decimal(calculated)}
    if calculated is None:
        return {"status": "catalog_pricing_missing", "reported": _decimal(reported), "calculated": None}
    difference = reported - calculated
    return {
        "status": "matched" if abs(difference) <= tolerance else "mismatch",
        "reported": _decimal(reported),
        "calculated": _decimal(calculated),
        "difference": _decimal(difference),
        "tolerance": _decimal(tolerance),
    }


def _charge(rate: Decimal | None, units: int | None) -> Decimal:
    if rate is None or units is None:
        return Decimal("0")
    return rate * Decimal(units)


def _nested_int(value: Mapping[str, Any], parent: str, child: str) -> int | None:
    nested = value.get(parent)
    return _int(nested.get(child)) if isinstance(nested, Mapping) else None


def _int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")
