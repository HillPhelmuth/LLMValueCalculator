from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
PERMANENT_STATUS_CODES = frozenset({400, 401, 402, 403, 404, 405, 406, 422})


@dataclass(frozen=True, slots=True)
class RetryClassification:
    retryable: bool
    status_code: int | None
    retry_after_seconds: float | None
    reason: str


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    base_seconds: float = 0.25
    max_seconds: float = 30.0
    jitter_fraction: float = 0.2

    def delay(
        self,
        retry_index: int,
        *,
        retry_after_seconds: float | None = None,
        random_value: float | None = None,
    ) -> float:
        exponential = min(self.max_seconds, self.base_seconds * (2**retry_index))
        requested = max(0.0, retry_after_seconds or 0.0)
        delay = max(exponential, requested)
        if self.jitter_fraction:
            value = random.random() if random_value is None else random_value
            value = max(0.0, min(1.0, value))
            delay *= 1 - self.jitter_fraction + (2 * self.jitter_fraction * value)
        return min(self.max_seconds, delay)


@dataclass(frozen=True, slots=True)
class TransportEvent:
    request_hash: str
    provider: str
    transport_attempt: int
    event_type: str
    status_code: int | None = None
    retry_after_seconds: float | None = None
    delay_seconds: float | None = None
    error_type: str | None = None
    error_message: str | None = None


class AsyncBudgetGate:
    """A bounded request/token budget shared by provider workers."""

    def __init__(self, *, max_requests: int | None = None, max_tokens: int | None = None) -> None:
        self.max_requests = max_requests
        self.max_tokens = max_tokens
        self.requests_used = 0
        self.tokens_used = 0
        self._lock = asyncio.Lock()

    async def reserve(self, *, requests: int = 1, tokens: int = 0) -> None:
        async with self._lock:
            if self.max_requests is not None and self.requests_used + requests > self.max_requests:
                raise BudgetExceeded("request budget exceeded")
            if self.max_tokens is not None and self.tokens_used + tokens > self.max_tokens:
                raise BudgetExceeded("token budget exceeded")
            self.requests_used += requests
            self.tokens_used += tokens


class BudgetExceeded(RuntimeError):
    pass


def classify_exception(error: BaseException) -> RetryClassification:
    status_code = _status_code(error)
    retry_after = _retry_after_seconds(error)
    if status_code in PERMANENT_STATUS_CODES:
        return RetryClassification(False, status_code, retry_after, "permanent HTTP error")
    if status_code in RETRYABLE_STATUS_CODES:
        return RetryClassification(True, status_code, retry_after, "retryable HTTP error")
    if status_code is not None:
        return RetryClassification(False, status_code, retry_after, "unclassified HTTP error")
    name = type(error).__name__.casefold()
    if any(token in name for token in ("timeout", "connection", "network", "transport", "ratelimit")):
        return RetryClassification(True, None, retry_after, "transient transport error")
    return RetryClassification(False, None, retry_after, "non-transport error")


def _status_code(error: BaseException) -> int | None:
    value = getattr(error, "status_code", None)
    if value is None:
        response = getattr(error, "response", None)
        value = getattr(response, "status_code", None)
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _retry_after_seconds(error: BaseException) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or getattr(error, "headers", None) or {}
    raw = None
    if hasattr(headers, "get"):
        raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        try:
            timestamp = parsedate_to_datetime(str(raw))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            return max(0.0, (timestamp - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
