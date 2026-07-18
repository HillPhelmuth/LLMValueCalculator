from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from calibration.schema import SCHEMA_VERSION, validate_record
from calibration.security import redact_text
from calibration.storage.artifacts import ArtifactStore


OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


class CatalogError(RuntimeError):
    """Base class for catalog retrieval and validation failures."""


class CatalogHttpError(CatalogError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"OpenRouter catalog request failed ({status_code}): {message}")
        self.status_code = status_code


class CatalogSchemaError(CatalogError):
    pass


@dataclass(frozen=True, slots=True)
class ModelCatalogEntry:
    id: str
    canonical_slug: str | None
    created: int | None
    expiration_date: str | None
    context_length: int | None
    input_modalities: tuple[str, ...]
    output_modalities: tuple[str, ...]
    modalities: tuple[str, ...]
    supported_parameters: tuple[str, ...]
    pricing: dict[str, Decimal | None]
    top_provider_limits: dict[str, Any]
    available: bool = True
    versioned: bool = False
    resolved_model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    @property
    def max_completion_tokens(self) -> int | None:
        value = self.top_provider_limits.get("max_completion_tokens")
        return _int_or_none(value)

    def supports(self, parameter: str) -> bool:
        return parameter in self.supported_parameters

    def to_json(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "id": self.id,
            "canonical_slug": self.canonical_slug,
            "created": self.created,
            "expiration_date": self.expiration_date,
            "context_length": self.context_length,
            "input_modalities": list(self.input_modalities),
            "output_modalities": list(self.output_modalities),
            "modalities": list(self.modalities),
            "supported_parameters": list(self.supported_parameters),
            "pricing": {
                key: None if value is None else format(value, "f")
                for key, value in self.pricing.items()
            },
            "top_provider_limits": self.top_provider_limits,
            "available": self.available,
            "versioned": self.versioned,
            "resolved_model": self.resolved_model,
            "raw": self.raw,
        }
        return value

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "ModelCatalogEntry":
        pricing = {
            str(key): _decimal_or_none(raw_value)
            for key, raw_value in dict(value.get("pricing", {})).items()
        }
        return cls(
            id=str(value["id"]),
            canonical_slug=value.get("canonical_slug"),
            created=_int_or_none(value.get("created")),
            expiration_date=value.get("expiration_date"),
            context_length=_int_or_none(value.get("context_length")),
            input_modalities=tuple(value.get("input_modalities", ())),
            output_modalities=tuple(value.get("output_modalities", ())),
            modalities=tuple(value.get("modalities", ())),
            supported_parameters=tuple(value.get("supported_parameters", ())),
            pricing=pricing,
            top_provider_limits=dict(value.get("top_provider_limits", {})),
            available=bool(value.get("available", True)),
            versioned=bool(value.get("versioned", False)),
            resolved_model=value.get("resolved_model"),
            raw=dict(value.get("raw", {})),
            schema_version=str(value.get("schema_version", SCHEMA_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    snapshot_id: str
    captured_utc: str
    source_url: str
    models: tuple[ModelCatalogEntry, ...]
    raw_pages: tuple[dict[str, Any], ...]
    snapshot_hash: str
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_pages(
        cls,
        pages: tuple[dict[str, Any], ...],
        *,
        captured_utc: str | None = None,
        source_url: str = OPENROUTER_MODELS_URL,
    ) -> "CatalogSnapshot":
        models: list[ModelCatalogEntry] = []
        seen: set[str] = set()
        for page in pages:
            if not isinstance(page, dict) or not isinstance(page.get("data"), list):
                raise CatalogSchemaError("Each catalog page must contain a data array")
            for raw_model in page.get("data", []):
                entry = normalize_model(raw_model)
                if entry.id in seen:
                    raise CatalogSchemaError(f"Duplicate model ID in catalog: {entry.id}")
                seen.add(entry.id)
                models.append(entry)
        if not models:
            raise CatalogSchemaError("OpenRouter catalog response contained no models")
        captured = captured_utc or datetime.now(timezone.utc).isoformat()
        identity = {
            "source_url": source_url,
            "models": [model.to_json() for model in models],
            "raw_pages": list(pages),
        }
        snapshot_hash = _sha256_json(identity)
        snapshot_id = f"{captured.replace(':', '').replace('+00:00', 'Z')}-{snapshot_hash[:16]}"
        snapshot = cls(
            snapshot_id=snapshot_id,
            captured_utc=captured,
            source_url=source_url,
            models=tuple(models),
            raw_pages=pages,
            snapshot_hash=snapshot_hash,
        )
        validate_record("model_catalog_snapshot", snapshot.to_json())
        return snapshot

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "captured_utc": self.captured_utc,
            "source_url": self.source_url,
            "models": [model.to_json() for model in self.models],
            "raw_pages": list(self.raw_pages),
            "snapshot_hash": self.snapshot_hash,
        }

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "CatalogSnapshot":
        snapshot = cls(
            snapshot_id=str(value["snapshot_id"]),
            captured_utc=str(value["captured_utc"]),
            source_url=str(value["source_url"]),
            models=tuple(ModelCatalogEntry.from_json(item) for item in value["models"]),
            raw_pages=tuple(value["raw_pages"]),
            snapshot_hash=str(value["snapshot_hash"]),
            schema_version=str(value.get("schema_version", SCHEMA_VERSION)),
        )
        validate_record("model_catalog_snapshot", snapshot.to_json())
        return snapshot

    def model(self, model_id: str) -> ModelCatalogEntry:
        for model in self.models:
            if model.id == model_id:
                return model
        raise KeyError(f"Model is not present in catalog snapshot: {model_id}")

    def persist(self, artifacts: ArtifactStore) -> str:
        """Persist the timestamped snapshot as an immutable content-addressed object."""
        return artifacts.put_json(self.to_json(), media_type="application/json")


class OpenRouterCatalogClient:
    """Authenticated async client for the OpenRouter model catalog."""

    def __init__(
        self,
        api_key: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = OPENROUTER_MODELS_URL,
        page_size: int = 100,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("OpenRouter catalog client requires an API key")
        if page_size < 1:
            raise ValueError("page_size must be positive")
        self._api_key = api_key
        self._http_client = http_client
        self._base_url = base_url
        self._page_size = page_size
        self._timeout = timeout_seconds

    async def fetch_page(self, *, offset: int = 0, limit: int | None = None) -> dict[str, Any]:
        page_limit = limit or self._page_size
        params = {"offset": offset, "limit": page_limit}
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        client = self._http_client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout)
        try:
            try:
                response = await client.get(self._base_url, params=params, headers=headers)
            except httpx.HTTPError as error:
                raise CatalogHttpError(0, redact_text(str(error))) from error
        finally:
            if owns_client:
                await client.aclose()
        if response.status_code >= 400:
            raise CatalogHttpError(response.status_code, _response_message(response))
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as error:
            raise CatalogSchemaError("OpenRouter catalog response was not valid JSON") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise CatalogSchemaError("OpenRouter catalog response must contain a data array")
        return payload

    async def fetch_snapshot(self) -> CatalogSnapshot:
        pages: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = await self.fetch_page(offset=offset, limit=self._page_size)
            pages.append(page)
            count = len(page["data"])
            if count < self._page_size:
                break
            offset += count
        return CatalogSnapshot.from_pages(tuple(pages), source_url=self._base_url)


def normalize_model(raw: dict[str, Any]) -> ModelCatalogEntry:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
        raise CatalogSchemaError("Each catalog model must have a non-empty id")
    architecture = raw.get("architecture") or {}
    if not isinstance(architecture, dict):
        raise CatalogSchemaError(f"Invalid architecture for model {raw['id']}")
    input_modalities = _strings(
        architecture.get("input_modalities", raw.get("input_modalities", ()))
    )
    output_modalities = _strings(
        architecture.get("output_modalities", raw.get("output_modalities", ()))
    )
    modality = architecture.get("modality")
    modalities = tuple(dict.fromkeys((*input_modalities, *output_modalities, *(_strings((modality,)) if modality else ()))))
    supported = _strings(raw.get("supported_parameters", ()))
    pricing_raw = raw.get("pricing") or {}
    if not isinstance(pricing_raw, dict):
        raise CatalogSchemaError(f"Invalid pricing for model {raw['id']}")
    pricing = {str(key): _decimal_or_none(value) for key, value in pricing_raw.items()}
    top_provider = raw.get("top_provider") or {}
    if not isinstance(top_provider, dict):
        raise CatalogSchemaError(f"Invalid top_provider for model {raw['id']}")
    availability = raw.get("availability", raw.get("available", True))
    if not isinstance(availability, (bool, int)):
        availability = True
    resolved = raw.get("resolved_model") or raw.get("version")
    canonical = raw.get("canonical_slug")
    versioned = bool(raw.get("versioned", False) or resolved or _looks_versioned(str(canonical or raw["id"])))
    return ModelCatalogEntry(
        id=raw["id"],
        canonical_slug=None if canonical is None else str(canonical),
        created=_int_or_none(raw.get("created")),
        expiration_date=_string_or_none(raw.get("expiration_date", raw.get("expiration"))),
        context_length=_int_or_none(raw.get("context_length")),
        input_modalities=input_modalities,
        output_modalities=output_modalities,
        modalities=modalities,
        supported_parameters=supported,
        pricing=pricing,
        top_provider_limits=dict(top_provider),
        available=bool(availability),
        versioned=versioned,
        resolved_model=None if resolved is None else str(resolved),
        raw=dict(raw),
    )


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise CatalogSchemaError(f"Invalid decimal pricing value: {value!r}") from error
    if not decimal.is_finite() or decimal < 0:
        raise CatalogSchemaError(f"Pricing must be a finite non-negative decimal: {value!r}")
    return decimal


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        raise CatalogSchemaError(f"Expected a string array, got {type(value).__name__}")
    return tuple(str(item) for item in value if item is not None)


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise CatalogSchemaError(f"Expected an integer, got {value!r}") from error


def _string_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def _looks_versioned(value: str) -> bool:
    lowered = value.casefold()
    return any(character.isdigit() for character in lowered) and any(
        marker in lowered for marker in ("-20", ":20", "-v", ".v", "-instruct")
    )


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _response_message(response: httpx.Response) -> str:
    try:
        value = response.json()
        if isinstance(value, dict):
            return redact_text(str(value.get("error", value)))
    except ValueError:
        pass
    return redact_text(response.text[:500])
