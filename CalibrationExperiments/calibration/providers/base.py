from __future__ import annotations

from abc import ABC, abstractmethod

from calibration.models import ProviderRequest, ProviderResponse


class ModelProvider(ABC):
    name: str
    requests_per_minute: float | None = None
    max_concurrency: int = 1

    @abstractmethod
    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Execute one model attempt without applying experimental retries."""

