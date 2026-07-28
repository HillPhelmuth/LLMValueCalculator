from __future__ import annotations

import json
from typing import Any

from calibration.models import CanonicalCase, ProviderResponse, ScoreResult
from calibration.scorers.base import Scorer


VERDICTS = {"correct", "incorrect", "abstain"}
REASON_CODES = {
    "exact_answer",
    "semantic_equivalent",
    "choice_equivalent",
    "numeric_equivalent",
    "contradiction",
    "incomplete_or_no_answer",
    "ambiguous",
    "other",
}


class LlmSemanticCorrectnessScorer(Scorer):
    """Parse a structured LLM judgment without making a semantic decision."""

    name = "llm_semantic_correctness"
    version = "1.0.0"

    def score(
        self, case: CanonicalCase, response: ProviderResponse
    ) -> ScoreResult:
        try:
            value = _parse_object(response.parsed_answer)
            verdict = str(value["verdict"])
            reason_code = str(value["reason_code"])
            confidence = float(value["confidence"])
            extracted = value.get("extracted_final_answer")
            rationale = str(value["brief_rationale"])
            if verdict not in VERDICTS:
                raise ValueError(f"unsupported verdict: {verdict}")
            if reason_code not in REASON_CODES:
                raise ValueError(f"unsupported reason_code: {reason_code}")
            if not 0 <= confidence <= 1:
                raise ValueError("confidence must be in [0, 1]")
            if extracted is not None and not isinstance(extracted, str):
                raise ValueError("extracted_final_answer must be a string or null")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            return ScoreResult(
                scorer_name=self.name,
                scorer_version=self.version,
                success=None,
                acceptable=None,
                schema_valid=False,
                semantic_score=None,
                failure_class="invalid_judge_output",
                metrics={
                    **_source_metrics(case),
                    "parse_error": str(error),
                    "judge_validation_status": "not_run_by_policy",
                },
            )

        success = None if verdict == "abstain" else verdict == "correct"
        return ScoreResult(
            scorer_name=self.name,
            scorer_version=self.version,
            success=success,
            acceptable=success,
            schema_valid=True,
            semantic_score=None if success is None else float(success),
            failure_class=None if success else (
                "judge_abstained" if verdict == "abstain" else "answer_mismatch"
            ),
            metrics={
                **_source_metrics(case),
                "verdict": verdict,
                "confidence": confidence,
                "reason_code": reason_code,
                "extracted_final_answer": extracted,
                "brief_rationale": rationale,
                "judge_validation_status": "not_run_by_policy",
            },
        )


def _parse_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        raise TypeError("judge output must be a JSON object")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise TypeError("judge output must be a JSON object")
    return parsed


def _source_metrics(case: CanonicalCase) -> dict[str, Any]:
    names = (
        "source_run_id",
        "source_attempt_id",
        "source_response_hash",
        "source_model_id",
        "source_repeat_index",
        "source_case_id",
        "judge_lock_hash",
        "self_judged",
        "task_family",
        "source_dataset_id",
    )
    return {name: case.metadata.get(name) for name in names}
