from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from calibration.models import CanonicalCase, CaseFeatures, Message, ScoreResult


class DatasetAdapter(ABC):
    @abstractmethod
    def prepare(self) -> None:
        """Verify that the pinned dataset is available and unchanged."""

    @abstractmethod
    def cases(self, split: str) -> Iterable[CanonicalCase]:
        """Yield canonical cases with stable IDs."""

    @abstractmethod
    def render(self, case: CanonicalCase, condition: str) -> tuple[Message, ...]:
        """Render provider-neutral messages for a condition."""

    @abstractmethod
    def metadata(self, case: CanonicalCase) -> CaseFeatures:
        """Return fitting features for a case."""

    def score(self, case: CanonicalCase, response: object) -> tuple[ScoreResult, ...]:
        """Return optional dataset-specific deterministic scores."""
        return ()

