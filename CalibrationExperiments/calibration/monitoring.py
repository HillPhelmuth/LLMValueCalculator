from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from calibration.manifest import ExperimentManifest
from calibration.models import ProviderRequest, ProviderResponse
from calibration.providers.cost import calculate_catalog_cost
from calibration.providers.openrouter_catalog import CatalogSnapshot
from calibration.providers.retry import BudgetExceeded
from calibration.storage.sqlite import SqliteRunStore


class MonitoringError(RuntimeError):
    """Raised when a run cannot satisfy an operational monitoring gate."""


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    run_usd: float | None
    experiment_usd: float | None
    model_usd: float | None
    daily_usd: float | None
    max_requests: int | None
    max_tokens: int | None
    warning_fraction: float = 0.8

    @classmethod
    def from_manifest(
        cls, manifest: ExperimentManifest, environ: Mapping[str, str] | None = None
    ) -> "BudgetLimits":
        values = os.environ if environ is None else environ
        run = _optional_float(values.get("CALIBRATION_RUN_MAX_USD"))
        if run is None:
            run = manifest.budgets.max_usd
        experiment = _optional_float(values.get("CALIBRATION_EXPERIMENT_MAX_USD"))
        model = _optional_float(values.get("CALIBRATION_MODEL_MAX_USD"))
        daily = _optional_float(values.get("CALIBRATION_DAILY_MAX_USD"))
        return cls(
            run_usd=run,
            experiment_usd=experiment if experiment is not None else run,
            model_usd=model if model is not None else experiment if experiment is not None else run,
            daily_usd=daily if daily is not None else run,
            max_requests=manifest.budgets.max_requests,
            max_tokens=manifest.budgets.max_tokens,
        )

    def validate(self) -> None:
        for value in (self.run_usd, self.experiment_usd, self.model_usd, self.daily_usd):
            if value is not None and value < 0:
                raise MonitoringError("Budget ceilings cannot be negative")
        if self.max_requests is not None and self.max_requests < 1:
            raise MonitoringError("Request ceiling must be positive")
        if self.max_tokens is not None and self.max_tokens < 1:
            raise MonitoringError("Token ceiling must be positive")
        if not 0 < self.warning_fraction <= 1:
            raise MonitoringError("Budget warning fraction must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    event_id: str
    run_id: str
    experiment_id: str
    model_id: str
    request_hash: str
    estimated_usd: float
    token_count: int


class BudgetLedger:
    """Persisted, concurrency-safe request and spend reservation layer."""

    def __init__(self, limits: BudgetLimits) -> None:
        limits.validate()
        self.limits = limits

    def reserve(
        self,
        store: SqliteRunStore,
        run_id: str,
        manifest: ExperimentManifest,
        request: ProviderRequest,
        *,
        estimated_usd: float,
        token_count: int,
    ) -> BudgetReservation:
        day = datetime.now(timezone.utc).date().isoformat()
        checks = (
            ("run", store.budget_totals(run_id=run_id), self.limits.run_usd),
            (
                "experiment",
                store.budget_totals(run_id="", experiment_id=manifest.experiment_id),
                self.limits.experiment_usd,
            ),
            (
                "model",
                store.budget_totals(
                    run_id="",
                    experiment_id=manifest.experiment_id,
                    model_id=request.model_id,
                ),
                self.limits.model_usd,
            ),
            ("daily", store.budget_totals(run_id="", day=day), self.limits.daily_usd),
        )
        for scope, totals, limit in checks:
            if limit is not None and totals["usd"] + estimated_usd > limit:
                raise BudgetExceeded(f"{scope} spend ceiling would be exceeded")
        run_totals = checks[0][1]
        if self.limits.max_requests is not None and run_totals["requests"] + 1 > self.limits.max_requests:
            raise BudgetExceeded("request budget exceeded")
        if self.limits.max_tokens is not None and run_totals["tokens"] + token_count > self.limits.max_tokens:
            raise BudgetExceeded("token budget exceeded")
        event_id = store.record_budget_event(
            run_id=run_id,
            experiment_id=manifest.experiment_id,
            model_id=request.model_id,
            provider=request.provider,
            request_hash=request.request_hash,
            amount_usd=estimated_usd,
            estimated_usd=estimated_usd,
            token_count=token_count,
        )
        return BudgetReservation(
            event_id,
            run_id,
            manifest.experiment_id,
            request.model_id,
            request.request_hash,
            estimated_usd,
            token_count,
        )

    def settle(
        self,
        store: SqliteRunStore,
        reservation: BudgetReservation,
        response: ProviderResponse | None,
    ) -> float:
        actual = response.provider_cost if response and response.provider_cost is not None else None
        if actual is None and response and response.calculated_cost is not None:
            actual = response.calculated_cost
        actual_usd = 0.0 if actual is None else float(actual)
        day = datetime.now(timezone.utc).date().isoformat()
        run_total = store.budget_totals(run_id=reservation.run_id, exclude_event_id=reservation.event_id)
        experiment_total = store.budget_totals(
            run_id="",
            experiment_id=reservation.experiment_id,
            exclude_event_id=reservation.event_id,
        )
        model_total = store.budget_totals(
            run_id="",
            experiment_id=reservation.experiment_id,
            model_id=reservation.model_id,
            exclude_event_id=reservation.event_id,
        )
        daily_total = store.budget_totals(run_id="", day=day, exclude_event_id=reservation.event_id)
        for scope, total, limit in (
            ("run", run_total, self.limits.run_usd),
            ("experiment", experiment_total, self.limits.experiment_usd),
            ("model", model_total, self.limits.model_usd),
            ("daily", daily_total, self.limits.daily_usd),
        ):
            if limit is not None and total["usd"] + actual_usd > limit:
                store.settle_budget_event(reservation.event_id, actual_usd, over_budget=True)
                raise BudgetExceeded(f"{scope} spend ceiling exceeded after provider response")
        store.settle_budget_event(reservation.event_id, actual_usd)
        return actual_usd

    @staticmethod
    def estimate_request_usd(
        request: ProviderRequest, catalog: CatalogSnapshot | None
    ) -> float:
        if catalog is None:
            return 0.0
        try:
            model = catalog.model(request.dated_model_version)
        except KeyError:
            try:
                model = catalog.model(request.model_id)
            except KeyError:
                return 0.0
        estimated, _ = calculate_catalog_cost(
            model.pricing,
            {
                "prompt_tokens": max(1, sum(len(message.content.split()) for message in request.messages)),
                "completion_tokens": request.max_output_tokens,
                "request_count": 1,
            },
        )
        return 0.0 if estimated is None else float(estimated)


@dataclass(frozen=True, slots=True)
class RunMetrics:
    run_id: str
    status: str
    queue_depth: int
    throughput_per_minute: float
    retries: int
    errors: int
    error_rate: float
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cache_hits: int
    missing_cells: int
    estimated_cost_usd: float
    actual_cost_usd: float
    budget_limits: dict[str, float | int | None]
    alerts: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return asdict(self) | {"alerts": list(self.alerts)}


def collect_run_metrics(
    store: SqliteRunStore,
    run_id: str,
    *,
    limits: BudgetLimits | None = None,
    expected_cells: int | None = None,
    stall_after_seconds: float = 900,
) -> RunMetrics:
    summary = store.run_summary(run_id)
    attempts = store.rows_for_export("attempts", run_id)
    transports = store.rows_for_export("transport_events", run_id)
    queue = store.queue_counts(run_id)
    budget_rows = store.budget_rows(run_id)
    tokens = [json.loads(str(row["token_counts_json"])) for row in attempts]
    input_tokens = sum(
        int(row.get("input_tokens") or row.get("prompt_tokens") or row.get("input") or 0)
        for row in tokens
    )
    output_tokens = sum(
        int(row.get("output_tokens") or row.get("completion_tokens") or row.get("output") or 0)
        for row in tokens
    )
    errors = sum(row.get("event_type") == "failed" for row in transports)
    retries = sum(row.get("event_type") == "retry" for row in transports)
    started = _parse_time(summary.get("started_utc"))
    completed = _parse_time(summary.get("completed_utc")) or datetime.now(timezone.utc)
    elapsed_minutes = max((completed - (started or completed)).total_seconds() / 60, 1 / 60)
    latency_values = [float(row["latency_ms"]) for row in attempts]
    observed = len(attempts)
    missing = max((expected_cells or observed) - observed, 0)
    estimated = sum(float(row.get("estimated_usd") or 0) for row in budget_rows)
    actual = sum(float(row.get("amount_usd") or 0) for row in budget_rows if row.get("status") != "released")
    error_rate = errors / max(1, errors + observed)
    alerts: list[str] = []
    if summary.get("status") == "running" and queue["pending"] + queue["leased"] and _heartbeat_age(summary) > stall_after_seconds:
        alerts.append("stalled_work")
    if limits:
        for name, value, limit in (
            ("run", actual, limits.run_usd),
            ("experiment", actual, limits.experiment_usd),
            ("model", actual, limits.model_usd),
            ("daily", actual, limits.daily_usd),
        ):
            if limit is not None and limit > 0 and value >= limit * limits.warning_fraction:
                alerts.append(f"budget_risk_{name}")
    if error_rate >= 0.2:
        alerts.append("error_rate_spike")
    snapshots = store.rows_for_export("model_snapshots", run_id)
    if not snapshots or any(_catalog_unavailable(row.get("catalog_json")) for row in snapshots):
        alerts.append("model_disappearance")
    return RunMetrics(
        run_id=run_id,
        status=str(summary["status"]),
        queue_depth=queue["pending"] + queue["leased"],
        throughput_per_minute=observed / elapsed_minutes,
        retries=retries,
        errors=errors,
        error_rate=error_rate,
        latency_ms=sum(latency_values) / len(latency_values) if latency_values else 0.0,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_hits=sum(bool(row["from_cache"]) for row in attempts),
        missing_cells=missing,
        estimated_cost_usd=estimated,
        actual_cost_usd=actual,
        budget_limits={} if limits is None else asdict(limits),
        alerts=tuple(dict.fromkeys(alerts)),
    )


def write_run_status(
    store: SqliteRunStore,
    run_id: str,
    output: str | Path,
    *,
    limits: BudgetLimits | None = None,
    expected_cells: int | None = None,
) -> Path:
    metrics = collect_run_metrics(store, run_id, limits=limits, expected_cells=expected_cells)
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(metrics.to_json(), sort_keys=True, indent=2), encoding="utf-8")
    return destination


def _optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    result = float(value)
    if result < 0:
        raise MonitoringError("Budget values must be non-negative")
    return result


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _heartbeat_age(summary: dict[str, object]) -> float:
    heartbeat = _parse_time(summary.get("heartbeat_utc"))
    if heartbeat is None:
        return float("inf")
    return max(0.0, (datetime.now(timezone.utc) - heartbeat).total_seconds())


def _catalog_unavailable(value: object) -> bool:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return True
    return isinstance(value, dict) and value.get("available") is False
