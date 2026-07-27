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

    def test_recorded_attempt_reconciles_a_stale_work_item(self) -> None:
        manifest = load_manifest(ROOT / "manifests/pr-smoke.yaml")
        with tempfile.TemporaryDirectory() as directory:
            with SqliteRunStore(Path(directory) / "runs.sqlite3") as store:
                resolved = manifest.resolved(("pr-001",))
                run_id = store.create_run(manifest, "phase6-test", resolved_manifest=resolved)
                request_hash = "a" * 64
                work_item = store.create_work_item(run_id, request_hash)
                self.assertEqual(work_item, store.claim_work_item(run_id, "owner"))
                store._connection.execute(
                    """
                    INSERT INTO attempts (
                        schema_version, attempt_id, run_id, case_id, condition_id,
                        model_id, model_version, provider, prompt_version, repeat_index,
                        request_hash, raw_request_uri, raw_response_uri, latency_ms,
                        token_counts_json, tool_calls_json, finish_reason, refusal,
                        response_id, from_cache, created_utc
                    ) VALUES ('1.0', 'attempt-1', ?, 'case-1', 'baseline',
                        'fake-1', 'fake/echo-v1', 'fake', 'test-v1', 0, ?,
                        'objects/request.json', 'objects/response.json', 1.0,
                        '{}', '[]', 'stop', 0, 'response-1', 0, '2026-01-01T00:00:00+00:00')
                    """,
                    (run_id, request_hash),
                )
                store._connection.commit()
                self.assertTrue(
                    store.reconcile_completed_work_item(work_item, run_id, request_hash)
                )
                self.assertTrue(store.has_recorded_attempt(run_id, request_hash))
                self.assertEqual(1, store.queue_counts(run_id)["completed"])

    def test_bulk_reconciliation_only_closes_rows_with_recorded_attempts(self) -> None:
        manifest = load_manifest(ROOT / "manifests/pr-smoke.yaml")
        with tempfile.TemporaryDirectory() as directory:
            with SqliteRunStore(Path(directory) / "runs.sqlite3") as store:
                resolved = manifest.resolved(("pr-001",))
                run_id = store.create_run(manifest, "phase6-test", resolved_manifest=resolved)
                recorded_hash = "b" * 64
                missing_hash = "c" * 64
                store.create_work_item(run_id, recorded_hash)
                store.create_work_item(run_id, missing_hash)
                store._connection.execute(
                    """
                    INSERT INTO attempts (
                        schema_version, attempt_id, run_id, case_id, condition_id,
                        model_id, model_version, provider, prompt_version, repeat_index,
                        request_hash, raw_request_uri, raw_response_uri, latency_ms,
                        token_counts_json, tool_calls_json, finish_reason, refusal,
                        response_id, from_cache, created_utc
                    ) VALUES ('1.0', 'attempt-2', ?, 'case-1', 'baseline',
                        'fake-1', 'fake/echo-v1', 'fake', 'test-v1', 0, ?,
                        'objects/request.json', 'objects/response.json', 1.0,
                        '{}', '[]', 'stop', 0, 'response-2', 0, '2026-01-01T00:00:00+00:00')
                    """,
                    (run_id, recorded_hash),
                )
                store._connection.commit()
                self.assertEqual(1, store.reconcile_recorded_work_items(run_id))
                self.assertEqual(
                    {"pending": 1, "leased": 0, "completed": 1, "failed": 0},
                    store.queue_counts(run_id),
                )


if __name__ == "__main__":
    unittest.main()
