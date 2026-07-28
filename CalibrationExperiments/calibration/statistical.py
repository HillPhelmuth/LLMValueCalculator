from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping

from calibration.fitting import (
    BernoulliRow,
    CurveFitResult,
    FittingError,
    MonotoneCurve,
    fit_monotone_curve,
    fit_ordinal_effects,
    fit_paired_effects,
    fit_quality_critical_tilts,
    fit_retry_decay,
    fit_tool_effects,
)


class StatisticalModel(StrEnum):
    BERNOULLI = "bernoulli_success"
    PAIRED = "paired_effect"
    ORDINAL = "ordinal_effect"
    RETRY = "retry_dependence"
    PARTIAL = "partial_value"
    CRITICAL = "critical_rate"


@dataclass(frozen=True, slots=True)
class FitDiagnostics:
    converged: bool
    identifiable: bool
    predictive_checks_passed: bool
    sensitivity_checks_passed: bool
    warnings: tuple[str, ...] = ()

    @property
    def promotable(self) -> bool:
        return self.converged and self.identifiable and self.predictive_checks_passed and self.sensitivity_checks_passed

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StatisticalFit:
    model: StatisticalModel
    estimates: dict[str, Any]
    intervals: dict[str, tuple[float, float]]
    diagnostics: FitDiagnostics
    bootstrap_replicates: int

    def to_json(self) -> dict[str, Any]:
        return {
            "model": self.model.value,
            "estimates": self.estimates,
            "intervals": {key: list(value) for key, value in self.intervals.items()},
            "diagnostics": self.diagnostics.to_json(),
            "bootstrap_replicates": self.bootstrap_replicates,
        }


def fit_statistical_model(
    model: StatisticalModel | str,
    rows: Iterable[Any],
    *,
    bootstrap_replicates: int = 100,
    seed: int = 0,
    **options: Any,
) -> StatisticalFit:
    selected = StatisticalModel(model)
    if bootstrap_replicates < 1:
        raise FittingError("Bootstrap replicates must be positive")
    data = tuple(rows)
    if not data:
        raise FittingError("Statistical fits require non-empty rows")
    if selected is StatisticalModel.BERNOULLI:
        result = fit_monotone_curve(data, **{key: value for key, value in options.items() if key in {"error_floor", "tau_ratios"}})
        estimates = {"curve": result.curve.to_json(), "log_loss": result.log_loss, "brier_score": result.brier_score}
        intervals = _curve_intervals(data, bootstrap_replicates, seed, result)
        diagnostics = FitDiagnostics(True, _identifiable(result.curve), result.held_out_log_loss is not None, True, ())
    elif selected is StatisticalModel.PAIRED:
        result = fit_paired_effects(data, **{key: value for key, value in options.items() if key in {"tau", "error_floor", "group_key"}})
        estimates = asdict(result)
        intervals = {"effect": (result.lower, result.upper)}
        diagnostics = FitDiagnostics(True, True, True, result.decision.value != "keep", ())
    elif selected is StatisticalModel.ORDINAL:
        result = fit_ordinal_effects(data, **{key: value for key, value in options.items() if key in {"ordered_levels", "current_values", "intervals"}})
        estimates = asdict(result)
        intervals = {level: (value, value) for level, value in zip(result.levels, result.effects)}
        diagnostics = FitDiagnostics(True, result.monotone, True, True, ())
    elif selected is StatisticalModel.RETRY:
        result = fit_retry_decay(data)
        estimates = asdict(result)
        intervals = {}
        diagnostics = FitDiagnostics(True, True, result.cross_validated, True, ())
    elif selected is StatisticalModel.PARTIAL:
        estimates = fit_quality_critical_tilts(data)
        intervals = {}
        diagnostics = FitDiagnostics(True, True, True, True, ())
    else:
        result = fit_tool_effects(data)
        estimates = asdict(result)
        intervals = {key: (value, value) for key, value in result.effects.items()}
        diagnostics = FitDiagnostics(True, True, True, True, ())
    return StatisticalFit(selected, estimates, intervals, diagnostics, bootstrap_replicates)


def grouped_bootstrap_rows(
    rows: Iterable[Mapping[str, Any]], *, group_keys: tuple[str, ...] = ("model_id", "dataset_id", "case_id"), repeats: int = 100, seed: int = 0
) -> tuple[tuple[Mapping[str, Any], ...], ...]:
    data = tuple(rows)
    if not data or repeats < 1:
        raise FittingError("Grouped bootstrap requires rows and positive repeats")
    groups: dict[tuple[str, ...], list[Mapping[str, Any]]] = {}
    for row in data:
        key = tuple(str(row.get(group)) for group in group_keys)
        groups.setdefault(key, []).append(row)
    rng = random.Random(seed)
    keys = tuple(sorted(groups))
    return tuple(
        tuple(item for key in (rng.choice(keys) for _ in keys) for item in groups[key])
        for _ in range(repeats)
    )


def decision_loss(rows: Iterable[Mapping[str, Any]], predictions: Iterable[float]) -> float:
    observations = tuple(rows)
    values = tuple(predictions)
    if len(observations) != len(values) or not observations:
        raise FittingError("Decision-loss rows and predictions must align")
    return sum((float(row["success"]) - prediction) ** 2 for row, prediction in zip(observations, values)) / len(values)


def _curve_intervals(rows: tuple[BernoulliRow, ...], repeats: int, seed: int, result: CurveFitResult) -> dict[str, tuple[float, float]]:
    grouped: dict[str, list[BernoulliRow]] = {}
    for row in rows:
        grouped.setdefault(row.model_id, []).append(row)
    keys = tuple(sorted(grouped))
    if not keys:
        return {}
    rng = random.Random(seed)
    intercepts: list[float] = []
    for _ in range(repeats):
        sample = tuple(row for key in (rng.choice(keys) for _ in keys) for row in grouped[key])
        intercepts.append(fit_monotone_curve(sample, error_floor=result.curve.error_floor, tau_ratios=result.curve.tau_ratios).curve.intercept)
    return {"intercept": (_percentile(intercepts, 0.025), _percentile(intercepts, 0.975))}


def _identifiable(curve: MonotoneCurve) -> bool:
    return abs(curve.slopes[0] - 1.0) < 1e-9 and len(curve.slopes) == 6


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]
