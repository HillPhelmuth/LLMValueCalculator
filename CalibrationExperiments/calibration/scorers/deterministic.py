from __future__ import annotations

import re
import unicodedata
from collections import Counter

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
