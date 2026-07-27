from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass, field
from typing import Any

from calibration.models import CanonicalCase, ProviderResponse, ScoreResult
from calibration.scorers.base import Scorer
from calibration.scorers.deterministic import (
    ClassificationAccuracyScorer,
    ExactMatchScorer,
    FieldLevelComparisonScorer,
    NdcgScorer,
    RetrievalRecallScorer,
    SchemaValidityScorer,
    SemanticStructuredValueScorer,
    SupportingFactRecallScorer,
    TokenF1Scorer,
)
from calibration.scorers.judge import LlmSemanticCorrectnessScorer


class ScorerRegistryError(ValueError):
    """Raised when scorer names or locks are ambiguous."""


@dataclass(frozen=True, slots=True)
class ScorerConfig:
    name: str
    version: str
    configuration: dict[str, Any] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)

    @property
    def configuration_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {"configuration": self.configuration, "thresholds": self.thresholds},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


class ScorerRegistry:
    def __init__(self, scorers: dict[str, Scorer] | None = None) -> None:
        self._scorers: dict[str, Scorer] = {}
        for scorer in (scorers or self._default()).values():
            self.register(scorer)

    @staticmethod
    def _default() -> dict[str, Scorer]:
        return {
            scorer.name: scorer
            for scorer in (
                ExactMatchScorer(),
                TokenF1Scorer(),
                ClassificationAccuracyScorer(),
                FieldLevelComparisonScorer(),
                SupportingFactRecallScorer(),
                RetrievalRecallScorer(),
                NdcgScorer(),
                SchemaValidityScorer({"type": "object"}),
                SemanticStructuredValueScorer(),
                LlmSemanticCorrectnessScorer(),
            )
        }

    def register(self, scorer: Scorer) -> None:
        if not scorer.name or not scorer.version:
            raise ScorerRegistryError("Scorers require stable names and versions")
        if scorer.name in self._scorers:
            raise ScorerRegistryError(f"Duplicate scorer: {scorer.name}")
        self._scorers[scorer.name] = scorer

    def get(self, name: str) -> Scorer:
        try:
            return self._scorers[name]
        except KeyError as error:
            raise ScorerRegistryError(f"Unknown scorer: {name}") from error

    def score_all(
        self,
        names: tuple[str, ...],
        case: CanonicalCase,
        response: ProviderResponse,
    ) -> tuple[ScoreResult, ...]:
        results = tuple(self.get(name).score(case, response) for name in names)
        keys = [(result.scorer_name, result.scorer_version) for result in results]
        if len(keys) != len(set(keys)):
            raise ScorerRegistryError("Multiple scorers produced the same result key")
        return results

    def locks(self, names: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
        locks: list[dict[str, Any]] = []
        for name in names:
            scorer = self.get(name)
            implementation = inspect.getsource(type(scorer))
            locks.append(
                {
                    "name": scorer.name,
                    "version": scorer.version,
                    "implementation_hash": hashlib.sha256(
                        implementation.encode("utf-8")
                    ).hexdigest(),
                    "configuration_hash": ScorerConfig(
                        scorer.name, scorer.version
                    ).configuration_hash,
                }
            )
        return tuple(locks)
