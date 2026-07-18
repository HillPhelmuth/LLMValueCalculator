from __future__ import annotations

import asyncio
import time


class AsyncTokenBucket:
    def __init__(self, requests_per_minute: float | None) -> None:
        self._rate_per_second = (
            None if requests_per_minute is None else requests_per_minute / 60
        )
        self._capacity = max(1.0, requests_per_minute or 1.0)
        self._tokens = self._capacity
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self._rate_per_second is None:
            return

        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._tokens = min(
                    self._capacity,
                    self._tokens + elapsed * self._rate_per_second,
                )
                self._updated_at = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                delay = (1 - self._tokens) / self._rate_per_second
            await asyncio.sleep(delay)

