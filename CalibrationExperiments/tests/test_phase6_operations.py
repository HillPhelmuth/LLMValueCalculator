from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from calibration.manifest import load_manifest
from calibration.models import Message, ProviderRequest, ProviderResponse
from calibration.monitoring import BudgetLedger, BudgetLimits, collect_run_metrics
from calibration.providers.retry import BudgetExceeded
from calibration.storage.sqlite import SqliteRunStore


ROOT = Path(__file__).resolve().parents[1]


class Phase6OperationsTests(unittest.TestCase):
    def test_budget_ledger_reserves_and_settles_with_ceiling(self) -> None:
        manifest = load_manifest(ROOT / "manifests/openrouter-smoke.yaml")
        request = ProviderRequest(
            case_id="case-1",
            model_id="openai/gpt-4o-mini",
            dated_model_version="openai/gpt-4o-mini",
            provider="openrouter",
            messages=(Message("user", "hello"),),
            temperature=0,
            max_output_tokens=8,
            reasoning_effort=None,
            condition_id="baseline",
            prompt_version="phase6",
            repeat_index=0,
        )
        limits = BudgetLimits(run_usd=0.5, experiment_usd=0.5, model_usd=0.5, daily_usd=0.5, max_requests=3, max_tokens=100)
        response = ProviderResponse(
            response_id="response-1",
            raw_response={"choices": []},
            parsed_answer="ok",
            finish_reason="stop",
            provider_cost=Decimal("0.25"),
            input_tokens=1,
            output_tokens=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            with SqliteRunStore(Path(directory) / "runs.sqlite3") as store:
                resolved = manifest.resolved(("case-1",))
                run_id = store.create_run(manifest, "phase6-test", resolved_manifest=resolved)
                ledger = BudgetLedger(limits)
                reservation = ledger.reserve(store, run_id, manifest, request, estimated_usd=0.4, token_count=9)
                self.assertEqual(0.25, ledger.settle(store, reservation, response))
                with self.assertRaises(BudgetExceeded):
                    ledger.reserve(store, run_id, manifest, request, estimated_usd=0.3, token_count=9)

    def test_status_metrics_include_queue_budget_and_alerts(self) -> None:
        manifest = load_manifest(ROOT / "manifests/pr-smoke.yaml")
        with tempfile.TemporaryDirectory() as directory:
            with SqliteRunStore(Path(directory) / "runs.sqlite3") as store:
                resolved = manifest.resolved(("pr-001",))
                run_id = store.create_run(manifest, "phase6-test", resolved_manifest=resolved)
                store.create_work_item(run_id, "request-hash")
                metrics = collect_run_metrics(store, run_id, expected_cells=2, stall_after_seconds=0)
                self.assertEqual(2, metrics.missing_cells)
                self.assertEqual(1, metrics.queue_depth)
                self.assertIn("stalled_work", metrics.alerts)

    def test_cancellation_is_resumable_and_recovers_leases(self) -> None:
        manifest = load_manifest(ROOT / "manifests/pr-smoke.yaml")
        with tempfile.TemporaryDirectory() as directory:
            with SqliteRunStore(Path(directory) / "runs.sqlite3") as store:
                resolved = manifest.resolved(("pr-001",))
                run_id = store.create_run(manifest, "phase6-test", resolved_manifest=resolved)
                work_item = store.create_work_item(run_id, "request-hash")
                self.assertEqual(work_item, store.claim_work_item(run_id, "owner"))
                store.cancel_run(run_id)
                self.assertTrue(store.cancellation_requested(run_id))
                store.resume_run(run_id, manifest, resolved)
                self.assertFalse(store.cancellation_requested(run_id))
                self.assertEqual(1, store.queue_counts(run_id)["pending"])


if __name__ == "__main__":
    unittest.main()
