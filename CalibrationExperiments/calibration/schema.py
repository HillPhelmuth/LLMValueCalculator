from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


SCHEMA_VERSION = "1.0"
SCHEMA_DIRECTORY = Path(__file__).with_name("schemas")


class SchemaValidationError(ValueError):
    """Raised when a persisted language-neutral record is not valid."""


def schema_path(record_type: str) -> Path:
    return SCHEMA_DIRECTORY / f"{record_type}.schema.json"


def load_schema(record_type: str) -> dict[str, Any]:
    path = schema_path(record_type)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SchemaValidationError(f"Unknown record schema: {record_type}") from error


def validate_record(record_type: str, value: dict[str, Any]) -> dict[str, Any]:
    validator = Draft202012Validator(load_schema(record_type))
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<record>"
        raise SchemaValidationError(
            f"Invalid {record_type} record at {location}: {first.message}"
        )
    return value


def with_schema_version(value: dict[str, Any]) -> dict[str, Any]:
    def json_compatible(item: Any) -> Any:
        if isinstance(item, tuple):
            return [json_compatible(part) for part in item]
        if isinstance(item, list):
            return [json_compatible(part) for part in item]
        if isinstance(item, dict):
            return {str(key): json_compatible(part) for key, part in item.items()}
        return item

    copy = json_compatible(dict(value))
    copy.setdefault("schema_version", SCHEMA_VERSION)
    return copy
