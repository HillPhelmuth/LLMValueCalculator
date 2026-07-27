from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from calibration.model_mapping import ArtificialAnalysisSnapshot
from calibration.providers.openrouter_catalog import CatalogSnapshot, ModelCatalogEntry
from calibration.schema import SCHEMA_VERSION, validate_record
from calibration.storage.artifacts import ArtifactStore


class EligibilityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ModelEligibilityRules:
    experiment_id: str
    as_of_date: str
    minimum_models: int = 1
    required_context_length: int = 0
    required_output_tokens: int = 0
    required_parameters: tuple[str, ...] = ()
    required_modalities: tuple[str, ...] = ()
    require_tools: bool = False
    require_json: bool = False
    require_versioned: bool = True
    max_models: int | None = None

    def as_of(self) -> date:
        return date.fromisoformat(self.as_of_date)


@dataclass(frozen=True, slots=True)
class PanelSelection:
    panel_id: str
    catalog_snapshot_hash: str
    aa_snapshot_hash: str
    selected: tuple[dict[str, Any], ...]
    decisions: tuple[dict[str, Any], ...]
    panel_hash: str
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def create(
        cls,
        catalog: CatalogSnapshot,
        aa_snapshot: ArtificialAnalysisSnapshot,
        selected: tuple[dict[str, Any], ...],
        decisions: tuple[dict[str, Any], ...],
    ) -> "PanelSelection":
        identity = {
            "catalog_snapshot_hash": catalog.snapshot_hash,
            "aa_snapshot_hash": aa_snapshot.snapshot_hash,
            "selected": list(selected),
            "decisions": list(decisions),
        }
        panel_hash = _hash_json(identity)
        panel = cls(
            panel_id=f"panel-{panel_hash[:16]}",
            catalog_snapshot_hash=catalog.snapshot_hash,
            aa_snapshot_hash=aa_snapshot.snapshot_hash,
            selected=selected,
            decisions=decisions,
            panel_hash=panel_hash,
        )
        validate_record("model_panel", panel.to_json())
        return panel

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "panel_id": self.panel_id,
            "catalog_snapshot_hash": self.catalog_snapshot_hash,
            "aa_snapshot_hash": self.aa_snapshot_hash,
            "selected": list(self.selected),
            "decisions": list(self.decisions),
            "panel_hash": self.panel_hash,
        }

    def persist(self, artifacts: ArtifactStore) -> str:
        return artifacts.put_json(self.to_json(), media_type="application/json")


def select_model_panel(
    catalog: CatalogSnapshot,
    aa_snapshot: ArtificialAnalysisSnapshot,
    rules: ModelEligibilityRules,
) -> PanelSelection:
    decisions: list[dict[str, Any]] = []
    candidates: list[tuple[ModelCatalogEntry, Any, int]] = []
    as_of = rules.as_of()
    experiment_one = _is_experiment_one(rules.experiment_id)
    for model in catalog.models:
        reasons: list[str] = []
        mapping = None
        try:
            mapping = aa_snapshot.mapping_for(model.id)
        except Exception as error:
            reasons.append(str(error))
        if (
            experiment_one
            and mapping is not None
            and mapping.intelligence_index is None
        ):
            reasons.append("Artificial Analysis intelligence index is missing")
        if not model.available:
            reasons.append("model is unavailable")
        if model.expiration_date and _parse_date(model.expiration_date) <= as_of:
            reasons.append("model has expired")
        if rules.require_versioned and not (model.versioned or model.resolved_model):
            reasons.append("model is an unversioned rolling alias")
        if (
            model.context_length is None
            or model.context_length < rules.required_context_length
        ):
            reasons.append("context length is below the experiment requirement")
        if rules.required_output_tokens and (
            model.max_completion_tokens is None
            or model.max_completion_tokens < rules.required_output_tokens
        ):
            reasons.append("provider output limit is below the experiment requirement")
        missing_parameters = [
            parameter
            for parameter in rules.required_parameters
            if not model.supports(parameter)
        ]
        if missing_parameters:
            reasons.append(
                f"missing supported parameters: {', '.join(missing_parameters)}"
            )
        missing_modalities = [
            modality
            for modality in rules.required_modalities
            if modality not in model.modalities
        ]
        if missing_modalities:
            reasons.append(f"missing modalities: {', '.join(missing_modalities)}")
        if rules.require_tools and not any(
            parameter in model.supported_parameters
            for parameter in ("tools", "tool_choice")
        ):
            reasons.append("tool calling is not supported")
        if rules.require_json and not any(
            parameter in model.supported_parameters
            for parameter in ("structured_outputs", "response_format", "json_schema")
        ):
            reasons.append("structured JSON output is not supported")
        aa_band = _aa_band(mapping.intelligence_index if mapping else None)
        decision = {
            "model_id": model.id,
            "canonical_slug": model.canonical_slug,
            "eligible": not reasons,
            "reasons": reasons or ["eligible"],
            "aa_band": aa_band,
            "aa_model_id": None if mapping is None else mapping.aa_model_id,
            "aa_model_version": None if mapping is None else mapping.aa_model_version,
        }
        decisions.append(decision)
        if not reasons and mapping is not None:
            candidates.append((model, mapping, aa_band))

    if not candidates:
        raise EligibilityError(
            "No eligible models remain after catalog and capability filters"
        )
    required_models = 10 if experiment_one else rules.minimum_models
    if experiment_one:
        _require_experiment_one_coverage(candidates, required_models)
        candidates = _select_experiment_one_candidates(candidates)
    if len(candidates) < required_models:
        raise EligibilityError(
            f"Only {len(candidates)} eligible models; {required_models} are required"
        )
    candidates.sort(
        key=lambda item: (
            item[2] is None,
            item[2] if item[2] is not None else 10_000,
            item[0].id,
        )
    )
    if rules.max_models is not None:
        candidates = candidates[: rules.max_models]
    selected = tuple(
        {
            "model_id": model.id,
            "provider_model": model.resolved_model or model.id,
            "catalog_entry": model.to_json(),
            "aa_mapping": mapping.to_json(),
            "aa_band": band,
            "selection_reason": "passed all catalog, freshness, mapping, and capability rules",
        }
        for model, mapping, band in candidates
    )
    return PanelSelection.create(catalog, aa_snapshot, selected, tuple(decisions))


def _require_experiment_one_coverage(
    candidates: list[tuple[ModelCatalogEntry, Any, int]], minimum_models: int
) -> None:
    if minimum_models < 10:
        minimum_models = 10
    bands: dict[int, int] = {}
    for _, _, band in candidates:
        if band is not None:
            bands[band] = bands.get(band, 0) + 1
    required_bands = (10, 20, 30, 40, 50)
    underrepresented = [band for band in required_bands if bands.get(band, 0) < 2]
    if underrepresented:
        raise EligibilityError(
            "Experiment 1 requires two models in each accepted intelligence band: "
            + ", ".join(str(band) for band in underrepresented)
        )
    if len(candidates) < minimum_models:
        raise EligibilityError(
            f"Experiment 1 requires at least {minimum_models} eligible models, got {len(candidates)}"
        )


def _select_experiment_one_candidates(
    candidates: list[tuple[ModelCatalogEntry, Any, int]],
) -> list[tuple[ModelCatalogEntry, Any, int]]:
    selected: list[tuple[ModelCatalogEntry, Any, int]] = []
    for band in (10, 20, 30, 40, 50):
        rows = sorted(
            (row for row in candidates if row[2] == band),
            key=lambda row: (_catalog_unit_cost(row[0]), row[0].id),
        )
        first = rows[0]
        first_provider = first[0].id.split("/", 1)[0]
        second = next(
            (row for row in rows[1:] if row[0].id.split("/", 1)[0] != first_provider),
            rows[1],
        )
        selected.extend((first, second))
    return selected


def _catalog_unit_cost(model: ModelCatalogEntry) -> Decimal:
    return (model.pricing.get("prompt") or Decimal("0")) + (
        model.pricing.get("completion") or Decimal("0")
    )


def _is_experiment_one(experiment_id: str) -> bool:
    normalized = experiment_id.casefold().replace("_", "-")
    return normalized in {
        "1",
        "experiment-1",
        "exp-1",
        "intelligence-curve",
    } or normalized.startswith("experiment-1-")


def _aa_band(index: Decimal | None) -> int | None:
    return None if index is None else int(index // Decimal("10")) * 10


def _parse_date(value: str) -> date:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return date.fromisoformat(value[:10])


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
