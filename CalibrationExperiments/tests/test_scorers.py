from __future__ import annotations

import unittest

from calibration.models import CanonicalCase, ProviderResponse
from calibration.scorers.deterministic import ExactMatchScorer, TokenF1Scorer


class DeterministicScorerTests(unittest.TestCase):
    def test_exact_match_normalizes_case_whitespace_and_punctuation(self) -> None:
        case = CanonicalCase("1", {}, "Kansas City")
        response = ProviderResponse("r1", {}, "  KANSAS, city! ", "stop")

        score = ExactMatchScorer().score(case, response)

        self.assertTrue(score.success)
        self.assertEqual(1.0, score.semantic_score)

    def test_token_f1_preserves_partial_credit(self) -> None:
        case = CanonicalCase("1", {}, "alpha beta gamma")
        response = ProviderResponse("r1", {}, "alpha beta delta", "stop")

        score = TokenF1Scorer().score(case, response)

        self.assertAlmostEqual(2 / 3, score.semantic_score or 0)
        self.assertIsNone(score.success)


if __name__ == "__main__":
    unittest.main()

