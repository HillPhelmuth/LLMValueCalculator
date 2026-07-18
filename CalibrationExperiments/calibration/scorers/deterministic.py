from __future__ import annotations

import re
import unicodedata
from collections import Counter
from math import log2
from typing import Any, Mapping

from jsonschema import ValidationError
from jsonschema import validate as validate_json

from calibration.models import CanonicalCase, ProviderResponse, ScoreResult
from calibration.scorers.base import Scorer


def normalize_answer(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


class ExactMatchScorer(Scorer):
    name = "answer_exact_match"
    version = "1.0.0"

    def score(self, case: CanonicalCase, response: ProviderResponse) -> ScoreResult:
        actual = normalize_answer(response.parsed_answer)
        expected = normalize_answer(case.expected)
        matched = actual == expected
        return ScoreResult(
            scorer_name=self.name,
            scorer_version=self.version,
            success=matched,
            acceptable=matched,
            semantic_score=1.0 if matched else 0.0,
            failure_class=None if matched else "answer_mismatch",
            metrics={"actual": actual, "expected": expected},
        )


class TokenF1Scorer(Scorer):
    name = "answer_token_f1"
    version = "1.0.0"

    def score(self, case: CanonicalCase, response: ProviderResponse) -> ScoreResult:
        actual = normalize_answer(response.parsed_answer).split()
        expected = normalize_answer(case.expected).split()
        common = Counter(actual) & Counter(expected)
        overlap = sum(common.values())

        if not actual and not expected:
            f1 = 1.0
        elif overlap == 0:
            f1 = 0.0
        else:
            precision = overlap / len(actual)
            recall = overlap / len(expected)
            f1 = 2 * precision * recall / (precision + recall)

        return ScoreResult(
            scorer_name=self.name,
            scorer_version=self.version,
            semantic_score=f1,
            metrics={"token_f1": f1},
        )


class ClassificationAccuracyScorer(Scorer):
    name = "classification_accuracy"
    version = "1.0.0"

    def score(self, case: CanonicalCase, response: ProviderResponse) -> ScoreResult:
        actual = normalize_answer(response.parsed_answer)
        expected = normalize_answer(case.expected)
        matched = actual == expected
        return ScoreResult(
            scorer_name=self.name,
            scorer_version=self.version,
            success=matched,
            acceptable=matched,
            semantic_score=float(matched),
            failure_class=None if matched else "classification_mismatch",
            metrics={"actual": actual, "expected": expected},
        )


class FieldLevelComparisonScorer(Scorer):
    name = "field_level_comparison"
    version = "1.0.0"

    def __init__(self, required_fields: tuple[str, ...] = ()) -> None:
        self.required_fields = required_fields

    def score(self, case: CanonicalCase, response: ProviderResponse) -> ScoreResult:
        expected = _mapping(case.expected)
        actual = _mapping(response.parsed_answer)
        fields = self.required_fields or tuple(sorted(set(expected) | set(actual)))
        matches = {field: _semantic_equal(actual.get(field), expected.get(field)) for field in fields}
        score = sum(matches.values()) / len(matches) if matches else 0.0
        return ScoreResult(
            scorer_name=self.name,
            scorer_version=self.version,
            success=all(matches.values()) if matches else False,
            semantic_score=score,
            failure_class=None if score == 1.0 else "field_mismatch",
            metrics={"fields": matches, "required_fields": list(fields)},
        )


class SupportingFactRecallScorer(Scorer):
    name = "supporting_fact_recall"
    version = "1.0.0"

    def score(self, case: CanonicalCase, response: ProviderResponse) -> ScoreResult:
        expected = _as_set(case.expected)
        actual_value = response.parsed_answer
        if isinstance(actual_value, Mapping):
            actual_value = actual_value.get("supporting_facts", ())
        actual = _as_set(actual_value)
        recall = len(expected & actual) / len(expected) if expected else float(not actual)
        return ScoreResult(
            scorer_name=self.name,
            scorer_version=self.version,
            semantic_score=recall,
            success=recall == 1.0,
            metrics={"expected_count": len(expected), "matched_count": len(expected & actual)},
        )


class RetrievalRecallScorer(Scorer):
    name = "retrieval_recall"
    version = "1.0.0"

    def score(self, case: CanonicalCase, response: ProviderResponse) -> ScoreResult:
        expected = _as_set(case.expected)
        actual = _as_set(response.parsed_answer)
        matched = expected & actual
        recall = len(matched) / len(expected) if expected else 0.0
        return ScoreResult(
            scorer_name=self.name,
            scorer_version=self.version,
            semantic_score=recall,
            success=recall == 1.0,
            metrics={"expected": sorted(expected), "retrieved": sorted(actual), "matched": sorted(matched)},
        )


class NdcgScorer(Scorer):
    name = "ndcg"
    version = "1.0.0"

    def score(self, case: CanonicalCase, response: ProviderResponse) -> ScoreResult:
        relevance = _mapping(case.expected)
        ranking = _as_sequence(response.parsed_answer)
        actual = _dcg(ranking, relevance)
        ideal = _dcg(sorted(relevance, key=relevance.get, reverse=True), relevance)
        value = actual / ideal if ideal else 0.0
        return ScoreResult(
            scorer_name=self.name,
            scorer_version=self.version,
            semantic_score=value,
            metrics={"dcg": actual, "ideal_dcg": ideal},
        )


class SchemaValidityScorer(Scorer):
    name = "schema_validity"
    version = "1.0.0"

    def __init__(self, schema: dict[str, Any] | None = None) -> None:
        self.schema = schema

    def score(self, case: CanonicalCase, response: ProviderResponse) -> ScoreResult:
        schema = self.schema or case.metadata.get("response_schema")
        if not isinstance(schema, dict):
            raise ValueError("SchemaValidityScorer requires a JSON schema")
        try:
            validate_json(response.parsed_answer, schema)
        except ValidationError as error:
            return ScoreResult(
                scorer_name=self.name,
                scorer_version=self.version,
                success=False,
                schema_valid=False,
                failure_class="schema",
                metrics={"error": error.message},
            )
        return ScoreResult(
            scorer_name=self.name,
            scorer_version=self.version,
            success=True,
            schema_valid=True,
            semantic_score=1.0,
        )


class SemanticStructuredValueScorer(Scorer):
    name = "semantic_structured_value"
    version = "1.0.0"

    def __init__(self, numeric_tolerance: float = 1e-6) -> None:
        self.numeric_tolerance = numeric_tolerance

    def score(self, case: CanonicalCase, response: ProviderResponse) -> ScoreResult:
        matched = _semantic_equal(
            response.parsed_answer, case.expected, self.numeric_tolerance
        )
        return ScoreResult(
            scorer_name=self.name,
            scorer_version=self.version,
            success=matched,
            semantic_score=float(matched),
            failure_class=None if matched else "structured_value_mismatch",
        )


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _as_set(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        value = value.keys()
    if isinstance(value, str):
        value = [value]
    return {normalize_answer(item) for item in (value or ())}


def _as_sequence(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return [str(key) for key in value]
    if isinstance(value, str):
        return [value]
    return [str(item) for item in (value or ())]


def _dcg(ranking: list[str], relevance: Mapping[str, Any]) -> float:
    return sum(
        (2 ** float(relevance.get(item, 0)) - 1) / log2(index + 2)
        for index, item in enumerate(ranking)
    )


def _semantic_equal(actual: Any, expected: Any, tolerance: float = 1e-6) -> bool:
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(float(actual) - float(expected)) <= tolerance
    if isinstance(actual, Mapping) and isinstance(expected, Mapping):
        return set(actual) == set(expected) and all(
            _semantic_equal(actual[key], expected[key], tolerance) for key in actual
        )
    if isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(
            _semantic_equal(left, right, tolerance) for left, right in zip(actual, expected)
        )
    return normalize_answer(actual) == normalize_answer(expected)
