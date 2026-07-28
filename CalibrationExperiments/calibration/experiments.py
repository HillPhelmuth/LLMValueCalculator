from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from calibration.datasets.jsonl import JsonlDatasetAdapter
from calibration.datasets.sampling import SampleLock, freeze_sample
from calibration.manifest import DatasetConfig, ExperimentManifest
from calibration.models import CanonicalCase
from calibration.perturbations import PerturbationVariant, paired_work_groups


class ExperimentPlanError(ValueError):
    """Raised when a pre-registered experiment plan is incomplete."""


@dataclass(frozen=True, slots=True)
class DatasetBinding:
    dataset_id: str
    adapter: str
    task_family: str
    prompt_id: str
    scorers: tuple[str, ...]
    required_features: tuple[str, ...] = ()
    overlap_with_artificial_analysis: str = "not_assessed"
    published_metric: float | None = None


@dataclass(frozen=True, slots=True)
class ConditionSpec:
    condition_id: str
    treatment: str
    paired: bool = True
    requires_retrieval: bool = False
    requires_validation: bool = False


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    experiment_id: str
    title: str
    datasets: tuple[DatasetBinding, ...]
    conditions: tuple[ConditionSpec, ...]
    sample_min: int
    sample_max: int
    holdout_fraction: float
    required_features: tuple[str, ...] = ()
    repeat_count: int = 1
    model_holdout_fraction: float = 0.0
    overlap_sensitivity: bool = False
    no_hidden_reasoning: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.experiment_id or not self.datasets or not self.conditions:
            raise ExperimentPlanError("Experiment plans require datasets and conditions")
        if self.sample_min < 1 or self.sample_max < self.sample_min:
            raise ExperimentPlanError("Experiment sample bounds are invalid")
        if not 0 <= self.holdout_fraction < 1 or not 0 <= self.model_holdout_fraction <= 1:
            raise ExperimentPlanError("Holdout fractions are invalid")
        dataset_ids = [item.dataset_id for item in self.datasets]
        if len(dataset_ids) != len(set(dataset_ids)):
            raise ExperimentPlanError("Experiment dataset IDs must be unique")
        condition_ids = [item.condition_id for item in self.conditions]
        if len(condition_ids) != len(set(condition_ids)):
            raise ExperimentPlanError("Experiment condition IDs must be unique")
        feature_set = set(self.required_features)
        if not all(feature_set.issubset(item.required_features + self.required_features) for item in self.datasets):
            raise ExperimentPlanError("Dataset bindings do not expose required features")
        if self.repeat_count < 1:
            raise ExperimentPlanError("Experiment repeats must be positive")

    def to_json(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @property
    def plan_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_json(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def freeze(self, path: str | Path) -> str:
        document = {"plan": self.to_json(), "plan_hash": self.plan_hash}
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.read_text(encoding="utf-8") != json.dumps(document, sort_keys=True, indent=2):
            raise ExperimentPlanError(f"Frozen experiment plan cannot be overwritten: {destination}")
        destination.write_text(json.dumps(document, sort_keys=True, indent=2), encoding="utf-8")
        return self.plan_hash


class ReferenceTaskAdapter(JsonlDatasetAdapter):
    """Shared adapter for benchmark reference tasks with canonical JSONL rows."""

    adapter_version = "reference-task-1.0.0"

    def __init__(self, config: DatasetConfig, manifest_directory: Path, task_family: str) -> None:
        super().__init__(config, manifest_directory)
        self.task_family = task_family

    def metadata(self, case: CanonicalCase):  # type: ignore[no-untyped-def]
        features = super().metadata(case)
        feature_json = {**features.feature_json, "task_family": self.task_family}
        return type(features)(
            case_id=features.case_id,
            dataset_id=features.dataset_id,
            dataset_revision=features.dataset_revision,
            split=features.split,
            category=features.category,
            base_difficulty_stratum=features.base_difficulty_stratum,
            context_band=features.context_band,
            reasoning_depth=features.reasoning_depth,
            domain_band=features.domain_band,
            tool_horizon=features.tool_horizon,
            verifiability_band=features.verifiability_band,
            output_band=features.output_band,
            criticality_band=features.criticality_band,
            feature_json=feature_json,
        )


def experiment1_plan() -> ExperimentPlan:
    datasets = tuple(
        DatasetBinding(
            dataset_id=dataset_id,
            adapter="reference-task",
            task_family=family,
            prompt_id=f"experiment-1-{family}-v1",
            scorers=("answer_exact_match", "answer_token_f1"),
            required_features=("category", "verifiability_band", "output_band"),
            overlap_with_artificial_analysis=overlap,
        )
        for dataset_id, family, overlap in (
            ("mmlu_adjacent", "knowledge", "adjacent_components_only"),
            ("gpqa", "knowledge", "documented_component_overlap"),
            ("gsm8k", "math", "documented_component_overlap"),
            ("proofwriter", "logic", "adjacent_components_only"),
            ("pubmedqa", "domain", "documented_component_overlap"),
            ("legalbench", "domain", "adjacent_components_only"),
            ("finqa", "domain", "adjacent_components_only"),
        )
    )
    return ExperimentPlan(
        experiment_id="experiment-1",
        title="Exact-score intelligence curve and tau calibration",
        datasets=datasets,
        conditions=(ConditionSpec("baseline", "pinned_reference_prompt"),),
        sample_min=2000,
        sample_max=5000,
        holdout_fraction=0.20,
        model_holdout_fraction=0.25,
        required_features=("category", "verifiability_band", "output_band"),
        metadata={"overlap_documentation": "dataset binding overlap fields are frozen with the plan"},
    )


def build_experiment_plans() -> dict[str, ExperimentPlan]:
    plans = {
        "experiment-1": experiment1_plan(),
        "experiment-2": ExperimentPlan(
            "experiment-2", "Context and retrieval effects",
            tuple(DatasetBinding(item, "reference-task", "retrieval", "experiment-2-retrieval-v1", ("answer_exact_match", "answer_token_f1", "supporting_fact_recall"), ("context_band", "verifiability_band")) for item in ("longbench", "hotpotqa", "musique", "beir")),
            tuple(ConditionSpec(item, item, requires_retrieval=item in {"clean", "noisy", "very_large", "measured_retrieval"}) for item in ("oracle", "clean", "noisy", "very_large", "no_context", "measured_retrieval")),
            1000, 10000, 0.20, ("context_band", "verifiability_band", "evidence_position", "document_count", "retrieval_recall", "retrieval_ndcg"),
            metadata={"paired_conditions": True},
        ),
        "experiment-3": ExperimentPlan(
            "experiment-3", "Reasoning depth and branching",
            tuple(DatasetBinding(item, "reference-task", "reasoning", "experiment-3-reasoning-v1", ("answer_exact_match", "answer_token_f1"), ("reasoning_depth", "output_band")) for item in ("proofwriter", "musique", "apps")),
            tuple(ConditionSpec(f"depth-{level}", "reasoning_depth") for level in ("single_step", "shallow", "moderate", "deep")),
            800, 10000, 0.20, ("reasoning_depth", "hop_count", "branching_factor", "dependency_depth", "intermediate_state"),
        ),
        "experiment-4": ExperimentPlan(
            "experiment-4", "Domain and task-category residuals",
            tuple(DatasetBinding(item, "reference-task", "domain", "experiment-4-domain-v1", ("answer_exact_match", "field_level_comparison"), ("domain_band", "category")) for item in ("general_controls", "pubmedqa", "legalbench", "finqa", "cuad")),
            (ConditionSpec("reference", "domain_normalized"),), 800, 10000, 0.20, ("domain_band", "category", "context_band", "reasoning_depth"), overlap_sensitivity=True,
        ),
        "experiment-5": ExperimentPlan(
            "experiment-5", "Tool use and agentic critical exposure",
            tuple(DatasetBinding(item, "reference-task", "tool-use", "experiment-5-tools-v1", ("tool_state",), ("tool_horizon", "criticality_band")) for item in ("bfcl", "tau_bench", "bigcodebench")),
            tuple(ConditionSpec(f"tool-stratum-{index}", "tool_horizon") for index in range(1, 6)), 500, 10000, 0.20, ("tool_horizon", "dependency_depth", "turn_count", "recovery", "irreversible_state"), repeat_count=5,
        ),
        "experiment-6": ExperimentPlan(
            "experiment-6", "Structured-output burden and validators",
            tuple(DatasetBinding(item, "reference-task", "structured-output", "experiment-6-json-v1", ("schema_validity", "semantic_structured_value"), ("output_band", "criticality_band")) for item in ("jsonschemabench", "json_schema_test_suite", "cuad", "finqa", "bfcl")),
            tuple(ConditionSpec(item, item, requires_validation=validation) for item, validation in (("free_text", False), ("prompted_json", False), ("constrained_decoding", False), ("free_text_validated", True), ("prompted_json_validated", True), ("constrained_decoding_validated", True))),
            500, 10000, 0.20, ("output_band", "schema_validity", "criticality_band"),
        ),
        "experiment-7": ExperimentPlan(
            "experiment-7", "Retry dependence and systematic error floor",
            tuple(DatasetBinding(item, "reference-task", "retry", "experiment-7-retry-v1", ("answer_exact_match", "tool_state", "schema_validity")) for item in ("proofwriter", "hotpotqa", "bfcl", "json_extraction", "apps")),
            tuple(ConditionSpec(item, item, paired=False) for item in ("same_prompt", "repair_feedback", "changed_evidence_or_tool_state")), 500, 10000, 0.20, repeat_count=5,
        ),
        "experiment-8": ExperimentPlan(
            "experiment-8", "Partial value and failure severity",
            tuple(DatasetBinding(item, "reference-task", "partial-value", "experiment-8-quality-v1", ("code_execution", "field_level_comparison", "semantic_structured_value"), ("criticality_band",)) for item in ("apps", "bigcodebench", "cuad", "summeval", "frank")),
            (ConditionSpec("reference", "failure-severity"),), 500, 10000, 0.20, ("criticality_band", "quality_share", "critical_share"),
        ),
    }
    for plan in plans.values():
        plan.validate()
    return plans


def write_experiment_plan_registry(path: str | Path) -> str:
    """Freeze all pre-registered plans as one content-addressed registry."""
    plans = build_experiment_plans()
    document = {"plans": {key: value.to_json() for key, value in sorted(plans.items())}}
    encoded = json.dumps(document, sort_keys=True, indent=2)
    registry_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    frozen = {"registry_hash": registry_hash, **document}
    serialized = json.dumps(frozen, sort_keys=True, indent=2)
    if destination.exists() and destination.read_text(encoding="utf-8") != serialized:
        raise ExperimentPlanError(f"Frozen plan registry cannot be overwritten: {destination}")
    destination.write_text(serialized, encoding="utf-8")
    return registry_hash


def freeze_experiment1_sample(
    cases: Iterable[CanonicalCase], output_directory: str | Path, *, seed: int
) -> SampleLock:
    rows = tuple(cases)
    if not 2000 <= len(rows) <= 5000:
        raise ExperimentPlanError("Experiment 1 requires a 2,000 to 5,000 case panel")
    return freeze_sample(
        rows,
        dataset_id="experiment-1-panel",
        dataset_revision="frozen-reference-panel",
        split="validation",
        sample_size=len(rows),
        seed=seed,
        output_directory=output_directory,
        holdout_fraction=experiment1_plan().holdout_fraction,
    )


def validate_experiment1_manifest(manifest: ExperimentManifest) -> None:
    if manifest.generation.temperature != 0:
        raise ExperimentPlanError("Experiment 1 main cells require temperature zero")
    if len(manifest.conditions) != 1 or manifest.conditions[0] != "baseline":
        raise ExperimentPlanError("Experiment 1 requires one pinned baseline condition")
    if manifest.generation.repeats != 1:
        raise ExperimentPlanError("Experiment 1 main cells use one deterministic attempt")
    if manifest.holdouts.model_fraction < 0.25:
        raise ExperimentPlanError("Experiment 1 requires at least 25 percent model holdout")


@dataclass(frozen=True, slots=True)
class CoverageReport:
    expected_cells: int
    observed_cells: int
    missing_cells: tuple[str, ...]
    missing_fraction: float
    integrity_errors: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.integrity_errors and self.missing_fraction <= 0.02


def validate_coverage(
    expected_cells: Iterable[str], observed_cells: Iterable[str], *, integrity_errors: Iterable[str] = ()
) -> CoverageReport:
    expected = set(expected_cells)
    observed = set(observed_cells)
    missing = tuple(sorted(expected - observed))
    return CoverageReport(
        expected_cells=len(expected),
        observed_cells=len(expected & observed),
        missing_cells=missing,
        missing_fraction=len(missing) / len(expected) if expected else 1.0,
        integrity_errors=tuple(integrity_errors),
    )


@dataclass(frozen=True, slots=True)
class RetrievalObservation:
    case_id: str
    condition_id: str
    token_count: int
    evidence_position: float | None
    document_count: int
    retrieved_ids: tuple[str, ...]
    relevant_ids: tuple[str, ...]
    answer_coverage: float
    exact_match: bool | None = None
    token_f1: float | None = None

    @property
    def recall(self) -> float:
        relevant = set(self.relevant_ids)
        return len(relevant & set(self.retrieved_ids)) / len(relevant) if relevant else 0.0

    @property
    def ndcg(self) -> float:
        relevant = set(self.relevant_ids)
        if not relevant:
            return 0.0
        dcg = sum((1.0 if item in relevant else 0.0) / math.log2(index + 2) for index, item in enumerate(self.retrieved_ids))
        ideal = sum(1.0 / math.log2(index + 2) for index in range(min(len(relevant), len(self.retrieved_ids))))
        return dcg / ideal if ideal else 0.0


def build_retrieval_conditions(
    case: CanonicalCase, documents: Sequence[Mapping[str, Any]], *, seed: int
) -> tuple[dict[str, Any], ...]:
    rng = random.Random(seed)
    docs = [dict(document) for document in documents]
    noisy = list(docs)
    rng.shuffle(noisy)
    return tuple(
        {"condition_id": condition, "documents": payload, "paired_case_id": case.case_id}
        for condition, payload in (
            ("oracle", docs),
            ("clean", docs),
            ("noisy", noisy),
            ("very_large", docs + [{"id": f"distractor-{index}", "text": "noise"} for index in range(100)]),
            ("no_context", []),
            ("measured_retrieval", docs[: max(1, len(docs) // 2)]),
        )
    )


def map_reasoning_features(metadata: Mapping[str, Any]) -> dict[str, Any]:
    depth = int(metadata.get("proofwriter_depth", metadata.get("hop_count", 1)))
    branching = int(metadata.get("branching_factor", 1))
    return {
        "reasoning_depth": "single_step" if depth <= 1 else "shallow" if depth <= 2 else "moderate" if depth <= 4 else "deep",
        "hop_count": depth,
        "branching_factor": branching,
        "dependency_depth": int(metadata.get("dependency_depth", depth)),
        "intermediate_state": bool(metadata.get("requires_intermediate_state", depth > 1)),
    }


def matched_reasoning_sample(cases: Iterable[CanonicalCase], *, seed: int) -> tuple[CanonicalCase, ...]:
    rows = list(cases)
    groups: dict[tuple[str, str], list[CanonicalCase]] = {}
    for case in rows:
        features = map_reasoning_features(case.metadata)
        key = (str(case.metadata.get("surface_length_band", "unknown")), features["reasoning_depth"])
        groups.setdefault(key, []).append(case)
    rng = random.Random(seed)
    selected: list[CanonicalCase] = []
    for group in groups.values():
        rng.shuffle(group)
        selected.extend(group[: max(1, min(len(group), 100))])
    return tuple(sorted(selected, key=lambda case: case.case_id))


@dataclass(frozen=True, slots=True)
class ToolTrajectory:
    case_id: str
    expected_calls: tuple[dict[str, Any], ...]
    actual_calls: tuple[dict[str, Any], ...]
    dependency_violations: int
    recovery_attempts: int
    policy_violations: int
    final_state: dict[str, Any]
    expected_state: dict[str, Any]
    turn_count: int
    critical_wrong_state: bool

    def validate(self) -> None:
        if self.turn_count < 0 or min(self.dependency_violations, self.recovery_attempts, self.policy_violations) < 0:
            raise ExperimentPlanError("Tool trajectory counts must be non-negative")

    def to_json(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


def schedule_tool_repeats(trajectory: ToolTrajectory, repeats: int = 5) -> tuple[int, ...]:
    if repeats < 5:
        raise ExperimentPlanError("Stochastic tool cases require at least five repeats")
    return tuple(range(repeats))


@dataclass(frozen=True, slots=True)
class StructuredObservation:
    case_id: str
    condition_id: str
    raw_output: str
    parseable: bool
    schema_valid: bool
    exact_values_correct: bool
    semantic_success: bool
    critical: bool
    validator_decision: bool | None
    validator_correct: bool | None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def validate_structured_observation(observation: StructuredObservation) -> None:
    if observation.validator_decision is not None and observation.validator_correct is None:
        raise ExperimentPlanError("Validator decisions require an independently scored correctness result")


def paired_variants_for_execution(
    cases: tuple[CanonicalCase, ...], variants: tuple[PerturbationVariant, ...]
) -> tuple[tuple[str, tuple[CanonicalCase, ...]], ...]:
    return paired_work_groups(cases, variants)
