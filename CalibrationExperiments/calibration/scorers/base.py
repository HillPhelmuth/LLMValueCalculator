from __future__ import annotations

from abc import ABC, abstractmethod

from calibration.models import CanonicalCase, ProviderResponse, ScoreResult


class Scorer(ABC):
    name: str
    version: str

    @abstractmethod
    def score(self, case: CanonicalCase, response: ProviderResponse) -> ScoreResult:
        """Score one response deterministically."""

