from __future__ import annotations

from typing import Any

from calibration.manifest import RoutingConfig


def build_provider_policy(config: RoutingConfig, *, fitted_run: bool = True) -> dict[str, Any]:
    """Translate the manifest routing lock to OpenRouter's provider object."""
    policy: dict[str, Any] = {
        "allow_fallbacks": False if fitted_run else config.allow_fallbacks,
        "require_parameters": config.require_parameters,
        "data_collection": config.data_collection,
        "zdr": config.zdr,
    }
    if config.provider_order:
        policy["order"] = list(config.provider_order)
    if config.endpoint:
        policy["only"] = [config.endpoint]
    if config.quantization:
        policy["quantizations"] = [config.quantization]
    return policy


def routing_manifest_fields(config: RoutingConfig) -> dict[str, Any]:
    """Return the complete canonical routing lock for resolved manifests."""
    return {
        "policy": config.policy,
        "provider_order": list(config.provider_order),
        "allow_fallbacks": config.allow_fallbacks,
        "require_parameters": config.require_parameters,
        "data_collection": config.data_collection,
        "zdr": config.zdr,
        "quantization": config.quantization,
        "endpoint": config.endpoint,
        "provider_object": build_provider_policy(config),
    }


def normalize_router_metadata(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {
        "resolved_model": raw.get("model") or raw.get("resolved_model"),
        "resolved_provider": raw.get("provider") or raw.get("resolved_provider"),
        "endpoint": raw.get("endpoint") or raw.get("provider_endpoint"),
        "fallback": bool(raw.get("fallback", False)),
        "raw": raw,
    }
