from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from calibration.providers.openrouter_catalog import CatalogSnapshot
from calibration.schema import SCHEMA_VERSION, validate_record
from calibration.storage.artifacts import ArtifactStore


class MappingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ArtificialAnalysisMapping:
    stable_catalog_id: str
    openrouter_id: str
    aa_model_id: str
    aa_model_version: str
    snapshot_date: str
    intelligence_index: Decimal | None
    coding_index: Decimal | None
    agentic_index: Decimal | None
    cost_index: Decimal | None
    source_citations: tuple[str, ...] = ()
    manual_override_rationale: str | None = None
    schema_version: str = SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "stable_catalog_id": self.stable_catalog_id,
            "openrouter_id": self.openrouter_id,
            "aa_model_id": self.aa_model_id,
            "aa_model_version": self.aa_model_version,
            "snapshot_date": self.snapshot_date,
            "intelligence_index": _decimal_json(self.intelligence_index),
            "coding_index": _decimal_json(self.coding_index),
            "agentic_index": _decimal_json(self.agentic_index),
            "cost_index": _decimal_json(self.cost_index),
            "source_citations": list(self.source_citations),
            "manual_override_rationale": self.manual_override_rationale,
        }


@dataclass(frozen=True, slots=True)
class ArtificialAnalysisSnapshot:
    snapshot_id: str
    snapshot_date: str
    source_citations: tuple[str, ...]
    mappings: tuple[ArtificialAnalysisMapping, ...]
    snapshot_hash: str
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_mappings(
        cls,
        mappings: tuple[ArtificialAnalysisMapping, ...],
        *,
        snapshot_date: str,
        source_citations: tuple[str, ...] = (),
        catalog: CatalogSnapshot | None = None,
    ) -> "ArtificialAnalysisSnapshot":
        if not mappings:
            raise MappingError("Artificial Analysis snapshot must contain mappings")
        _validate_mappings(mappings, catalog)
        identity = {
            "snapshot_date": snapshot_date,
            "source_citations": list(source_citations),
            "mappings": [mapping.to_json() for mapping in mappings],
        }
        snapshot_hash = _hash_json(identity)
        snapshot = cls(
            snapshot_id=f"aa-{snapshot_date}-{snapshot_hash[:16]}",
            snapshot_date=snapshot_date,
            source_citations=source_citations,
            mappings=mappings,
            snapshot_hash=snapshot_hash,
        )
        validate_record("artificial_analysis_snapshot", snapshot.to_json())
        return snapshot

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "snapshot_date": self.snapshot_date,
            "source_citations": list(self.source_citations),
            "mappings": [mapping.to_json() for mapping in self.mappings],
            "snapshot_hash": self.snapshot_hash,
        }

    def mapping_for(self, openrouter_id: str) -> ArtificialAnalysisMapping:
        matches = [item for item in self.mappings if item.openrouter_id == openrouter_id]
        if len(matches) != 1:
            raise MappingError(f"Expected one AA mapping for {openrouter_id}, got {len(matches)}")
        return matches[0]

    def persist(self, artifacts: ArtifactStore) -> str:
        return artifacts.put_json(self.to_json(), media_type="application/json")


def load_mapping_snapshot(
    path: str | Path,
    *,
    catalog: CatalogSnapshot | None = None,
) -> ArtificialAnalysisSnapshot:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise MappingError("Artificial Analysis mapping must be a YAML object")
    snapshot_date = _date_string(document.get("snapshot_date"))
    raw_mappings = document.get("mappings")
    if not isinstance(raw_mappings, list):
        raise MappingError("Artificial Analysis mapping requires a mappings array")
    mappings = tuple(_mapping_from_json(value) for value in raw_mappings)
    source_citations = tuple(str(value) for value in document.get("source_citations", ()))
    return ArtificialAnalysisSnapshot.from_mappings(
        mappings,
        snapshot_date=snapshot_date,
        source_citations=source_citations,
        catalog=catalog,
    )


def _mapping_from_json(value: Any) -> ArtificialAnalysisMapping:
    if not isinstance(value, dict):
        raise MappingError("Each Artificial Analysis mapping must be an object")
    try:
        return ArtificialAnalysisMapping(
            stable_catalog_id=str(value["stable_catalog_id"]),
            openrouter_id=str(value["openrouter_id"]),
            aa_model_id=str(value["aa_model_id"]),
            aa_model_version=str(value["aa_model_version"]),
            snapshot_date=_date_string(value["snapshot_date"]),
            intelligence_index=_decimal_or_none(value.get("intelligence_index")),
            coding_index=_decimal_or_none(value.get("coding_index")),
            agentic_index=_decimal_or_none(value.get("agentic_index")),
            cost_index=_decimal_or_none(value.get("cost_index")),
            source_citations=tuple(str(item) for item in value.get("source_citations", ())),
            manual_override_rationale=value.get("manual_override_rationale"),
        )
    except KeyError as error:
        raise MappingError(f"Mapping is missing required field: {error.args[0]}") from error


def _validate_mappings(
    mappings: tuple[ArtificialAnalysisMapping, ...], catalog: CatalogSnapshot | None
) -> None:
    openrouter_ids = [item.openrouter_id for item in mappings]
    aa_keys = [(item.aa_model_id, item.aa_model_version) for item in mappings]
    stable_ids = [item.stable_catalog_id for item in mappings]
    if len(openrouter_ids) != len(set(openrouter_ids)):
        raise MappingError("An OpenRouter ID has multiple Artificial Analysis mappings")
    if len(aa_keys) != len(set(aa_keys)):
        raise MappingError("An Artificial Analysis model version is mapped more than once")
    if len(stable_ids) != len(set(stable_ids)):
        raise MappingError("Stable catalog IDs must be unique")
    for item in mappings:
        if catalog is not None:
            try:
                catalog.model(item.openrouter_id)
            except KeyError as error:
                raise MappingError(
                    f"Mapping references an OpenRouter model absent from the catalog: {item.openrouter_id}"
                ) from error
        if not item.source_citations:
            raise MappingError(f"Mapping has no source citation: {item.openrouter_id}")
        if item.manual_override_rationale is not None and not item.manual_override_rationale.strip():
            raise MappingError(f"Manual override rationale is empty: {item.openrouter_id}")


def _date_string(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or not value:
        raise MappingError("Snapshot dates must be non-empty ISO date strings")
    return value


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise MappingError(f"Invalid Artificial Analysis number: {value!r}") from error
    if not number.is_finite():
        raise MappingError(f"Artificial Analysis number must be finite: {value!r}")
    return number


def _decimal_json(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
