from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path
from typing import Any, Callable, Iterable


class RegressionError(ValueError):
    """Raised when a profile regression is unsafe to compare or promote."""


@dataclass(frozen=True, slots=True)
class UseCaseInputs:
    category: str
    difficulty_band: str
    guardrail_combination: str
    risk_level: str
    retry_mode: str
    economic_regime: str


@dataclass(frozen=True, slots=True)
class RecommendationSnapshot:
    model_id: str
    eligible: bool
    rank: int
    success_rate: float
    critical_risk: float
    cost_usd: float
    expected_value_usd: float
    profile_hash: str


@dataclass(frozen=True, slots=True)
class RecommendationDiff:
    scenario: UseCaseInputs
    model_id: str
    baseline: RecommendationSnapshot
    candidate: RecommendationSnapshot
    deltas: dict[str, float]
    attribution: tuple[str, ...]
    material: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "scenario": asdict(self.scenario),
            "model_id": self.model_id,
            "baseline": asdict(self.baseline),
            "candidate": asdict(self.candidate),
            "deltas": self.deltas,
            "attribution": list(self.attribution),
            "material": self.material,
        }


def build_scenario_matrix(
    categories: Iterable[str],
    difficulty_bands: Iterable[str],
    guardrails: Iterable[str],
    risk_levels: Iterable[str],
    retry_modes: Iterable[str],
    economic_regimes: Iterable[str],
) -> tuple[UseCaseInputs, ...]:
    return tuple(
        UseCaseInputs(*values)
        for values in product(categories, difficulty_bands, guardrails, risk_levels, retry_modes, economic_regimes)
    )


Evaluator = Callable[[UseCaseInputs, str, str], RecommendationSnapshot]


def compare_profiles(
    scenarios: Iterable[UseCaseInputs],
    model_ids: Iterable[str],
    *,
    baseline_hash: str,
    candidate_hash: str,
    evaluator: Evaluator,
    thresholds: dict[str, float] | None = None,
) -> tuple[RecommendationDiff, ...]:
    limits = thresholds or {"success_rate": 0.20, "critical_risk": 0.20, "cost_usd": 0.50, "expected_value_usd": 0.50}
    diffs: list[RecommendationDiff] = []
    for scenario in scenarios:
        for model_id in model_ids:
            baseline = evaluator(scenario, model_id, baseline_hash)
            candidate = evaluator(scenario, model_id, candidate_hash)
            deltas = {
                "success_rate": candidate.success_rate - baseline.success_rate,
                "critical_risk": candidate.critical_risk - baseline.critical_risk,
                "cost_usd": candidate.cost_usd - baseline.cost_usd,
                "expected_value_usd": candidate.expected_value_usd - baseline.expected_value_usd,
            }
            material = (
                baseline.eligible != candidate.eligible
                or baseline.rank != candidate.rank
                or any(abs(value) > limits[key] for key, value in deltas.items())
            )
            changed_fields = tuple(key for key, value in deltas.items() if abs(value) > 0)
            if baseline.eligible != candidate.eligible:
                changed_fields += ("eligibility",)
            if baseline.rank != candidate.rank:
                changed_fields += ("rank",)
            diffs.append(RecommendationDiff(scenario, model_id, baseline, candidate, deltas, changed_fields, material))
    return tuple(diffs)


def validate_regression_diffs(diffs: Iterable[RecommendationDiff], *, max_material_fraction: float = 0.25) -> None:
    rows = tuple(diffs)
    if not rows:
        raise RegressionError("Regression suite produced no comparisons")
    material_fraction = sum(item.material for item in rows) / len(rows)
    if material_fraction > max_material_fraction:
        raise RegressionError(
            f"Candidate profile changes {material_fraction:.1%} of scenarios, above the {max_material_fraction:.1%} material-change limit"
        )
    if any(item.material and not item.attribution for item in rows):
        raise RegressionError("Every material recommendation change requires attribution")


def write_regression_report(diffs: Iterable[RecommendationDiff], path: str | Path) -> Path:
    rows = tuple(diffs)
    validate_regression_diffs(rows)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps([row.to_json() for row in rows], sort_keys=True, indent=2), encoding="utf-8")
    return destination
