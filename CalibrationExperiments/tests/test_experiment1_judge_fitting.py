from __future__ import annotations

import math

import numpy as np

from calibration.judge_fitting import (
    _decision,
    _encode,
    _fit_model,
    _fit_registered_alternatives,
    _objective_and_gradient,
    _predict,
    _raw_to_slopes,
    _slopes_to_raw,
    _tau_interval_is_interior,
)
from calibration import experiment1


def _row(model: str, case: str, success: bool | None) -> dict[str, object]:
    return {
        "model_id": model,
        "case_id": case,
        "dataset_id": "gsm8k",
        "intelligence_index": 35.0,
        "difficulty": 22.0,
        "tau_key": "sharp",
        "success": success,
    }


def test_monotone_transform_fixes_first_slope_and_preserves_order() -> None:
    slopes = _raw_to_slopes(np.array([-2.0, -1.0, 0.0, 1.0, 2.0]))
    assert slopes[0] == 1.0
    assert all(left <= right for left, right in zip(slopes[1:], slopes[2:]))
    assert all(value > 0 for value in slopes[1:])


def test_likelihood_gradient_includes_curve_tau_and_nuisance_effects() -> None:
    rows = tuple(
        _row(f"m{model}", f"c{case}", bool((model + case) % 2))
        for model in range(3)
        for case in range(5)
    )
    encoded = _encode(rows, "exclude")
    baseline = np.array([1.0, 1.4, 1.8, 2.2, 2.6, 3.0])
    parameters = np.concatenate(
        (
            _slopes_to_raw(baseline),
            np.array([0.0]),
            np.zeros(
                len(encoded.dataset_levels)
                + len(encoded.model_levels)
                + len(encoded.item_levels)
            ),
        )
    )
    _, gradient = _objective_and_gradient(
        parameters, encoded, True, True, baseline, 1.0
    )
    for index in range(6):
        delta = np.zeros_like(parameters)
        delta[index] = 1e-6
        upper = _objective_and_gradient(
            parameters + delta, encoded, True, True, baseline, 1.0
        )[0]
        lower = _objective_and_gradient(
            parameters - delta, encoded, True, True, baseline, 1.0
        )[0]
        assert math.isclose(
            gradient[index],
            (upper - lower) / 2e-6,
            rel_tol=1e-4,
            abs_tol=1e-5,
        )


def test_abstentions_are_excluded_or_relabelled_only_by_sensitivity_policy() -> None:
    rows = (_row("m1", "c1", True), _row("m1", "c2", None))
    assert len(_encode(rows, "exclude").success) == 1
    assert _encode(rows, "incorrect").success.tolist() == [1.0, 0.0]
    assert _encode(rows, "correct").success.tolist() == [1.0, 1.0]


def test_tau_interval_rejects_numerical_bounds() -> None:
    assert _tau_interval_is_interior(
        {"lower": 0.5, "upper": 2.0, "profile_scan_closed": True}
    )
    assert not _tau_interval_is_interior(
        {"lower": 0.01, "upper": 2.0, "profile_scan_closed": True}
    )
    assert not _tau_interval_is_interior(
        {"lower": 0.5, "upper": 100.0, "profile_scan_closed": True}
    )
    assert not _tau_interval_is_interior(
        {"lower": 0.5, "upper": 2.0, "profile_scan_closed": False}
    )


def test_ablation_attribution_requires_each_separate_holdout_gate() -> None:
    def result(log_loss: float, brier: float) -> dict[str, float]:
        return {"rows": 10, "log_loss": log_loss, "brier_score": brier}

    metrics = {
        "compiled": {
            "model_holdout": result(1.0, 0.25),
            "dataset_holdout": result(1.0, 0.25),
        },
        "tau_only": {
            "model_holdout": result(0.99, 0.249),
            "dataset_holdout": result(0.99, 0.249),
        },
        "curve_only": {
            "model_holdout": result(0.99, 0.249),
            "dataset_holdout": result(0.99, 0.249),
        },
        "full": {
            "model_holdout": result(0.95, 0.24),
            "dataset_holdout": result(0.95, 0.24),
        },
    }
    intervals = {
        "slopes": [
            {"lower": 1.0, "upper": 1.0, "profile_scan_closed": True},
            *(
                {
                    "lower": 1.0,
                    "upper": 2.0,
                    "profile_scan_closed": True,
                }
                for _ in range(5)
            ),
        ],
        "global_information_rank": 6,
        "expected_rank": 6,
    }
    decision = _decision(metrics, intervals, True)
    assert decision["decision"] == "change"
    assert decision["curve_supported"]
    assert decision["tau_supported"]


def test_fit_rows_never_include_locked_model_or_dataset_holdouts() -> None:
    rows = []
    for model in range(4):
        for case in range(12):
            row = _row(f"m{model}", f"c{case}", bool((model + case) % 2))
            row["dataset_holdout"] = case < 2
            row["model_holdout"] = model == 3
            rows.append(row)
    alternatives = _fit_registered_alternatives(tuple(rows), "exclude")
    assert all(
        not row["dataset_holdout"] and not row["model_holdout"]
        for row in alternatives["full"].encoded.raw
    )


def test_predictions_apply_fitted_nuisance_effects_for_seen_groups() -> None:
    rows = tuple(
        _row(f"m{model}", f"c{case}", model > 0)
        for model in range(2)
        for case in range(8)
    )
    fit = _fit_model(
        rows,
        "exclude",
        False,
        False,
        np.array([1.0, 1.4, 1.8, 2.2, 2.6, 3.0]),
        1.0,
    )
    seen = dict(rows[0])
    unseen = dict(rows[0]) | {
        "model_id": "unseen-model",
        "dataset_id": "unseen-dataset",
        "case_id": "unseen-case",
    }
    predictions = _predict(fit, (seen, unseen))
    assert predictions[0] != predictions[1]


def test_valid_recovery_verdict_replaces_invalid_original_including_abstain(
    monkeypatch,
) -> None:
    original = (
        {
            "source_attempt_id": "source-1",
            "schema_valid": False,
            "success": None,
        },
    )

    def fake_read(database, *, require_complete=True):
        assert require_complete
        return (
            (
                {
                    "source_attempt_id": "source-1",
                    "schema_valid": True,
                    "success": None,
                    "verdict": "abstain",
                },
            ),
            {"run_id": str(database), "manifest_hash": "hash"},
        )

    monkeypatch.setattr(experiment1, "_read_judge_scores", fake_read)
    merged = experiment1._merge_judge_recoveries(original, ("recovery",))
    assert merged[0]["schema_valid"]
    assert merged[0]["verdict"] == "abstain"
