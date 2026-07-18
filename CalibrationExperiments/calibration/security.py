from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


SECRET_KEY_PATTERN = re.compile(
    r"^(?:api[_-]?key|authorization|access[_-]?token|auth[_-]?token|token|secret|password|credential|private[_-]?key)$|(?:api[_-]?key|access[_-]?token|auth[_-]?token)$",
    re.IGNORECASE,
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
)
REDACTED = "[REDACTED]"


def redact(value: Any, *, key: str | None = None) -> Any:
    """Return a recursively redacted copy suitable for logs and artifacts."""
    if key and SECRET_KEY_PATTERN.search(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {str(k): redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        result = value
        for pattern in SECRET_VALUE_PATTERNS:
            result = pattern.sub(REDACTED, result)
        return result
    return value


def redact_text(value: str) -> str:
    return str(redact(value))


def assert_secret_free(value: Any, secrets: Sequence[str]) -> None:
    """Raise if a configured secret occurs in a nested value."""
    rendered = str(value)
    for secret in secrets:
        if secret and secret in rendered:
            raise ValueError("Secret material was present in a persisted value")
