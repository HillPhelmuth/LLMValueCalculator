import json
from dataclasses import replace
from urllib.error import HTTPError

import pytest

from calibration.experiment1 import (
    EXPERIMENT_ONE_JUDGE_MODEL_ID,
    EXPERIMENT_ONE_MAX_OUTPUT_TOKENS,
    EXPERIMENT_ONE_RECOVERY_MAX_OUTPUT_TOKENS,
    ExperimentOneError,
    FrozenCase,
    PanelCandidate,
    estimate_spend,
    freeze_cases,
    select_cost_diverse_panel,
    select_model_holdouts,
    validate_experiment_one_credentials,
    write_fitting_prior_map,
    _usable_recovery,
    _normalize_rows,
    _judge_rows,
)
from calibration.models import CanonicalCase, ProviderResponse
from calibration.scorers.judge import LlmSemanticCorrectnessScorer


def test_experiment_one_reserves_reasoning_and_final_answer_budget() -> None:
    assert EXPERIMENT_ONE_MAX_OUTPUT_TOKENS == 2048
    assert EXPERIMENT_ONE_RECOVERY_MAX_OUTPUT_TOKENS == 4096


def test_fitting_prior_map_is_hashed_and_recovery_requires_a_final_answer(tmp_path) -> None:
    path = write_fitting_prior_map(tmp_path / "priors.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["tau_ratio"] == {"normal": 5.0, "sharp": 3.0, "soft": 8.0}
    assert len(document["prior_map_hash"]) == 64
    assert _usable_recovery({"success": 1, "schema_valid": 1, "finish_reason": "stop", "content_json": '"final"'})
    assert not _usable_recovery({"success": 1, "schema_valid": 1, "finish_reason": "length", "content_json": '"final"'})
    assert not _usable_recovery({"success": 1, "schema_valid": 1, "finish_reason": "stop", "content_json": '""'})


def test_judge_rows_are_blinded_and_self_judging_is_external_metadata() -> None:
    source = {
        "attempt_id": "attempt-1",
        "run_id": "run-1",
        "model_id": EXPERIMENT_ONE_JUDGE_MODEL_ID,
        "case_id": "case-1",
        "repeat_index": 0,
        "content_json": json.dumps("The final answer is B."),
    }
    cases = {
        "case-1": {
            "case_id": "case-1",
            "prompt": "Choose A or B.",
            "expected": "B",
            "metadata": {
                "dataset_id": "unit",
                "task_family": "classification",
            },
        }
    }
    row = _judge_rows((source,), cases, "main", "c" * 64)[0]
    assert EXPERIMENT_ONE_JUDGE_MODEL_ID not in row["prompt"]
    grading_input = json.loads(row["prompt"].split("<grading_input>\n", 1)[1].split("\n</grading_input>", 1)[0])
    assert set(grading_input) == {"reference_answer", "model_response"}
    assert grading_input == {
        "reference_answer": "B",
        "model_response": "The final answer is B.",
    }
    assert "Choose A or B." not in row["prompt"]
    assert row["metadata"]["self_judged"] is True
    assert row["metadata"]["source_model_id"] == EXPERIMENT_ONE_JUDGE_MODEL_ID
    assert row["metadata"]["response_format"]["type"] == "json_schema"


def test_llm_judge_scorer_only_parses_structured_verdict() -> None:
    case = CanonicalCase(
        "judge:main:a",
        {},
        None,
        {
            "source_attempt_id": "a",
            "source_run_id": "r",
            "source_response_hash": "h",
            "self_judged": True,
        },
    )
    valid = ProviderResponse(
        "judge-response",
        {},
        json.dumps(
            {
                "verdict": "correct",
                "confidence": 0.95,
                "reason_code": "semantic_equivalent",
                "extracted_final_answer": "B",
                "brief_rationale": "The final answer is equivalent.",
            }
        ),
        "stop",
    )
    score = LlmSemanticCorrectnessScorer().score(case, valid)
    assert score.success is True
    assert score.schema_valid is True
    assert score.metrics["self_judged"] is True

    invalid = ProviderResponse("bad", {}, "not json", "stop")
    failed = LlmSemanticCorrectnessScorer().score(case, invalid)
    assert failed.success is None
    assert failed.schema_valid is False


def _cases(dataset: str, family: str, count: int) -> list[FrozenCase]:
    return [
        FrozenCase(
            f"{dataset}:{i}", dataset, family, f"question {i}", str(i), family, "rev"
        )
        for i in range(count)
    ]


def test_freeze_exact_stratification_holdouts_and_repeats() -> None:
    rows = {
        "mmlu": _cases("mmlu", "knowledge", 300),
        "gpqa": _cases("gpqa", "knowledge", 300),
        "gsm8k": _cases("gsm8k", "mathematics", 500),
        "proofwriter": _cases("proofwriter", "logic", 500),
        "pubmedqa": _cases("pubmedqa", "domain", 250),
        "finqa": _cases("finqa", "domain", 250),
        "legalbench": _cases("legalbench", "classification", 500),
    }
    first = freeze_cases(rows)
    second = freeze_cases(rows)
    assert first == second
    assert len(first) == 2000
    assert sum(row.repeat_selected for row in first) == 400
    assert {row.dataset_id for row in first if row.split == "held_out"} == {
        "gpqa",
        "legalbench",
    }
    assert {
        family: sum(row.task_family == family for row in first)
        for family in {row.task_family for row in first}
    } == {
        "knowledge": 400,
        "mathematics": 400,
        "logic": 400,
        "domain": 400,
        "classification": 400,
    }


def test_cost_diverse_panel_holdouts_and_budget_gate() -> None:
    candidates = []
    for band in range(10, 60, 10):
        for index, provider in enumerate(("a", "b", "a")):
            candidates.append(
                PanelCandidate(
                    f"m-{band}-{index}",
                    f"provider/m-{band}-{index}",
                    provider,
                    band + index + 1,
                    0.01 + index,
                    0.02 + index,
                    "2026-01-01",
                    "reviewed-map",
                )
            )
    panel = select_cost_diverse_panel(candidates)
    assert len(panel) == 10
    assert all(
        len({row.provider for row in panel if row.band == band}) == 2
        for band in range(10, 60, 10)
    )
    assert len(select_model_holdouts(panel)) == 3
    estimate = estimate_spend(
        panel, prompt_tokens_per_call=500, completion_tokens_per_call=20
    )
    assert estimate.calls == 32_000
    with pytest.raises(ExperimentOneError, match="exceeds hard limit"):
        estimate_spend(
            tuple(replace(row, completion_cost_per_million=10_000) for row in panel),
            prompt_tokens_per_call=500,
            completion_tokens_per_call=500,
        )


def test_freeze_rejects_duplicate_ids() -> None:
    rows = {f"d{i}": _cases(f"d{i}", f"f{i}", 400) for i in range(5)}
    rows["d1"][0] = replace(rows["d1"][0], case_id=rows["d0"][0].case_id)
    with pytest.raises(ExperimentOneError, match="Duplicate"):
        freeze_cases(rows)


def test_normalization_deterministically_excludes_duplicate_source_ids() -> None:
    spec = {"task_family": "logic", "revision": "pinned"}
    rows = [
        {"id": "same", "question": "B?", "theory": "B.", "answer": True},
        {"id": "same", "question": "A?", "theory": "A.", "answer": False},
    ]
    normalized, exclusions = _normalize_rows("proofwriter", spec, rows)
    assert len(normalized) == 1
    assert normalized[0].prompt.endswith("Question: A?")
    assert exclusions == {"malformed": 0, "duplicate_id": 1}


class _CredentialResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _size: int) -> bytes:
        return b"x"


def test_credentials_are_both_validated_before_acquisition(tmp_path) -> None:
    registry = tmp_path / "sources.yaml"
    registry.write_text(
        "sources:\n  gpqa:\n    source_url: https://huggingface.co/pinned.csv\n",
        encoding="utf-8",
    )
    requests = []

    def opener(request, *, timeout):
        requests.append((request.full_url, request.headers, timeout))
        return _CredentialResponse()

    result = validate_experiment_one_credentials(
        registry,
        hf_token="hf-secret",
        openrouter_api_key="or-secret",
        opener=opener,
    )
    assert result == {"gpqa": "authenticated", "openrouter": "authenticated"}
    assert [row[0] for row in requests] == [
        "https://huggingface.co/pinned.csv",
        "https://openrouter.ai/api/v1/key",
    ]


def test_failed_gpqa_gate_stops_before_openrouter_or_acquisition(tmp_path) -> None:
    registry = tmp_path / "sources.yaml"
    registry.write_text(
        "sources:\n  gpqa:\n    source_url: https://huggingface.co/pinned.csv\n",
        encoding="utf-8",
    )
    calls = 0

    def opener(request, *, timeout):
        nonlocal calls
        calls += 1
        raise HTTPError(request.full_url, 403, "Forbidden", {}, None)

    with pytest.raises(ExperimentOneError, match="GPQA access agreement"):
        validate_experiment_one_credentials(
            registry,
            hf_token="hf-secret",
            openrouter_api_key="or-secret",
            opener=opener,
        )
    assert calls == 1
