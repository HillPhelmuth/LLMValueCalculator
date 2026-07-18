from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

from calibration.profile import baseline_profile
from calibration.promotion import (
    PromotionEvidence,
    PromotionError,
    PromotionStore,
    check_promotion,
)


def _evidence(candidate_hash: str, baseline_hash: str) -> PromotionEvidence:
    return PromotionEvidence(
        candidate_profile_hash=candidate_hash,
        baseline_profile_hash=baseline_hash,
        sign_agreement=0.9,
        held_out_improvement=0.03,
        duplicate_pathways=0,
        material_recommendation_fraction=0.1,
        intervals={"intercept": [0.1, 0.2]},
        calibration_cards=("card.json",),
        scenario_diff="diff.json",
        provenance_ids=("prov-1",),
        cost_usd=1.25,
        limitations=("synthetic smoke only",),
        reviewers=({"id": "reviewer-1", "decision": "approved"},),
    )


def test_promotion_checks_and_append_only_rollback() -> None:
    baseline = baseline_profile()
    candidate = replace(baseline, profile_version="candidate-1.0.0", profile_hash="")
    evidence = _evidence(candidate.profile_hash, baseline.profile_hash)
    check = check_promotion(candidate, baseline, evidence)
    assert check.passed

    with tempfile.TemporaryDirectory() as directory:
        store = PromotionStore(Path(directory) / "profiles")
        promoted = store.promote(
            candidate,
            baseline,
            evidence,
            application_directory=Path(directory) / "app",
        )
        assert promoted["index"]["active_profile_hash"] == candidate.profile_hash
        assert (Path(directory) / "app/CalibrationProfile.generated.cs").is_file()
        rolled_back = store.rollback(baseline.profile_hash)
        assert rolled_back["index"]["active_profile_hash"] == baseline.profile_hash
        assert [event["event"] for event in store.history()["history"]] == ["promote", "rollback"]


def test_promotion_rejects_duplication_or_missing_review() -> None:
    baseline = baseline_profile()
    candidate = replace(baseline, profile_version="candidate-1.0.1", profile_hash="")
    evidence = _evidence(candidate.profile_hash, baseline.profile_hash)
    bad = replace(evidence, duplicate_pathways=1, reviewers=())
    check = check_promotion(candidate, baseline, bad)
    assert not check.passed
    assert not check.checks["no_duplication"]
    assert not check.checks["reviewer_approval"]
    with tempfile.TemporaryDirectory() as directory:
        with unittest.TestCase().assertRaises(PromotionError):
            PromotionStore(Path(directory) / "profiles").promote(candidate, baseline, bad)
