from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

import numpy as np
from scipy.optimize import OptimizeResult, minimize
from scipy.special import expit

from calibration.profile import CalibrationProfile, baseline_profile


class JudgeFittingError(ValueError):
    """Raised when the judge-only Experiment 1 fit is invalid."""


AbstentionTreatment = Literal["exclude", "incorrect", "correct"]
TAU_SCALE_BOUNDS = (0.01, 100.0)
EFFECT_STANDARD_DEVIATION = 1.5
FLOOR = 0.01
BASE_TAU = {"soft": 8.0, "normal": 5.0, "sharp": 3.0}


@dataclass(slots=True)
class _EncodedRows:
    intelligence: np.ndarray
    difficulty: np.ndarray
    base_tau: np.ndarray
    success: np.ndarray
    model_codes: np.ndarray
    dataset_codes: np.ndarray
    item_codes: np.ndarray
    model_levels: tuple[str, ...]
    dataset_levels: tuple[str, ...]
    item_levels: tuple[str, ...]
    raw: tuple[Mapping[str, Any], ...]


@dataclass(slots=True)
class _Fit:
    slopes: np.ndarray
    tau_scale: float
    effects: np.ndarray
    encoded: _EncodedRows
    objective: float
    converged: bool
    message: str
    iterations: int
    fit_curve: bool
    fit_tau: bool
    raw_parameters: np.ndarray


def fit_experiment_one_judge(
    fitting_data: str | Path,
    fitting_lock: str | Path,
    output: str | Path,
    *,
    profile_version: str = "experiment-1-judge-candidate-1.0.0",
    bootstrap_replicates: int = 100,
    profile_points: int = 5,
) -> dict[str, Path]:
    """Fit all registered alternatives and write immutable decision evidence."""
    if bootstrap_replicates != 100:
        raise JudgeFittingError(
            "Experiment 1 requires exactly 100 bootstraps per family"
        )
    source = Path(fitting_data)
    lock_path = Path(fitting_lock)
    rows = tuple(
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    actual_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual_hash != lock["fitting_data_hash"]:
        raise JudgeFittingError(
            "Fitting data bytes do not match the judge fitting lock"
        )
    if len(rows) != 20_000:
        raise JudgeFittingError(f"Expected 20,000 judge rows, found {len(rows)}")

    primary = _fit_registered_alternatives(rows, "exclude")
    sensitivity_treatments: tuple[AbstentionTreatment, ...] = (
        "incorrect",
        "correct",
    )
    sensitivities = {
        treatment: _fit_registered_alternatives(rows, treatment)
        for treatment in sensitivity_treatments
    }
    intervals = _profile_intervals(primary["full"], points=profile_points)
    tau_interior = _tau_interval_is_interior(intervals["tau_scale"])
    metrics = {
        name: _evaluation_report(fit, rows, "exclude") for name, fit in primary.items()
    }
    primary_decision = _decision(metrics, intervals, tau_interior)
    sensitivity_decisions = {
        treatment: _decision(
            {
                name: _evaluation_report(fit, rows, treatment)
                for name, fit in alternatives.items()
            },
            intervals,
            tau_interior,
        )
        for treatment, alternatives in sensitivities.items()
    }
    stable = all(
        item["decision"] == primary_decision["decision"]
        and item["curve_supported"] == primary_decision["curve_supported"]
        and item["tau_supported"] == primary_decision["tau_supported"]
        for item in sensitivity_decisions.values()
    )
    bootstraps = {
        "model": _bootstrap_stability(
            rows, "model_id", bootstrap_replicates, seed=2026071801
        ),
        "dataset_case": _bootstrap_stability(
            rows, "case_id", bootstrap_replicates, seed=2026071802
        ),
    }
    bootstrap_gate = all(
        item["sign_agreement"] >= 0.8 and item["converged_fraction"] >= 0.8
        for item in bootstraps.values()
    )
    change = (
        primary_decision["decision"] == "change"
        and (primary_decision["curve_supported"] or primary_decision["tau_supported"])
        and stable
        and bootstrap_gate
        and primary["full"].converged
        and tau_interior
    )

    baseline = baseline_profile()
    selected_slopes = (
        primary["full"].slopes.tolist()
        if change and primary_decision["curve_supported"]
        else [float(item["slope"]) for item in baseline.curve_segments]
    )
    selected_scale = (
        primary["full"].tau_scale
        if change and primary_decision["tau_supported"]
        else 1.0
    )
    final_decision = "change" if change else "keep"
    evidence = {
        "schema_version": "1.0",
        "experiment_id": "experiment-1-judge-refit",
        "supersedes_candidate_hash": (
            "6d97164936a9c8aedbcbd138f5aac719e17b4b42e24e78e84f0f8ac295ec7916"
        ),
        "decision": final_decision,
        "judge": {
            "model": "deepseek/deepseek-v4-flash",
            "validation_status": "unvalidated-by-policy",
            "self_judging": "blind-model-identity",
            "confidence_usage": "diagnostic-only",
        },
        "fitting_data_hash": actual_hash,
        "fitting_lock_hash": lock["fitting_data_lock_hash"],
        "rows": _row_accounting(rows),
        "optimizer": {
            "method": "L-BFGS-B constrained transformed likelihood",
            "curve_parameterization": "positive monotone softplus increments",
            "tau_parameterization": "log scale",
            "tau_scale_bounds": list(TAU_SCALE_BOUNDS),
            "effect_standard_deviation": EFFECT_STANDARD_DEVIATION,
            "nuisance_effects": ["dataset", "model", "item"],
            "prompt_effect": "non-estimable: one prompt version",
        },
        "alternatives": {
            name: _fit_json(fit, metrics[name]) for name, fit in primary.items()
        },
        "profile_likelihood_intervals": intervals,
        "tau_interval_interior": tau_interior,
        "primary_attribution": primary_decision,
        "abstention_sensitivity": sensitivity_decisions,
        "abstention_decision_stable": stable,
        "bootstraps": bootstraps,
        "bootstrap_gate": bootstrap_gate,
        "selected_slopes": selected_slopes,
        "selected_tau": {
            key: value * selected_scale for key, value in BASE_TAU.items()
        },
        "active_error_floor": FLOOR,
        "repeat_error_floor_status": "provisional-only",
        "limitations": [
            "The judge was not validated against human or synthetic labels.",
            "DeepSeek responses were judged by DeepSeek with source identity hidden.",
            "Judge confidence was not used as a soft correctness label.",
        ],
    }
    evidence["evidence_hash"] = _canonical_hash(evidence)

    profile = CalibrationProfile(
        profile_version=profile_version,
        curve_segments=tuple(
            {
                "upper": 10.0 * (index + 1) if index < 5 else None,
                "slope": float(slope),
            }
            for index, slope in enumerate(selected_slopes)
        ),
        tau={key: value * selected_scale for key, value in BASE_TAU.items()},
        error_floor=FLOOR,
        adjustments=baseline.adjustments,
        risk_multipliers=baseline.risk_multipliers,
        uncertainty={
            "experiment_1_evidence_hash": evidence["evidence_hash"],
            "candidate_only": True,
            "judge_validation_status": "unvalidated-by-policy",
            "self_judging": "blind-model-identity",
            "profile_likelihood_intervals": intervals,
            "bootstrap_stability": bootstraps,
        },
        manifest_hashes=(
            lock["main_judge_manifest_hash"],
            lock["repeat_judge_manifest_hash"],
        ),
        fitting_data_hash=actual_hash,
        aa_snapshot=lock["panel_hash"],
        source_estimate_ids=(evidence["evidence_hash"],),
        promotion_decisions={
            "experiment_1": final_decision,
            "curve": "change"
            if change and primary_decision["curve_supported"]
            else "keep",
            "tau": "change" if change and primary_decision["tau_supported"] else "keep",
            "error_floor": "keep",
        },
    )

    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    evidence_path = root / "experiment-1-judge-fit-evidence.json"
    candidate_path = root / "experiment-1-judge-candidate.json"
    metrics_path = root / "experiment-1-judge-metrics.json"
    diagnostics_path = root / "experiment-1-judge-diagnostics.json"
    ablation_path = root / "experiment-1-judge-ablation.json"
    cost_path = root / "experiment-1-judge-cost-reconciliation.json"
    reliability_path = root / "experiment-1-judge-reliability.svg"
    residual_path = root / "experiment-1-judge-residuals.svg"
    card_path = root / "experiment-1-judge-calibration-card.md"
    _write_immutable_json(evidence_path, evidence)
    _write_immutable_json(candidate_path, profile.to_json())
    diagnostics = _diagnostics(primary["full"], rows)
    _write_immutable_json(
        metrics_path,
        {
            "alternatives": metrics,
            "per_model": _group_metrics(primary["full"], rows, "model_id"),
            "per_dataset": _group_metrics(primary["full"], rows, "dataset_id"),
            "per_band": _group_metrics(primary["full"], rows, "aa_band"),
            "abstentions": _abstention_groups(rows),
            "self_judged": _self_judged_metrics(rows),
            "repeat_evidence": lock["repeat_evidence"],
            "judge_cost_usd": lock["judge_cost_usd"],
            "judge_recovery_cost_usd": lock["judge_recovery_cost_usd"],
            "aggregate_experiment_spend_usd": lock["aggregate_experiment_spend_usd"],
        },
    )
    _write_immutable_json(diagnostics_path, diagnostics)
    _write_immutable_json(
        ablation_path,
        {
            "registered_alternatives": {
                name: _fit_json(fit, metrics[name]) for name, fit in primary.items()
            },
            "parameter_attribution": primary_decision,
            "interpretation": (
                "Curve changes require full versus tau-only gates; tau changes "
                "require full versus curve-only gates."
            ),
        },
    )
    _write_immutable_json(
        cost_path,
        {
            "prior_experiment_spend_usd": lock["prior_experiment_spend_usd"],
            "judge_spend_usd": lock["judge_cost_usd"],
            "judge_recovery_spend_usd": lock["judge_recovery_cost_usd"],
            "judge_spend_basis": lock["judge_spend_basis"],
            "unresolved_reservations": lock["unresolved_judge_reservations"],
            "unresolved_reservation_usd": lock["unresolved_judge_reservation_usd"],
            "aggregate_experiment_spend_usd": lock["aggregate_experiment_spend_usd"],
            "judge_ceiling_usd": lock["judge_ceiling_usd"],
            "aggregate_ceiling_usd": lock["aggregate_ceiling_usd"],
            "passed": lock["judge_cost_usd"] + lock["judge_recovery_cost_usd"]
            <= lock["judge_ceiling_usd"]
            and lock["aggregate_experiment_spend_usd"] <= lock["aggregate_ceiling_usd"],
        },
    )
    _write_immutable_text(
        reliability_path,
        _line_svg(
            [
                (float(item["mean_prediction"]), float(item["observed_rate"]))
                for item in diagnostics["reliability"]
                if item["rows"]
            ],
            "Held-out reliability",
            "Predicted",
            "Observed",
        ),
    )
    _write_immutable_text(
        residual_path,
        _bar_svg(
            [
                (str(item["group"]), float(item["mean_residual"]))
                for item in diagnostics["residuals_by_model"]
            ],
            "Held-out residual by model (observed - predicted)",
        ),
    )
    _write_immutable_text(
        card_path,
        _calibration_card(profile, evidence, lock, diagnostics),
    )
    return {
        "evidence": evidence_path,
        "candidate": candidate_path,
        "metrics": metrics_path,
        "diagnostics": diagnostics_path,
        "ablation": ablation_path,
        "cost_reconciliation": cost_path,
        "reliability_plot": reliability_path,
        "residual_plot": residual_path,
        "calibration_card": card_path,
    }


def _fit_registered_alternatives(
    rows: tuple[Mapping[str, Any], ...],
    treatment: AbstentionTreatment,
) -> dict[str, _Fit]:
    train = tuple(
        row
        for row in rows
        if not bool(row["dataset_holdout"])
        and not bool(row["model_holdout"])
        and _success(row, treatment) is not None
    )
    if not train:
        raise JudgeFittingError("No fitting rows remain after holdout isolation")
    baseline_slopes = np.array([1.0, 1.4, 1.8, 2.2, 2.6, 3.0])
    return {
        "compiled": _fit_model(train, treatment, False, False, baseline_slopes, 1.0),
        "tau_only": _fit_model(train, treatment, False, True, baseline_slopes, 1.0),
        "curve_only": _fit_model(train, treatment, True, False, baseline_slopes, 1.0),
        "full": _fit_model(train, treatment, True, True, baseline_slopes, 1.0),
    }


def _fit_model(
    rows: tuple[Mapping[str, Any], ...],
    treatment: AbstentionTreatment,
    fit_curve: bool,
    fit_tau: bool,
    initial_slopes: np.ndarray,
    initial_tau: float,
    *,
    max_iterations: int = 80,
) -> _Fit:
    encoded = _encode(rows, treatment)
    slope_raw = _slopes_to_raw(initial_slopes)
    parts: list[np.ndarray] = []
    if fit_curve:
        parts.append(slope_raw)
    if fit_tau:
        parts.append(np.array([math.log(initial_tau)]))
    effect_count = (
        len(encoded.dataset_levels)
        + len(encoded.model_levels)
        + len(encoded.item_levels)
    )
    parts.append(np.zeros(effect_count))
    initial = np.concatenate(parts)
    bounds: list[tuple[float | None, float | None]] = []
    if fit_curve:
        bounds.extend([(-12.0, 8.0)] * 5)
    if fit_tau:
        bounds.append(
            (
                math.log(TAU_SCALE_BOUNDS[0]),
                math.log(TAU_SCALE_BOUNDS[1]),
            )
        )
    bounds.extend([(None, None)] * effect_count)

    def objective(parameters: np.ndarray) -> tuple[float, np.ndarray]:
        return _objective_and_gradient(
            parameters,
            encoded,
            fit_curve,
            fit_tau,
            initial_slopes,
            initial_tau,
        )

    result: OptimizeResult = minimize(
        objective,
        initial,
        method="L-BFGS-B",
        jac=True,
        bounds=bounds,
        options={
            "maxiter": max_iterations,
            "ftol": 1e-10,
            "gtol": 1e-6,
            "maxls": 100,
        },
    )
    offset = 0
    if fit_curve:
        slopes = _raw_to_slopes(result.x[:5])
        offset = 5
    else:
        slopes = initial_slopes.copy()
    if fit_tau:
        tau_scale = float(math.exp(result.x[offset]))
        offset += 1
    else:
        tau_scale = initial_tau
    return _Fit(
        slopes=slopes,
        tau_scale=tau_scale,
        effects=result.x[offset:].copy(),
        encoded=encoded,
        objective=float(result.fun),
        converged=bool(result.success),
        message=str(result.message),
        iterations=int(result.nit),
        fit_curve=fit_curve,
        fit_tau=fit_tau,
        raw_parameters=result.x.copy(),
    )


def _objective_and_gradient(
    parameters: np.ndarray,
    data: _EncodedRows,
    fit_curve: bool,
    fit_tau: bool,
    fixed_slopes: np.ndarray,
    fixed_tau: float,
) -> tuple[float, np.ndarray]:
    offset = 0
    if fit_curve:
        raw_slopes = parameters[:5]
        slopes = _raw_to_slopes(raw_slopes)
        offset = 5
    else:
        raw_slopes = np.empty(0)
        slopes = fixed_slopes
    if fit_tau:
        log_tau = float(parameters[offset])
        tau_scale = math.exp(log_tau)
        offset += 1
    else:
        tau_scale = fixed_tau
    effects = parameters[offset:]
    dataset_count = len(data.dataset_levels)
    model_count = len(data.model_levels)
    dataset_effects = effects[:dataset_count]
    model_effects = effects[dataset_count : dataset_count + model_count]
    item_effects = effects[dataset_count + model_count :]
    nuisance = (
        dataset_effects[data.dataset_codes]
        + model_effects[data.model_codes]
        + item_effects[data.item_codes]
    )
    latent, features = _curve_latent(data.intelligence, slopes)
    local_index = _local_slope_index(data.difficulty, slopes)
    local_slope = slopes[local_index]
    denominator = data.base_tau * tau_scale * local_slope
    numerator = latent - data.difficulty
    z = numerator / denominator + nuisance
    sigmoid = expit(z)
    probability = np.clip((1.0 - FLOOR) * sigmoid, 1e-12, 1.0 - 1e-12)
    n = len(data.success)
    loss = -np.mean(
        data.success * np.log(probability)
        + (1.0 - data.success) * np.log(1.0 - probability)
    )
    penalty = 0.5 * float(np.dot(effects, effects)) / (EFFECT_STANDARD_DEVIATION**2 * n)
    derivative_probability = (1.0 - FLOOR) * sigmoid * (1.0 - sigmoid)
    dloss_dz = (
        (probability - data.success)
        / (probability * (1.0 - probability))
        * derivative_probability
        / n
    )
    gradients: list[np.ndarray] = []
    if fit_curve:
        dz_dslope = features / denominator[:, None]
        rows_index = np.arange(n)
        dz_dslope[rows_index, local_index] -= numerator / (
            data.base_tau * tau_scale * local_slope**2
        )
        slope_gradient = dloss_dz @ dz_dslope
        gradients.append(_slope_gradient_to_raw(slope_gradient, raw_slopes))
    if fit_tau:
        gradients.append(np.array([float(np.dot(dloss_dz, -z))]))
    effect_scale = dloss_dz
    effect_gradient = np.concatenate(
        (
            np.bincount(
                data.dataset_codes,
                weights=effect_scale,
                minlength=dataset_count,
            ),
            np.bincount(data.model_codes, weights=effect_scale, minlength=model_count),
            np.bincount(
                data.item_codes,
                weights=effect_scale,
                minlength=len(data.item_levels),
            ),
        )
    )
    effect_gradient += effects / (EFFECT_STANDARD_DEVIATION**2 * n)
    gradients.append(effect_gradient)
    return float(loss + penalty), np.concatenate(gradients)


def _evaluation_report(
    fit: _Fit,
    rows: tuple[Mapping[str, Any], ...],
    treatment: AbstentionTreatment,
) -> dict[str, Any]:
    groups = {
        "fit": lambda row: not row["dataset_holdout"] and not row["model_holdout"],
        "model_holdout": lambda row: row["model_holdout"]
        and not row["dataset_holdout"],
        "dataset_holdout": lambda row: row["dataset_holdout"]
        and not row["model_holdout"],
        "combined_holdout": lambda row: row["dataset_holdout"] or row["model_holdout"],
    }
    return {
        key: _metrics(fit, tuple(row for row in rows if predicate(row)), treatment)
        for key, predicate in groups.items()
    }


def _metrics(
    fit: _Fit,
    rows: tuple[Mapping[str, Any], ...],
    treatment: AbstentionTreatment,
) -> dict[str, float | int]:
    usable = tuple(row for row in rows if _success(row, treatment) is not None)
    if not usable:
        return {"rows": 0, "log_loss": 0.0, "brier_score": 0.0}
    success = np.array([_required_success(row, treatment) for row in usable])
    probability = _predict(fit, usable)
    return {
        "rows": len(usable),
        "log_loss": float(
            -np.mean(
                success * np.log(np.clip(probability, 1e-12, 1))
                + (1 - success) * np.log(np.clip(1 - probability, 1e-12, 1))
            )
        ),
        "brier_score": float(np.mean((probability - success) ** 2)),
    }


def _predict(fit: _Fit, rows: tuple[Mapping[str, Any], ...]) -> np.ndarray:
    intelligence = np.array([float(row["intelligence_index"]) for row in rows])
    difficulty = np.array([float(row["difficulty"]) for row in rows])
    base_tau = np.array([BASE_TAU[str(row["tau_key"])] for row in rows])
    latent, _ = _curve_latent(intelligence, fit.slopes)
    levels = (
        fit.encoded.dataset_levels,
        fit.encoded.model_levels,
        fit.encoded.item_levels,
    )
    counts = tuple(len(item) for item in levels)
    offsets = (0, counts[0], counts[0] + counts[1])
    maps = tuple({value: index for index, value in enumerate(item)} for item in levels)
    nuisance = np.zeros(len(rows))
    keys = ("dataset_id", "model_id", "case_id")
    for key, mapping, offset in zip(keys, maps, offsets):
        nuisance += np.array(
            [
                fit.effects[offset + mapping[str(row[key])]]
                if str(row[key]) in mapping
                else 0.0
                for row in rows
            ]
        )
    local = fit.slopes[_local_slope_index(difficulty, fit.slopes)]
    z = (latent - difficulty) / (base_tau * fit.tau_scale * local) + nuisance
    return np.clip((1 - FLOOR) / (1 + np.exp(-np.clip(z, -700, 700))), 1e-12, 1 - 1e-12)


def _decision(
    metrics: Mapping[str, Mapping[str, Mapping[str, Any]]],
    intervals: Mapping[str, Any],
    tau_interior: bool,
) -> dict[str, Any]:
    def beats(candidate: str, comparator: str) -> tuple[bool, dict[str, Any]]:
        details: dict[str, Any] = {}
        passed = True
        for holdout in ("model_holdout", "dataset_holdout"):
            candidate_metrics = metrics[candidate][holdout]
            comparator_metrics = metrics[comparator][holdout]
            log_gain = (
                comparator_metrics["log_loss"] - candidate_metrics["log_loss"]
            ) / max(comparator_metrics["log_loss"], 1e-12)
            brier_gain = (
                comparator_metrics["brier_score"] - candidate_metrics["brier_score"]
            ) / max(comparator_metrics["brier_score"], 1e-12)
            details[holdout] = {
                "relative_log_loss_improvement": log_gain,
                "relative_brier_improvement": brier_gain,
                "passed": log_gain >= 0.02 and brier_gain >= 0.01,
            }
            passed &= bool(details[holdout]["passed"])
        return passed, details

    full_gate, versus_baseline = beats("full", "compiled")
    curve_gate, versus_tau_only = beats("full", "tau_only")
    tau_gate, versus_curve_only = beats("full", "curve_only")
    curve_identifiable = intervals["global_information_rank"] == intervals[
        "expected_rank"
    ] and all(
        math.isfinite(item["lower"])
        and math.isfinite(item["upper"])
        and item["upper"] - item["lower"] < 10
        and bool(item["profile_scan_closed"])
        for item in intervals["slopes"]
    )
    return {
        "decision": "change" if full_gate else "keep",
        "full_versus_compiled": versus_baseline,
        "curve_supported": full_gate and curve_gate and curve_identifiable,
        "curve_versus_tau_only": versus_tau_only,
        "tau_supported": full_gate and tau_gate and tau_interior,
        "tau_versus_curve_only": versus_curve_only,
        "curve_identifiable": curve_identifiable,
    }


def _profile_intervals(fit: _Fit, *, points: int) -> dict[str, Any]:
    if points < 5 or points % 2 == 0:
        raise JudgeFittingError("Profile likelihood requires an odd grid of at least 5")
    # Use the local information matrix only to choose a wide scan. At every
    # scan point all other curve, tau, and nuisance parameters are reoptimized.
    global_count = int(fit.fit_curve) * 5 + int(fit.fit_tau)
    if global_count != 6:
        raise JudgeFittingError("Intervals require the full curve-and-tau fit")
    epsilon = 2e-3
    hessian = np.zeros((global_count, global_count))

    def gradient(global_values: np.ndarray) -> np.ndarray:
        candidate = fit.raw_parameters.copy()
        candidate[:global_count] = global_values
        return _objective_and_gradient(
            candidate,
            fit.encoded,
            True,
            True,
            fit.slopes,
            fit.tau_scale,
        )[1][:global_count]

    center = fit.raw_parameters[:global_count]
    for column in range(global_count):
        delta = np.zeros(global_count)
        delta[column] = epsilon
        hessian[:, column] = (gradient(center + delta) - gradient(center - delta)) / (
            2 * epsilon
        )
    hessian = (hessian + hessian.T) / 2
    rank = int(np.linalg.matrix_rank(hessian, tol=1e-8))
    covariance = np.linalg.pinv(hessian)
    standard_errors = np.sqrt(np.maximum(np.diag(covariance), 1e-8))
    scans: list[dict[str, Any]] = []
    parameter_bounds = [(-12.0, 8.0)] * 5 + [
        tuple(math.log(value) for value in TAU_SCALE_BOUNDS)
    ]
    for index in range(global_count):
        lower_bound, upper_bound = parameter_bounds[index]
        radius = min(max(3.0 * standard_errors[index], 0.25), 4.0)
        grid = np.linspace(
            max(lower_bound, center[index] - radius),
            min(upper_bound, center[index] + radius),
            points,
        )
        grid[int(np.argmin(np.abs(grid - center[index])))] = center[index]
        grid.sort()
        scan_rows: list[dict[str, Any]] = []
        free = np.array(
            [item for item in range(len(fit.raw_parameters)) if item != index]
        )
        free_bounds = (
            parameter_bounds[:index]
            + parameter_bounds[index + 1 :]
            + [(None, None)] * len(fit.effects)
        )
        for fixed_value in grid:
            initial = fit.raw_parameters.copy()
            initial[index] = fixed_value

            def profiled(value: np.ndarray) -> tuple[float, np.ndarray]:
                candidate = initial.copy()
                candidate[free] = value
                objective, full_gradient = _objective_and_gradient(
                    candidate,
                    fit.encoded,
                    True,
                    True,
                    fit.slopes,
                    fit.tau_scale,
                )
                return objective, full_gradient[free]

            result = minimize(
                profiled,
                initial[free],
                method="L-BFGS-B",
                jac=True,
                bounds=free_bounds,
                options={
                    # The nuisance design is intentionally high dimensional
                    # (item effects).  A short, warm-started constrained
                    # re-fit at each profile point is enough to establish
                    # whether the likelihood crosses the LR threshold while
                    # keeping the 100+100 bootstrap verification practical.
                    "maxiter": 6,
                    "ftol": 1e-9,
                    "gtol": 1e-5,
                    "maxls": 100,
                },
            )
            candidate = initial.copy()
            candidate[free] = result.x
            values = _raw_to_slopes(candidate[:5])
            scan_rows.append(
                {
                    "fixed_transformed_value": float(fixed_value),
                    "parameter_value": float(values[index + 1])
                    if index < 5
                    else float(math.exp(candidate[5])),
                    "likelihood_ratio": float(
                        2
                        * len(fit.encoded.success)
                        * max(float(result.fun) - fit.objective, 0.0)
                    ),
                    "converged": bool(result.success),
                }
            )
        scans.append(
            {
                "parameter": f"slope_{index + 2}" if index < 5 else "tau_scale",
                "points": scan_rows,
            }
        )

    slope_intervals = [
        {
            "parameter": "slope_1",
            "estimate": 1.0,
            "lower": 1.0,
            "upper": 1.0,
            "method": "fixed by preregistration",
            "profile_scan_closed": True,
        }
    ]
    for index, scan in enumerate(scans[:5]):
        accepted = [
            item["parameter_value"]
            for item in scan["points"]
            if item["likelihood_ratio"] <= 3.841459
        ]
        if not accepted:
            accepted = [float(fit.slopes[index + 1])]
            accepted_from_profile = False
        else:
            accepted_from_profile = True
        slope_intervals.append(
            {
                "parameter": f"slope_{index + 2}",
                "estimate": float(fit.slopes[index + 1]),
                "lower": min(accepted) if accepted else float("nan"),
                "upper": max(accepted) if accepted else float("nan"),
                "method": "profile likelihood (95%, LR <= 3.841459)",
                "profile_scan_closed": accepted_from_profile
                and scan["points"][0]["likelihood_ratio"] > 3.841459
                and scan["points"][-1]["likelihood_ratio"] > 3.841459,
            }
        )
    tau_accepted = [
        item["parameter_value"]
        for item in scans[5]["points"]
        if item["likelihood_ratio"] <= 3.841459
    ]
    if not tau_accepted:
        tau_accepted = [fit.tau_scale]
        tau_accepted_from_profile = False
    else:
        tau_accepted_from_profile = True
    return {
        "slopes": slope_intervals,
        "tau_scale": {
            "estimate": fit.tau_scale,
            "lower": min(tau_accepted) if tau_accepted else float("nan"),
            "upper": max(tau_accepted) if tau_accepted else float("nan"),
            "method": "profile likelihood (95%, LR <= 3.841459)",
            "profile_scan_closed": tau_accepted_from_profile
            and scans[5]["points"][0]["likelihood_ratio"] > 3.841459
            and scans[5]["points"][-1]["likelihood_ratio"] > 3.841459,
        },
        "scans": scans,
        "global_information_rank": rank,
        "expected_rank": global_count,
        "grid_points": points,
    }


def _bootstrap_stability(
    rows: tuple[Mapping[str, Any], ...],
    group_key: str,
    repeats: int,
    *,
    seed: int,
) -> dict[str, Any]:
    training = tuple(
        row
        for row in rows
        if not row["dataset_holdout"]
        and not row["model_holdout"]
        and row["success"] is not None
    )
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in training:
        grouped.setdefault(str(row[group_key]), []).append(row)
    keys = tuple(sorted(grouped))
    rng = random.Random(seed)
    baseline_slopes = np.array([1.0, 1.4, 1.8, 2.2, 2.6, 3.0])
    improvements: list[float] = []
    brier_improvements: list[float] = []
    estimates: list[dict[str, Any]] = []
    for _ in range(repeats):
        sample = tuple(
            row for key in (rng.choice(keys) for _ in keys) for row in grouped[key]
        )
        baseline = _fit_model(
            sample,
            "exclude",
            False,
            False,
            baseline_slopes,
            1.0,
            max_iterations=4,
        )
        candidate = _fit_model(
            sample,
            "exclude",
            True,
            True,
            baseline_slopes,
            1.0,
            max_iterations=6,
        )
        heldout = tuple(
            row
            for row in rows
            if (row["dataset_holdout"] or row["model_holdout"])
            and row["success"] is not None
        )
        base_metric = _metrics(baseline, heldout, "exclude")
        candidate_metric = _metrics(candidate, heldout, "exclude")
        gain = (base_metric["log_loss"] - candidate_metric["log_loss"]) / max(
            base_metric["log_loss"], 1e-12
        )
        brier_gain = (
            base_metric["brier_score"] - candidate_metric["brier_score"]
        ) / max(base_metric["brier_score"], 1e-12)
        improvements.append(float(gain))
        brier_improvements.append(float(brier_gain))
        estimates.append(
            {
                "slopes": candidate.slopes.tolist(),
                "tau_scale": candidate.tau_scale,
                "log_loss_improvement": gain,
                "brier_improvement": brier_gain,
                "converged": candidate.converged,
            }
        )
    return {
        "replicates": repeats,
        "group_key": group_key,
        "sign_agreement": sum(
            log_gain > 0 and brier_gain > 0
            for log_gain, brier_gain in zip(
                improvements, brier_improvements, strict=True
            )
        )
        / repeats,
        "log_loss_improvement_interval": [
            float(np.quantile(improvements, 0.025)),
            float(np.quantile(improvements, 0.975)),
        ],
        "brier_improvement_interval": [
            float(np.quantile(brier_improvements, 0.025)),
            float(np.quantile(brier_improvements, 0.975)),
        ],
        "slope_intervals": [
            [
                float(np.quantile([row["slopes"][index] for row in estimates], 0.025)),
                float(np.quantile([row["slopes"][index] for row in estimates], 0.975)),
            ]
            for index in range(6)
        ],
        "tau_scale_interval": [
            float(np.quantile([row["tau_scale"] for row in estimates], 0.025)),
            float(np.quantile([row["tau_scale"] for row in estimates], 0.975)),
        ],
        "converged_fraction": sum(row["converged"] for row in estimates) / repeats,
    }


def _encode(
    rows: tuple[Mapping[str, Any], ...],
    treatment: AbstentionTreatment,
) -> _EncodedRows:
    usable = tuple(row for row in rows if _success(row, treatment) is not None)
    model_levels = tuple(sorted({str(row["model_id"]) for row in usable}))
    dataset_levels = tuple(sorted({str(row["dataset_id"]) for row in usable}))
    item_levels = tuple(sorted({str(row["case_id"]) for row in usable}))
    model_map = {value: index for index, value in enumerate(model_levels)}
    dataset_map = {value: index for index, value in enumerate(dataset_levels)}
    item_map = {value: index for index, value in enumerate(item_levels)}
    return _EncodedRows(
        intelligence=np.array([float(row["intelligence_index"]) for row in usable]),
        difficulty=np.array([float(row["difficulty"]) for row in usable]),
        base_tau=np.array([BASE_TAU[str(row["tau_key"])] for row in usable]),
        success=np.array([_required_success(row, treatment) for row in usable]),
        model_codes=np.array([model_map[str(row["model_id"])] for row in usable]),
        dataset_codes=np.array([dataset_map[str(row["dataset_id"])] for row in usable]),
        item_codes=np.array([item_map[str(row["case_id"])] for row in usable]),
        model_levels=model_levels,
        dataset_levels=dataset_levels,
        item_levels=item_levels,
        raw=usable,
    )


def _curve_latent(
    intelligence: np.ndarray, slopes: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    lower = np.array([0, 10, 20, 30, 40, 50], dtype=float)
    features = np.maximum(0.0, np.minimum(intelligence[:, None] - lower[None, :], 10.0))
    features[:, -1] = np.maximum(0.0, intelligence - 50.0)
    return features @ slopes, features


def _local_slope_index(difficulty: np.ndarray, slopes: np.ndarray) -> np.ndarray:
    adjusted_bounds = np.cumsum(slopes[:5] * 10.0)
    return np.searchsorted(adjusted_bounds, difficulty, side="left")


def _raw_to_slopes(raw: np.ndarray) -> np.ndarray:
    increments = np.logaddexp(0.0, raw)
    slopes = np.empty(6)
    slopes[0] = 1.0
    slopes[1] = 1.0 + increments[0]
    for index in range(2, 6):
        slopes[index] = slopes[index - 1] + increments[index - 1]
    return slopes


def _slopes_to_raw(slopes: np.ndarray) -> np.ndarray:
    increments = np.array(
        [max(float(slopes[1]) - 1.0, 1e-6)]
        + [max(float(slopes[index] - slopes[index - 1]), 1e-6) for index in range(2, 6)]
    )
    return np.log(np.expm1(increments))


def _slope_gradient_to_raw(slope_gradient: np.ndarray, raw: np.ndarray) -> np.ndarray:
    sigmoid = 1.0 / (1.0 + np.exp(-raw))
    return np.array(
        [
            sigmoid[index] * float(np.sum(slope_gradient[index + 1 :]))
            for index in range(5)
        ]
    )


def _success(row: Mapping[str, Any], treatment: AbstentionTreatment) -> bool | None:
    value = row.get("success")
    if value is not None:
        return bool(value)
    if treatment == "incorrect":
        return False
    if treatment == "correct":
        return True
    return None


def _required_success(row: Mapping[str, Any], treatment: AbstentionTreatment) -> float:
    value = _success(row, treatment)
    if value is None:
        raise JudgeFittingError("An abstention reached an encoded fitting row")
    return float(value)


def _tau_interval_is_interior(interval: Mapping[str, float]) -> bool:
    log_lower, log_upper = (math.log(item) for item in TAU_SCALE_BOUNDS)
    margin = 0.02 * (log_upper - log_lower)
    return (
        math.isfinite(float(interval["lower"]))
        and math.isfinite(float(interval["upper"]))
        and bool(interval["profile_scan_closed"])
        and math.log(max(float(interval["lower"]), TAU_SCALE_BOUNDS[0]))
        > log_lower + margin
        and math.log(min(float(interval["upper"]), TAU_SCALE_BOUNDS[1]))
        < log_upper - margin
    )


def _fit_json(fit: _Fit, metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "slopes": fit.slopes.tolist(),
        "tau_scale": fit.tau_scale,
        "tau": {key: value * fit.tau_scale for key, value in BASE_TAU.items()},
        "objective": fit.objective,
        "converged": fit.converged,
        "message": fit.message,
        "iterations": fit.iterations,
        "metrics": metrics,
    }


def _row_accounting(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    data = tuple(rows)
    return {
        "total": len(data),
        "correct": sum(row["success"] is True for row in data),
        "incorrect": sum(row["success"] is False for row in data),
        "abstain_or_invalid": sum(row["success"] is None for row in data),
        "self_judged": sum(bool(row["self_judged"]) for row in data),
    }


def _abstention_groups(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    output: dict[str, dict[str, dict[str, int]]] = {}
    for key in ("model_id", "dataset_id", "task_family"):
        grouped: dict[str, dict[str, int]] = {}
        for row in rows:
            value = str(row[key])
            item = grouped.setdefault(value, {"rows": 0, "abstentions": 0})
            item["rows"] += 1
            item["abstentions"] += row["success"] is None
        output[key] = grouped
    return output


def _self_judged_metrics(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    data = tuple(row for row in rows if bool(row["self_judged"]))
    decided = tuple(row for row in data if row["success"] is not None)
    return {
        "rows": len(data),
        "decided": len(decided),
        "correct_rate": sum(bool(row["success"]) for row in decided) / len(decided)
        if decided
        else None,
        "interpretation": "reported separately; no accuracy-validation gate",
    }


def _group_metrics(
    fit: _Fit,
    rows: Iterable[Mapping[str, Any]],
    key: str,
) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row[key]), []).append(row)
    return {
        group: _metrics(fit, tuple(values), "exclude")
        for group, values in sorted(grouped.items())
    }


def _diagnostics(fit: _Fit, rows: tuple[Mapping[str, Any], ...]) -> dict[str, Any]:
    heldout = tuple(
        row
        for row in rows
        if (row["dataset_holdout"] or row["model_holdout"])
        and row["success"] is not None
    )
    predictions = _predict(fit, heldout)
    outcomes = np.array([float(row["success"]) for row in heldout])
    reliability: list[dict[str, Any]] = []
    for index in range(10):
        lower = index / 10
        upper = (index + 1) / 10
        selected = (predictions >= lower) & (
            predictions <= upper if index == 9 else predictions < upper
        )
        reliability.append(
            {
                "lower": lower,
                "upper": upper,
                "rows": int(np.sum(selected)),
                "mean_prediction": float(np.mean(predictions[selected]))
                if np.any(selected)
                else (lower + upper) / 2,
                "observed_rate": float(np.mean(outcomes[selected]))
                if np.any(selected)
                else 0.0,
            }
        )
    residual_groups: list[dict[str, Any]] = []
    for model in sorted({str(row["model_id"]) for row in heldout}):
        selected = np.array([str(row["model_id"]) == model for row in heldout])
        residual_groups.append(
            {
                "group": model,
                "rows": int(np.sum(selected)),
                "mean_residual": float(
                    np.mean(outcomes[selected] - predictions[selected])
                ),
            }
        )
    return {
        "heldout_rows": len(heldout),
        "reliability": reliability,
        "residuals_by_model": residual_groups,
        "rank_diagnostic": "reported in fit evidence",
        "prompt_effect": "non-estimable: one prompt version",
    }


def _line_svg(
    points: list[tuple[float, float]],
    title: str,
    x_label: str,
    y_label: str,
) -> str:
    coordinates = " ".join(f"{60 + x * 500:.1f},{340 - y * 280:.1f}" for x, y in points)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400" '
        'viewBox="0 0 640 400">'
        '<rect width="640" height="400" fill="white"/>'
        f'<text x="320" y="28" text-anchor="middle" font-family="sans-serif" '
        f'font-size="16">{title}</text>'
        '<line x1="60" y1="340" x2="560" y2="60" stroke="#aaa" '
        'stroke-dasharray="5 5"/>'
        f'<polyline points="{coordinates}" fill="none" stroke="#2563eb" '
        'stroke-width="3"/>'
        f'<text x="310" y="385" text-anchor="middle" '
        f'font-family="sans-serif">{x_label}</text>'
        f'<text x="18" y="200" text-anchor="middle" font-family="sans-serif" '
        f'transform="rotate(-90 18 200)">{y_label}</text>'
        "</svg>\n"
    )


def _bar_svg(points: list[tuple[str, float]], title: str) -> str:
    width = 760
    height = 80 + 28 * len(points)
    bars: list[str] = []
    for index, (label, value) in enumerate(points):
        y = 55 + index * 28
        x = 380 + min(value, 0) * 900
        bar_width = max(abs(value) * 900, 1)
        bars.append(
            f'<text x="8" y="{y + 12}" font-family="sans-serif" '
            f'font-size="11">{label}</text>'
            f'<rect x="{x:.1f}" y="{y}" width="{bar_width:.1f}" height="16" '
            f'fill="{"#16a34a" if value >= 0 else "#dc2626"}"/>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" fill="white"/>'
        f'<text x="{width / 2}" y="24" text-anchor="middle" '
        f'font-family="sans-serif" font-size="16">{title}</text>'
        f'<line x1="380" y1="45" x2="380" y2="{height - 10}" stroke="#333"/>'
        + "".join(bars)
        + "</svg>\n"
    )


def _calibration_card(
    profile: CalibrationProfile,
    evidence: Mapping[str, Any],
    lock: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
) -> str:
    attribution = evidence["primary_attribution"]
    return f"""# Experiment 1 LLM-Judge Calibration Card

Decision: **{evidence["decision"]}** (candidate only; not promoted)

- Candidate profile hash: `{profile.profile_hash}`
- Fitting data hash: `{evidence["fitting_data_hash"]}`
- Judge: `deepseek/deepseek-v4-flash`
- Judge validation: **unvalidated by policy**
- Self-judging: source model identity hidden; reported separately
- Main rows: {evidence["rows"]["total"]}
- Abstain/invalid rows: {evidence["rows"]["abstain_or_invalid"]}
- Held-out diagnostic rows: {diagnostics["heldout_rows"]}
- Aggregate Experiment 1 spend: ${lock["aggregate_experiment_spend_usd"]:.6f}

## Parameter decision

- Curve supported: {attribution["curve_supported"]}
- Tau supported: {attribution["tau_supported"]}
- Abstention sensitivity stable: {evidence["abstention_decision_stable"]}
- Model bootstrap sign agreement: {evidence["bootstraps"]["model"]["sign_agreement"]:.1%}
- Dataset/case bootstrap sign agreement: {evidence["bootstraps"]["dataset_case"]["sign_agreement"]:.1%}
- Error floor: retained at `0.01`; repeat evidence remains provisional

## Limitations

The judge was deliberately not validated against human or synthetic labels. DeepSeek
judged its own evaluated responses with source identity hidden. Confidence was
diagnostic only. Promotion still requires explicit reviewer approval plus profile
hash, ETag, fallback, and rollback verification.
"""


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _write_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(
            value,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if path.exists() and path.read_bytes() != encoded:
        raise JudgeFittingError(f"Immutable artifact cannot be overwritten: {path}")
    path.write_bytes(encoded)


def _write_immutable_text(path: Path, value: str) -> None:
    encoded = value.encode("utf-8")
    if path.exists() and path.read_bytes() != encoded:
        raise JudgeFittingError(f"Immutable artifact cannot be overwritten: {path}")
    path.write_bytes(encoded)
