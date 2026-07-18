from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator

from calibration.schema import SCHEMA_VERSION, validate_record


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PromptLock(StrictModel):
    prompt_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)


class RoutingConfig(StrictModel):
    policy: str = "default"
    provider_order: tuple[str, ...] = ()
    allow_fallbacks: bool = False
    require_parameters: bool = False
    data_collection: str = "deny"
    zdr: bool = True
    quantization: str | None = None
    endpoint: str | None = None


class BudgetConfig(StrictModel):
    max_usd: float | None = Field(default=None, ge=0)
    max_requests: PositiveInt | None = None
    max_tokens: PositiveInt | None = None
    max_retries: int = Field(default=0, ge=0)
    approval_artifact: str | None = None


class HoldoutConfig(StrictModel):
    dataset_ids: tuple[str, ...] = ()
    model_fraction: float = Field(default=0.0, ge=0, le=1)
    case_ids: tuple[str, ...] = ()


class RetryConfig(StrictModel):
    transport_retries: int = Field(default=2, ge=0)
    experimental_retries: int = Field(default=0, ge=0)
    backoff_seconds: float = Field(default=0.25, ge=0)


class ContainerConfig(StrictModel):
    image: str | None = None
    digests: tuple[str, ...] = ()


class FittingConfig(StrictModel):
    estimator: str = "unconfigured"
    seed: int = 0
    data_revision: str | None = None


class ScorerLock(StrictModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    implementation_hash: str = Field(min_length=1)


class DatasetConfig(StrictModel):
    adapter: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    split: str = Field(min_length=1)
    sample_seed: int
    sample_ids: tuple[str, ...] = ()
    options: dict[str, Any] = Field(default_factory=dict)


class ModelConfig(StrictModel):
    catalog_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    provider_model: str = Field(min_length=1)
    aa_snapshot: str = Field(min_length=1)
    aa_intelligence_index: float | None = None

    @field_validator("aa_snapshot", mode="before")
    @classmethod
    def normalize_yaml_date(cls, value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return value


class GenerationConfig(StrictModel):
    temperature: float = Field(ge=0)
    max_output_tokens: PositiveInt
    reasoning_effort: str | None = None
    repeats: PositiveInt = 1


class ExperimentManifest(StrictModel):
    schema_version: str = SCHEMA_VERSION
    experiment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    dataset: DatasetConfig
    models: tuple[ModelConfig, ...]
    generation: GenerationConfig
    prompt_version: str = Field(min_length=1)
    conditions: tuple[str, ...]
    scorers: tuple[str, ...]
    prompt_hashes: tuple[str, ...] = ()
    scorer_versions: dict[str, str] = Field(default_factory=dict)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    holdouts: HoldoutConfig = Field(default_factory=HoldoutConfig)
    retries: RetryConfig = Field(default_factory=RetryConfig)
    containers: ContainerConfig = Field(default_factory=ContainerConfig)
    fitting: FittingConfig = Field(default_factory=FittingConfig)
    prompts: tuple[PromptLock, ...] = ()
    scorer_locks: tuple[ScorerLock, ...] = ()

    @field_validator("models", "conditions", "scorers")
    @classmethod
    def require_non_empty_unique_values(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        if not value:
            raise ValueError("must contain at least one value")

        comparable = [
            item.catalog_id if isinstance(item, ModelConfig) else item
            for item in value
        ]
        if len(comparable) != len(set(comparable)):
            raise ValueError("values must be unique")

        return value

    @property
    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @property
    def manifest_hash(self) -> str:
        return hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()

    def resolved(self, sample_ids: tuple[str, ...]) -> dict[str, Any]:
        """Resolve implicit locks into a canonical, reviewable run manifest."""
        if not sample_ids:
            raise ValueError("A resolved manifest must contain at least one sample ID")
        if self.dataset.sample_ids and tuple(self.dataset.sample_ids) != tuple(sample_ids):
            raise ValueError(
                "Dataset sample_ids do not match the cases selected for this run"
            )
        if self.holdouts.case_ids and not set(self.holdouts.case_ids).issubset(sample_ids):
            raise ValueError("Holdout case_ids must be present in the resolved sample")

        document = self.model_dump(mode="json")
        document["resolved"] = True
        document["source_manifest_hash"] = self.manifest_hash
        from calibration.providers.routing import routing_manifest_fields

        document["routing"] = routing_manifest_fields(self.routing)
        document["dataset"]["sample_ids"] = list(sample_ids)
        document["prompts"] = [
            prompt.model_dump(mode="json") for prompt in self.prompts
        ] or [
            {
                "prompt_id": self.prompt_version,
                "version": self.prompt_version,
                "content_hash": hashlib.sha256(
                    self.prompt_version.encode("utf-8")
                ).hexdigest(),
            }
        ]
        document["scorer_locks"] = [
            scorer.model_dump(mode="json") for scorer in self.scorer_locks
        ] or [
            {
                "name": name,
                "version": self.scorer_versions.get(name, "registry"),
                "implementation_hash": hashlib.sha256(name.encode("utf-8")).hexdigest(),
            }
            for name in self.scorers
        ]
        document["condition_locks"] = [
            {
                "condition_id": condition,
                "content_hash": hashlib.sha256(condition.encode("utf-8")).hexdigest(),
            }
            for condition in self.conditions
        ]
        document["resolved_manifest_hash"] = hashlib.sha256(
            json.dumps(
                document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
        validate_record("resolved_manifest", document)
        return document

    def validate_for_queue(self, sample_ids: tuple[str, ...]) -> dict[str, Any]:
        """Validate all outcome-affecting locks immediately before queue creation."""
        if self.containers.image and not self.containers.digests:
            raise ValueError("Container image locks require at least one immutable digest")
        if not self.dataset.revision:
            raise ValueError("Dataset revision must be locked before work is queued")
        if not all(model.provider_model and model.aa_snapshot for model in self.models):
            raise ValueError("Every model must lock a dated provider model and AA snapshot")
        if not self.prompt_version:
            raise ValueError("Prompt version must be locked before work is queued")
        if not self.conditions or not self.scorers:
            raise ValueError("Conditions and scorers must be non-empty before queueing")
        return self.resolved(sample_ids)


def load_manifest(path: str | Path) -> ExperimentManifest:
    manifest_path = Path(path)
    with manifest_path.open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream)

    if not isinstance(document, dict):
        raise ValueError(f"Manifest must contain a YAML object: {manifest_path}")

    manifest = ExperimentManifest.model_validate(document)
    validate_record("manifest", manifest.model_dump(mode="json"))
    return manifest
