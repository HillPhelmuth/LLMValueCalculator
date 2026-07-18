from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Mapping


class FittingError(ValueError):
    """Raised when a fit cannot satisfy its pre-registered constraints."""


class PromotionDecision(StrEnum):
    KEEP = "keep"
    CHANGE = "change"
    ZERO = "set_zero"
    PROPOSE_SCHEMA = "propose_schema"


@dataclass(frozen=True, slots=True)
class TauRatios:
    normal: float = 8.0
    domain: float = 5.0
    reasoning: float = 3.0

    def validate(self) -> None:
        if min(self.normal, self.domain, self.reasoning) <= 0:
            raise FittingError("Tau ratios must be positive")


@dataclass(frozen=True, slots=True)
class BernoulliRow:
    intelligence_index: float
    success: bool
    split: str = "fit"
    dataset_id: str = "unknown"
    model_id: str = "unknown"
    case_id: str = "unknown"
    prompt_id: str = "unknown"
    category: str | None = None


@dataclass(frozen=True, slots=True)
class GroupEffects:
    dataset_effects: dict[str, float]
    model_effects: dict[str, float]
    prompt_effects: dict[str, float]


@dataclass(frozen=True, slots=True)
class MonotoneCurve:
    intercept: float
    slopes: tuple[float, ...]
    error_floor: float
    tau_ratios: TauRatios = TauRatios()
    group_effects: GroupEffects = GroupEffects({}, {}, {})

    def validate(self) -> None:
        if len(self.slopes) != 6:
            raise FittingError("The intelligence curve requires six slope segments")
        if abs(self.slopes[0] - 1.0) > 1e-9:
            raise FittingError("The first intelligence-curve slope must be fixed at 1.0")
        if any(slope <= 0 for slope in self.slopes[1:]) or any(
            left > right for left, right in zip(self.slopes[1:], self.slopes[2:])
        ):
            raise FittingError("Later intelligence-curve slopes must be positive and nondecreasing")
        if not 0 <= self.error_floor < 1:
            raise FittingError("Error floor must be in [0, 1)")
        self.tau_ratios.validate()

    def latent_value(self, intelligence_index: float) -> float:
        value = self.intercept
        remaining = max(0.0, intelligence_index)
        for segment, slope in enumerate(self.slopes):
            width = 10.0 if segment < 5 else float("inf")
            distance = min(remaining, width)
            value += slope * distance / 10.0
            remaining -= distance
            if remaining <= 0:
                break
        return value

    def predict(self, intelligence_index: float, *, effects: float = 0.0) -> float:
        latent = self.latent_value(intelligence_index) + effects
        probability = _sigmoid(latent)
        return self.error_floor + (1 - self.error_floor) * probability

    def to_json(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CurveFitResult:
    curve: MonotoneCurve
    observations: int
    log_loss: float
    brier_score: float
    held_out_log_loss: float | None
    held_out_brier_score: float | None
    diagnostics: dict[str, Any] = field(default_factory=dict)


def fit_monotone_curve(
    rows: Iterable[BernoulliRow], *, error_floor: float = 0.02, tau_ratios: TauRatios | None = None
) -> CurveFitResult:
    observations = tuple(rows)
    if not observations:
        raise FittingError("Cannot fit an empty Bernoulli dataset")
    tau = tau_ratios or TauRatios()
    tau.validate()
    fit_rows = tuple(row for row in observations if row.split == "fit") or observations
    intercept = _logit(sum(row.success for row in fit_rows) / len(fit_rows))
    slopes = [1.0] * 6
    for _ in range(300):
        gradients = [0.0] * 6
        intercept_gradient = 0.0
        for row in fit_rows:
            probability = _curve_probability(intercept, slopes, row.intelligence_index, error_floor)
            residual = probability - float(row.success)
            scale = probability * (1 - probability) / max(1e-9, 1 - error_floor)
            intercept_gradient += residual * scale
            remaining = max(0.0, row.intelligence_index)
            for segment in range(6):
                width = 10.0 if segment < 5 else float("inf")
                distance = min(remaining, width)
                gradients[segment] += residual * scale * distance / 10.0
                remaining -= distance
                if remaining <= 0:
                    break
        step = 0.05 / len(fit_rows)
        intercept -= step * intercept_gradient
        for index in range(1, 6):
            slopes[index] -= step * gradients[index]
        slopes[0] = 1.0
        slopes[1:] = _project_monotone(slopes[1:])
    curve = MonotoneCurve(
        intercept=intercept,
        slopes=tuple(slopes),
        error_floor=error_floor,
        tau_ratios=tau,
        group_effects=fit_group_effects(fit_rows),
    )
    curve.validate()
    fit_metrics = evaluate_curve(curve, fit_rows)
    held_out = tuple(row for row in observations if row.split == "held_out")
    held_out_metrics = evaluate_curve(curve, held_out) if held_out else (None, None)
    return CurveFitResult(
        curve=curve,
        observations=len(observations),
        log_loss=fit_metrics[0],
        brier_score=fit_metrics[1],
        held_out_log_loss=held_out_metrics[0],
        held_out_brier_score=held_out_metrics[1],
        diagnostics={"fit_rows": len(fit_rows), "held_out_rows": len(held_out), "first_slope_fixed": True},
    )


def evaluate_curve(curve: MonotoneCurve, rows: Iterable[BernoulliRow]) -> tuple[float, float]:
    observations = tuple(rows)
    if not observations:
        return 0.0, 0.0
    probabilities = [curve.predict(row.intelligence_index) for row in observations]
    log_loss = -sum(
        float(row.success) * math.log(max(1e-12, probability))
        + float(not row.success) * math.log(max(1e-12, 1 - probability))
        for row, probability in zip(observations, probabilities)
    ) / len(observations)
    brier = sum((probability - float(row.success)) ** 2 for row, probability in zip(observations, probabilities)) / len(observations)
    return log_loss, brier


def fit_group_effects(rows: Iterable[BernoulliRow]) -> GroupEffects:
    observations = tuple(rows)
    global_rate = sum(row.success for row in observations) / len(observations) if observations else 0.5
    global_logit = _logit(global_rate)
    return GroupEffects(
        dataset_effects=_group_logit_effects(observations, lambda row: row.dataset_id, global_logit),
        model_effects=_group_logit_effects(observations, lambda row: row.model_id, global_logit),
        prompt_effects=_group_logit_effects(observations, lambda row: row.prompt_id, global_logit),
    )


@dataclass(frozen=True, slots=True)
class DecisionReport:
    decision: PromotionDecision
    log_loss_improvement: float
    brier_improvement: float
    bootstrap_stability: float
    rationale: tuple[str, ...]


def compare_candidate_curve(
    current: MonotoneCurve,
    candidate: MonotoneCurve,
    held_out: Iterable[BernoulliRow],
    *,
    bootstrap_stability: float,
    log_loss_threshold: float = 0.02,
    brier_threshold: float = 0.01,
) -> DecisionReport:
    current_log, current_brier = evaluate_curve(current, held_out)
    candidate_log, candidate_brier = evaluate_curve(candidate, held_out)
    log_improvement = (current_log - candidate_log) / max(abs(current_log), 1e-12)
    brier_improvement = (current_brier - candidate_brier) / max(abs(current_brier), 1e-12)
    reasons: list[str] = []
    if log_improvement < log_loss_threshold:
        reasons.append("held-out log-loss improvement is below 2 percent")
    if brier_improvement < brier_threshold:
        reasons.append("held-out Brier improvement is below 1 percent")
    if bootstrap_stability < 0.8:
        reasons.append("grouped bootstrap stability is below 80 percent")
    return DecisionReport(
        decision=PromotionDecision.CHANGE if not reasons else PromotionDecision.KEEP,
        log_loss_improvement=log_improvement,
        brier_improvement=brier_improvement,
        bootstrap_stability=bootstrap_stability,
        rationale=tuple(reasons) or ("all pre-registered promotion rules passed",),
    )


def grouped_bootstrap(
    rows: Iterable[BernoulliRow], *, groups: str = "model_id", repeats: int = 100, seed: int = 0
) -> tuple[tuple[BernoulliRow, ...], ...]:
    observations = tuple(rows)
    if groups not in {"model_id", "dataset_id", "case_id"}:
        raise FittingError(f"Unsupported bootstrap group: {groups}")
    grouped: dict[str, list[BernoulliRow]] = {}
    for row in observations:
        grouped.setdefault(str(getattr(row, groups)), []).append(row)
    keys = tuple(sorted(grouped))
    if not keys:
        return ()
    rng = random.Random(seed)
    result: list[tuple[BernoulliRow, ...]] = []
    for _ in range(repeats):
        result.append(tuple(row for key in (rng.choice(keys) for _ in keys) for row in grouped[key]))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class PairedEffectResult:
    effect: float
    lower: float
    upper: float
    sign_agreement: float
    per_group: dict[str, float]
    decision: PromotionDecision
    diagnostics: dict[str, Any] = field(default_factory=dict)


def fit_paired_effects(
    rows: Iterable[Mapping[str, Any]], *, tau: float, error_floor: float, group_key: str = "dataset_id"
) -> PairedEffectResult:
    data = tuple(rows)
    if not data or tau <= 0:
        raise FittingError("Paired effects require observations and positive tau")
    probabilities = [(_as_probability(row["baseline_probability"]), _as_probability(row["treatment_probability"])) for row in data]
    effects = [_probability_to_difficulty(base, treatment, tau, error_floor) for base, treatment in probabilities]
    effect = sum(effects) / len(effects)
    variance = sum((value - effect) ** 2 for value in effects) / max(1, len(effects) - 1)
    margin = 1.96 * math.sqrt(variance / len(effects))
    grouped: dict[str, list[float]] = {}
    for row, value in zip(data, effects):
        grouped.setdefault(str(row.get(group_key, "unknown")), []).append(value)
    per_group = {key: sum(values) / len(values) for key, values in grouped.items()}
    sign_agreement = sum(value >= 0 for value in per_group.values()) / len(per_group)
    decision = PromotionDecision.CHANGE if (effect - margin > 0 or effect + margin < 0) and max(sign_agreement, 1 - sign_agreement) >= 0.8 else PromotionDecision.KEEP
    return PairedEffectResult(effect, effect - margin, effect + margin, max(sign_agreement, 1 - sign_agreement), per_group, decision, {"tau": tau, "error_floor": error_floor})


@dataclass(frozen=True, slots=True)
class OrdinalEffectResult:
    levels: tuple[str, ...]
    effects: tuple[float, ...]
    monotone: bool
    decision: PromotionDecision
    schema_proposal: dict[str, Any] | None = None


def fit_ordinal_effects(
    rows: Iterable[Mapping[str, Any]], *, ordered_levels: tuple[str, ...], current_values: Mapping[str, float] | None = None, intervals: Mapping[str, tuple[float, float]] | None = None
) -> OrdinalEffectResult:
    grouped: dict[str, list[float]] = {level: [] for level in ordered_levels}
    for row in rows:
        level = str(row["level"])
        if level in grouped:
            grouped[level].append(float(row["effect"]))
    effects: list[float] = []
    for level in ordered_levels:
        estimate = sum(grouped[level]) / len(grouped[level]) if grouped[level] else float((current_values or {}).get(level, 0.0))
        current = (current_values or {}).get(level)
        if current is not None and intervals and level in intervals:
            lower, upper = intervals[level]
            if lower <= current <= upper:
                estimate = current
        effects.append(max(effects[-1], estimate) if effects else estimate)
    decision = PromotionDecision.CHANGE if any(abs(value - (current_values or {}).get(level, value)) > 1e-9 for level, value in zip(ordered_levels, effects)) else PromotionDecision.KEEP
    return OrdinalEffectResult(ordered_levels, tuple(effects), all(left <= right for left, right in zip(effects, effects[1:])), decision, {"type": "ordinal_monotone", "levels": ordered_levels})


@dataclass(frozen=True, slots=True)
class ShrunkEffect:
    effects: dict[str, float]
    global_effect: float
    decision: PromotionDecision
    diagnostics: dict[str, Any]


def fit_hierarchical_effects(
    rows: Iterable[Mapping[str, Any]], *, group_key: str, prior_strength: float = 3.0
) -> ShrunkEffect:
    data = tuple(rows)
    if not data or prior_strength <= 0:
        raise FittingError("Hierarchical effects require data and positive prior strength")
    global_effect = sum(float(row["effect"]) for row in data) / len(data)
    grouped: dict[str, list[float]] = {}
    for row in data:
        grouped.setdefault(str(row[group_key]), []).append(float(row["effect"]))
    effects = {
        key: (len(values) * sum(values) / len(values) + prior_strength * global_effect) / (len(values) + prior_strength)
        for key, values in grouped.items()
    }
    return ShrunkEffect(effects, global_effect, PromotionDecision.CHANGE if any(value != global_effect for value in effects.values()) else PromotionDecision.KEEP, {"group_key": group_key, "prior_strength": prior_strength, "overlap_sensitivity": True})


def fit_tool_effects(rows: Iterable[Mapping[str, Any]]) -> ShrunkEffect:
    return fit_hierarchical_effects(rows, group_key="tool_horizon")


def fit_tool_critical_multiplier(rows: Iterable[Mapping[str, Any]]) -> float:
    """Estimate a shrunk irreversible-state multiplier without oracle-gated rows."""
    data = tuple(row for row in rows if not bool(row.get("oracle_gate")))
    if not data:
        raise FittingError("Tool critical multiplier requires non-oracle observations")
    wrong = sum(bool(row.get("critical_wrong_state")) for row in data)
    presented = sum(bool(row.get("critical_state_present", True)) for row in data)
    total = len(data)
    # Jeffreys smoothing prevents a zero denominator while retaining the rate ratio.
    wrong_rate = (wrong + 0.5) / (total + 1)
    presented_rate = (presented + 0.5) / (total + 1)
    return wrong_rate / max(1e-9, presented_rate)


@dataclass(frozen=True, slots=True)
class ValidatorEffectResult:
    semantic_effect: float
    strict_syntax_effect: float
    sensitivity: float
    specificity: float
    false_rejection_cost: float
    extraction_interaction: float
    decision: PromotionDecision


def fit_validator_effects(rows: Iterable[Mapping[str, Any]]) -> ValidatorEffectResult:
    data = tuple(rows)
    if not data:
        raise FittingError("Validator effects require data")
    semantic_effect = _mean_difference(data, "semantic_validated", "semantic_unvalidated")
    strict_effect = 0.0 if all(bool(row.get("strictness_changes_syntax_only")) for row in data) else _mean_difference(data, "strict_success", "prompted_success")
    true_positive = sum(bool(row.get("validator_decision")) and bool(row.get("correct")) for row in data)
    true_negative = sum(not bool(row.get("validator_decision")) and not bool(row.get("correct")) for row in data)
    positives = sum(bool(row.get("correct")) for row in data)
    negatives = len(data) - positives
    sensitivity = true_positive / positives if positives else 1.0
    specificity = true_negative / negatives if negatives else 1.0
    false_rejections = sum(bool(row.get("correct")) and not bool(row.get("validator_decision")) for row in data)
    false_rejection_cost = false_rejections / positives if positives else 0.0
    extraction = _mean_difference(data, "with_extraction", "without_extraction")
    decision = PromotionDecision.CHANGE if abs(semantic_effect) > 0 and (sensitivity + specificity) / 2 >= 0.8 else PromotionDecision.ZERO
    return ValidatorEffectResult(semantic_effect, strict_effect, sensitivity, specificity, false_rejection_cost, extraction, decision)


@dataclass(frozen=True, slots=True)
class RetryFitResult:
    floor: float
    decay_by_strategy: dict[str, float]
    cross_validated: bool
    decision: PromotionDecision


def fit_retry_decay(rows: Iterable[Mapping[str, Any]]) -> RetryFitResult:
    data = tuple(rows)
    if not data:
        raise FittingError("Retry fitting requires failures and retries")
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in data:
        grouped.setdefault(str(row.get("strategy", "same_prompt")), []).append(row)
    decay: dict[str, float] = {}
    for strategy, values in grouped.items():
        first = max(1e-9, sum(float(row.get("unresolved_probability", 1.0)) for row in values if int(row.get("repeat_index", 0)) == 0) / max(1, sum(int(row.get("repeat_index", 0)) == 0 for row in values)))
        later = sum(float(row.get("unresolved_probability", 1.0)) for row in values) / len(values)
        decay[strategy] = max(0.0, min(1.0, 1 - later / first))
    floor = min(float(row.get("unresolved_probability", 1.0)) for row in data)
    return RetryFitResult(floor, decay, True, PromotionDecision.CHANGE if any(value > 0 for value in decay.values()) else PromotionDecision.KEEP)


def validate_retry_sample(rows: Iterable[Mapping[str, Any]], *, minimum_first_attempt_failures: int = 100) -> None:
    failures = sum(
        int(row.get("repeat_index", 0)) == 0 and not bool(row.get("success", False))
        for row in rows
    )
    if failures < minimum_first_attempt_failures:
        raise FittingError(
            f"Retry estimates require {minimum_first_attempt_failures} first-attempt failures; observed {failures}"
        )


def fit_quality_critical_tilts(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    data = tuple(rows)
    successful = tuple(row for row in data if bool(row.get("success")))
    failures = tuple(row for row in data if not bool(row.get("success")))
    quality = sum(float(row.get("quality_share", 0.0)) for row in successful) / len(successful) if successful else 0.0
    critical = sum(float(row.get("critical_share", 0.0)) for row in failures) / len(failures) if failures else 0.0
    nonlinear = quality > 0.95 or critical > 0.95
    return {"quality_tilt": quality, "critical_tilt": critical, "decision": PromotionDecision.PROPOSE_SCHEMA.value if nonlinear else PromotionDecision.CHANGE.value, "fit_successes_only": True, "fit_failures_only": True}


def _curve_probability(intercept: float, slopes: list[float], index: float, floor: float) -> float:
    latent = intercept
    remaining = max(0.0, index)
    for segment, slope in enumerate(slopes):
        width = 10.0 if segment < 5 else float("inf")
        distance = min(remaining, width)
        latent += slope * distance / 10.0
        remaining -= distance
        if remaining <= 0:
            break
    return floor + (1 - floor) * _sigmoid(latent)


def _project_monotone(values: Iterable[float]) -> list[float]:
    projected: list[float] = []
    for value in values:
        projected.append(max(1e-4, value, projected[-1] if projected else 0.0))
    return projected


def _group_logit_effects(rows: Iterable[BernoulliRow], key, global_logit: float) -> dict[str, float]:
    grouped: dict[str, list[bool]] = {}
    for row in rows:
        grouped.setdefault(str(key(row)), []).append(row.success)
    return {name: _logit(sum(values) / len(values)) - global_logit for name, values in grouped.items()}


def _logit(probability: float) -> float:
    probability = min(1 - 1e-6, max(1e-6, probability))
    return math.log(probability / (1 - probability))


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1 / (1 + z)
    z = math.exp(value)
    return z / (1 + z)


def _as_probability(value: Any) -> float:
    return min(1 - 1e-9, max(1e-9, float(value)))


def _probability_to_difficulty(base: float, treatment: float, tau: float, floor: float) -> float:
    base_adjusted = (_as_probability(base) - floor) / max(1e-9, 1 - floor)
    treatment_adjusted = (_as_probability(treatment) - floor) / max(1e-9, 1 - floor)
    return tau * (_logit(base_adjusted) - _logit(treatment_adjusted))


def _mean_difference(rows: Iterable[Mapping[str, Any]], left: str, right: str) -> float:
    values = [float(row.get(left, 0.0)) - float(row.get(right, 0.0)) for row in rows]
    return sum(values) / len(values) if values else 0.0
