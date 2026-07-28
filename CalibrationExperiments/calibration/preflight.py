from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from calibration.config import CalibrationSettings
from calibration.datasets.base import DatasetAdapter
from calibration.manifest import ExperimentManifest
from calibration.models import CanonicalCase, ProviderRequest
from calibration.providers.compatibility import (
    CompatibilityError,
    estimate_request_tokens,
    validate_requests_against_catalog,
)
from calibration.providers.cost import calculate_catalog_cost
from calibration.providers.openrouter_catalog import CatalogSnapshot
from calibration.providers.routing import build_provider_policy
from calibration.schema import SCHEMA_VERSION, validate_record


@dataclass(frozen=True, slots=True)
class PreflightReport:
    status: str
    manifest_hash: str
    catalog_snapshot_hash: str
    estimated_requests: int
    estimated_tokens: int
    estimated_cost: Decimal
    checks: tuple[dict[str, Any], ...]
    canary: dict[str, Any]
    approval_required: bool = False
    schema_version: str = SCHEMA_VERSION

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    def to_json(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "status": self.status,
            "manifest_hash": self.manifest_hash,
            "catalog_snapshot_hash": self.catalog_snapshot_hash,
            "estimated_requests": self.estimated_requests,
            "estimated_tokens": self.estimated_tokens,
            "estimated_cost": format(self.estimated_cost, "f"),
            "checks": list(self.checks),
            "canary": self.canary,
            "approval_required": self.approval_required,
        }
        validate_record("preflight_report", value)
        return value

    def write(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_json(), sort_keys=True, indent=2), encoding="utf-8"
        )

    def require_pass(self) -> None:
        if not self.passed:
            failures = [
                check.get("message", "check failed")
                for check in self.checks
                if not check.get("passed")
            ]
            raise PreflightError(
                "Preflight failed: " + "; ".join(str(item) for item in failures)
            )


class PreflightError(RuntimeError):
    pass


async def run_preflight(
    manifest: ExperimentManifest,
    dataset: DatasetAdapter,
    catalog: CatalogSnapshot,
    *,
    settings: CalibrationSettings | None = None,
    canary_provider: Any | None = None,
    run_canary: bool = False,
    approval_artifact: str | Path | None = None,
) -> PreflightReport:
    checks: list[dict[str, Any]] = []
    if settings is not None:
        try:
            settings.require_openrouter()
            checks.append(
                {
                    "name": "credentials",
                    "passed": True,
                    "message": "API key is configured",
                }
            )
        except Exception as error:
            checks.append(
                {"name": "credentials", "passed": False, "message": str(error)}
            )

    dataset.prepare()
    cases = list(dataset.cases(manifest.dataset.split))
    if not cases:
        checks.append(
            {"name": "dataset", "passed": False, "message": "dataset produced no cases"}
        )
    else:
        checks.append(
            {
                "name": "dataset",
                "passed": True,
                "message": f"validated {len(cases)} cases",
            }
        )

    requests = tuple(
        _request(manifest, dataset, case, condition, model)
        for model in manifest.models
        for case in cases
        for condition in manifest.conditions
    )
    compatibility_results = ()
    try:
        compatibility_results = validate_requests_against_catalog(requests, catalog)
        checks.append(
            {
                "name": "catalog_compatibility",
                "passed": True,
                "message": f"validated {len(compatibility_results)} request cells",
            }
        )
    except CompatibilityError as error:
        checks.append(
            {"name": "catalog_compatibility", "passed": False, "message": str(error)}
        )

    repeats = manifest.generation.repeats
    transport_contingency = 1 + manifest.retries.transport_retries
    estimated_requests = len(requests) * repeats * transport_contingency
    estimated_tokens = (
        sum(
            estimate_request_tokens(request) + request.max_output_tokens
            for request in requests
        )
        * repeats
        * transport_contingency
    )
    estimated_cost = Decimal("0")
    for request, result in zip(requests, compatibility_results):
        try:
            model = catalog.model(request.model_id)
            usage = {
                "prompt_tokens": result.input_tokens,
                "completion_tokens": request.max_output_tokens,
                "request_count": 1,
            }
            calculated, _ = calculate_catalog_cost(model.pricing, usage)
            if calculated is not None:
                estimated_cost += calculated * repeats * transport_contingency
        except KeyError:
            pass
    budget = manifest.budgets.max_usd
    over_budget = budget is not None and estimated_cost > Decimal(str(budget))
    over_requests = (
        manifest.budgets.max_requests is not None
        and estimated_requests > manifest.budgets.max_requests
    )
    over_tokens = (
        manifest.budgets.max_tokens is not None
        and estimated_tokens > manifest.budgets.max_tokens
    )
    if over_budget:
        approved = _approval_is_valid(
            approval_artifact or manifest.budgets.approval_artifact,
            manifest.manifest_hash,
            estimated_cost,
        )
        checks.append(
            {
                "name": "budget",
                "passed": approved,
                "message": "budget approval artifact accepted"
                if approved
                else "estimated spend exceeds budget and no valid approval artifact was found",
            }
        )
    else:
        checks.append(
            {
                "name": "budget",
                "passed": True,
                "message": "estimated spend is within budget",
            }
        )
    if over_requests:
        checks.append(
            {
                "name": "request_budget",
                "passed": False,
                "message": "estimated requests exceed the configured request budget",
            }
        )
    else:
        checks.append(
            {
                "name": "request_budget",
                "passed": True,
                "message": "estimated requests are within budget",
            }
        )
    if over_tokens:
        checks.append(
            {
                "name": "token_budget",
                "passed": False,
                "message": "estimated tokens exceed the configured token budget",
            }
        )
    else:
        checks.append(
            {
                "name": "token_budget",
                "passed": True,
                "message": "estimated tokens are within budget",
            }
        )

    canary: dict[str, Any] = {"requested": run_canary, "passed": not run_canary}
    if run_canary:
        if canary_provider is None or not requests:
            canary = {
                "requested": True,
                "passed": False,
                "message": "canary provider or request is unavailable",
            }
        else:
            first_by_model: dict[str, ProviderRequest] = {}
            for request in requests:
                first_by_model.setdefault(request.model_id, request)
            results: list[dict[str, Any]] = []
            for model_id, request in first_by_model.items():
                try:
                    canary_request = replace(
                        request, max_output_tokens=min(16, request.max_output_tokens)
                    )
                    response = await canary_provider.complete(canary_request)
                    results.append(
                        {
                            "model_id": model_id,
                            "passed": True,
                            "response_id": response.response_id,
                            "resolved_model": response.resolved_model,
                        }
                    )
                except Exception as error:
                    results.append(
                        {
                            "model_id": model_id,
                            "passed": False,
                            "message": f"{type(error).__name__}: {error}",
                        }
                    )
            canary = {
                "requested": True,
                "passed": all(result["passed"] for result in results),
                "models": results,
            }
        checks.append(
            {
                "name": "canary",
                "passed": bool(canary.get("passed")),
                "message": str(canary.get("message", "canary completed")),
            }
        )

    status = (
        "pass"
        if all(check["passed"] for check in checks) and canary.get("passed", True)
        else "fail"
    )
    return PreflightReport(
        status=status,
        manifest_hash=manifest.manifest_hash,
        catalog_snapshot_hash=catalog.snapshot_hash,
        estimated_requests=estimated_requests,
        estimated_tokens=estimated_tokens,
        estimated_cost=estimated_cost,
        checks=tuple(checks),
        canary=canary,
        approval_required=bool(over_budget),
    )


def _request(
    manifest: ExperimentManifest,
    dataset: DatasetAdapter,
    case: CanonicalCase,
    condition: str,
    model: Any,
) -> ProviderRequest:
    metadata = case.metadata
    return ProviderRequest(
        case_id=case.case_id,
        model_id=model.catalog_id,
        dated_model_version=model.provider_model,
        provider=model.provider,
        messages=dataset.render(case, condition),
        temperature=manifest.generation.temperature,
        max_output_tokens=manifest.generation.max_output_tokens,
        reasoning_effort=manifest.generation.reasoning_effort,
        condition_id=condition,
        prompt_version=manifest.prompt_version,
        repeat_index=0,
        tools=tuple(dict(tool) for tool in metadata.get("tools", ())),
        tool_choice=metadata.get("tool_choice"),
        response_format=metadata.get("response_format"),
        provider_routing=build_provider_policy(manifest.routing),
    )


def _approval_is_valid(
    path: str | Path | None, manifest_hash: str, estimated_cost: Decimal
) -> bool:
    if path is None or not Path(path).is_file():
        return False
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return (
            value.get("manifest_hash") == manifest_hash
            and bool(value.get("approved_by"))
            and Decimal(str(value.get("approved_limit_usd", "0"))) >= estimated_cost
        )
    except (OSError, ValueError, TypeError):
        return False
