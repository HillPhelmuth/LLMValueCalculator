from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx

from calibration.manifest import RoutingConfig
from calibration.manifest import load_manifest
from calibration.models import ProviderResponse
from calibration.model_mapping import (
    ArtificialAnalysisMapping,
    ArtificialAnalysisSnapshot,
    MappingError,
)
from calibration.model_panel import (
    EligibilityError,
    ModelEligibilityRules,
    select_model_panel,
)
from calibration.models import Message, ProviderRequest
from calibration.providers.compatibility import (
    CompatibilityError,
    validate_request_compatibility,
)
from calibration.providers.base import ModelProvider
from calibration.providers.cost import calculate_catalog_cost
from calibration.providers.openrouter import (
    OpenRouterProvider,
    build_openrouter_request,
)
from calibration.providers.openrouter_catalog import (
    CatalogSchemaError,
    CatalogSnapshot,
    OpenRouterCatalogClient,
)
from calibration.providers.retry import BackoffPolicy, classify_exception
from calibration.providers.routing import build_provider_policy
from calibration.preflight import run_preflight
from calibration.datasets.jsonl import JsonlDatasetAdapter
from calibration.storage.artifacts import ArtifactStore
from calibration.storage.sqlite import SqliteRunStore
from calibration.runner.runner import CalibrationRunner


FIXTURES = Path(__file__).parent / "fixtures" / "openrouter"


class Phase2ProviderTests(unittest.TestCase):
    def test_catalog_client_paginates_and_preserves_decimal_pricing(self) -> None:
        pages = [
            json.loads((FIXTURES / "models-page-0.json").read_text()),
            json.loads((FIXTURES / "models-page-1.json").read_text()),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            index = int(request.url.params.get("offset", "0")) // 1
            payload = pages[index] if index < len(pages) else {"data": []}
            return httpx.Response(200, json=payload, request=request)

        async def run() -> object:
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            catalog_client = OpenRouterCatalogClient(
                "test-key", http_client=client, page_size=1
            )
            snapshot = await catalog_client.fetch_snapshot()
            await client.aclose()
            return snapshot

        snapshot = asyncio.run(run())
        assert isinstance(snapshot, CatalogSnapshot)
        self.assertEqual(2, len(snapshot.models))
        self.assertEqual(Decimal("0.00000125"), snapshot.models[0].pricing["prompt"])
        self.assertEqual(64, len(snapshot.snapshot_hash))
        with tempfile.TemporaryDirectory() as directory:
            uri = snapshot.persist(ArtifactStore(directory))
            self.assertEqual(snapshot.to_json(), ArtifactStore(directory).get_json(uri))

    def test_catalog_schema_and_http_failures_are_explicit(self) -> None:
        async def run_invalid() -> None:
            async def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    content=(FIXTURES / "invalid.json").read_bytes(),
                    request=request,
                )

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                with self.assertRaises(CatalogSchemaError):
                    await OpenRouterCatalogClient(
                        "key", http_client=client
                    ).fetch_page()
            finally:
                await client.aclose()

        asyncio.run(run_invalid())

    def test_model_panel_requires_mapping_and_balanced_experiment_one_bands(
        self,
    ) -> None:
        raw_models = []
        mappings = []
        for band in range(10, 70, 10):
            for index in range(2):
                model_id = f"provider/model-{band}-{index}-2026"
                raw_models.append(
                    {
                        "id": model_id,
                        "canonical_slug": model_id,
                        "context_length": 8192,
                        "supported_parameters": ["temperature"],
                        "pricing": {
                            "prompt": "0.000001",
                            "completion": "0.000002",
                            "overrides": [
                                {"min_prompt_tokens": 272000, "prompt": "0.000002"}
                            ],
                        },
                        "top_provider": {"max_completion_tokens": 4096},
                        "versioned": True,
                    }
                )
                mappings.append(
                    ArtificialAnalysisMapping(
                        stable_catalog_id=f"stable-{band}-{index}",
                        openrouter_id=model_id,
                        aa_model_id=f"aa-{band}-{index}",
                        aa_model_version="2026-01",
                        snapshot_date="2026-07-01",
                        intelligence_index=Decimal(str(band + index)),
                        coding_index=Decimal("50"),
                        agentic_index=Decimal("50"),
                        cost_index=Decimal("50"),
                        source_citations=("https://artificialanalysis.ai/",),
                    )
                )
        catalog = CatalogSnapshot.from_pages(({"data": raw_models},))
        aa_snapshot = ArtificialAnalysisSnapshot.from_mappings(
            tuple(mappings),
            snapshot_date="2026-07-01",
            source_citations=("https://artificialanalysis.ai/",),
            catalog=catalog,
        )
        panel = select_model_panel(
            catalog,
            aa_snapshot,
            ModelEligibilityRules(
                experiment_id="experiment-1",
                as_of_date="2026-07-18",
                minimum_models=10,
                required_context_length=4096,
                required_output_tokens=1024,
                required_parameters=("temperature",),
            ),
        )
        self.assertEqual(10, len(panel.selected))
        self.assertTrue(all(item["selection_reason"] for item in panel.selected))
        self.assertEqual(64, len(panel.panel_hash))
        changed_snapshot = ArtificialAnalysisSnapshot.from_mappings(
            (replace(mappings[0], intelligence_index=Decimal("99")), *mappings[1:]),
            snapshot_date="2026-07-01",
            source_citations=("https://artificialanalysis.ai/",),
            catalog=catalog,
        )
        self.assertNotEqual(aa_snapshot.snapshot_hash, changed_snapshot.snapshot_hash)
        with self.assertRaises(MappingError):
            ArtificialAnalysisSnapshot.from_mappings(
                (mappings[0], mappings[0]),
                snapshot_date="2026-07-01",
                source_citations=("source",),
            )

        one_band_catalog = CatalogSnapshot.from_pages(({"data": raw_models[:-3]},))
        with self.assertRaises(EligibilityError):
            select_model_panel(
                one_band_catalog,
                aa_snapshot,
                ModelEligibilityRules(
                    experiment_id="experiment-1",
                    as_of_date="2026-07-18",
                    minimum_models=1,
                ),
            )

    def test_routing_and_openrouter_request_contract(self) -> None:
        routing = build_provider_policy(
            RoutingConfig(
                provider_order=("Provider A",),
                endpoint="Provider A",
                quantization="fp16",
                require_parameters=True,
            )
        )
        request = ProviderRequest(
            case_id="case-1",
            model_id="provider/model-2026",
            dated_model_version="provider/model-2026",
            provider="openrouter",
            messages=(Message("user", "hello"),),
            temperature=0,
            max_output_tokens=32,
            reasoning_effort="medium",
            condition_id="baseline",
            prompt_version="prompt-v1",
            repeat_index=0,
            tools=({"type": "function", "function": {"name": "lookup"}},),
            tool_choice="auto",
            response_format={"type": "json_object"},
            provider_routing=routing,
        )
        payload = build_openrouter_request(request)
        self.assertEqual("provider/model-2026", payload["model"])
        self.assertEqual(routing, payload["extra_body"]["provider"])
        self.assertFalse(payload.get("stream", False))

    def test_openrouter_provider_normalizes_usage_routing_and_cost(self) -> None:
        captured: dict[str, object] = {}

        class Completions:
            async def create(self, **kwargs: object) -> dict[str, object]:
                captured.update(kwargs)
                return {
                    "id": "resp-1",
                    "model": "provider/model-2026",
                    "provider": "Provider A",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": "answer",
                                "refusal": None,
                                "tool_calls": None,
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 2,
                        "prompt_tokens_details": {"cached_tokens": 1},
                        "completion_tokens_details": {"reasoning_tokens": 3},
                        "cost": "0.00001234",
                    },
                    "router_metadata": {"endpoint": "Provider A"},
                }

        provider = OpenRouterProvider(
            client=SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        )
        request = ProviderRequest(
            "case-1",
            "model-1",
            "model-1",
            "openrouter",
            (Message("user", "hi"),),
            0,
            16,
            None,
            "baseline",
            "p1",
            0,
        )
        response = asyncio.run(provider.complete(request))
        self.assertEqual("answer", response.parsed_answer)
        self.assertEqual(Decimal("0.00001234"), response.provider_cost)
        self.assertEqual(1, response.cached_tokens)
        self.assertEqual("Provider A", response.resolved_provider)
        self.assertEqual("Provider A", response.endpoint)
        self.assertEqual(False, captured["stream"])

    def test_compatibility_and_retry_classification(self) -> None:
        entry = CatalogSnapshot.from_pages(
            (json.loads((FIXTURES / "models-page-0.json").read_text()),)
        ).models[0]
        request = ProviderRequest(
            "case-1",
            entry.id,
            entry.id,
            "openrouter",
            (Message("user", "hello"),),
            0,
            9000,
            None,
            "baseline",
            "p1",
            0,
        )
        result = validate_request_compatibility(request, entry)
        self.assertFalse(result.compatible)
        self.assertIn("provider maximum", " ".join(result.errors))
        with self.assertRaises(CompatibilityError):
            from calibration.providers.compatibility import (
                validate_requests_against_catalog,
            )

            validate_requests_against_catalog(
                (request,),
                CatalogSnapshot.from_pages(
                    (json.loads((FIXTURES / "models-page-0.json").read_text()),)
                ),
            )
        self.assertEqual(
            0.8, BackoffPolicy(jitter_fraction=0).delay(0, retry_after_seconds=0.8)
        )
        error = SimpleNamespace(
            status_code=429, response=SimpleNamespace(headers={"Retry-After": "2"})
        )
        classification = classify_exception(error)
        self.assertTrue(classification.retryable)
        self.assertEqual(2.0, classification.retry_after_seconds)
        total, breakdown = calculate_catalog_cost(
            {
                "prompt": Decimal("0.000001"),
                "completion": Decimal("0.000002"),
                "input_cache_read": Decimal("0.0000005"),
                "internal_reasoning": Decimal("0.000003"),
                "image": Decimal("0.01"),
                "web_search": Decimal("0.02"),
                "request": Decimal("0.000004"),
            },
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "prompt_tokens_details": {"cached_tokens": 2},
                "completion_tokens_details": {"reasoning_tokens": 1},
                "image_tokens": 1,
                "web_search_queries": 1,
                "request_count": 1,
            },
        )
        self.assertEqual(Decimal("0.030026"), total)
        self.assertEqual(Decimal("0.000004"), breakdown.request)

    def test_preflight_reports_machine_readable_cost_and_canary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_path = root / "cases.jsonl"
            dataset_path.write_text(
                '{"case_id":"1","prompt":"hello","expected":"answer"}\n',
                encoding="utf-8",
            )
            import hashlib

            revision = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
            manifest_path = root / "manifest.yaml"
            manifest_path.write_text(
                f"""
experiment_id: preflight-v1
dataset:
  adapter: jsonl
  revision: sha256:{revision}
  split: validation
  sample_seed: 1
  options:
    path: cases.jsonl
models:
  - catalog_id: provider/model-2026-01
    provider: openrouter
    provider_model: provider/model-2026-01
    aa_snapshot: 2026-07-01
generation:
  temperature: 0
  max_output_tokens: 32
conditions: [baseline]
prompt_version: preflight-v1
scorers: [answer_exact_match]
""",
                encoding="utf-8",
            )
            manifest = load_manifest(manifest_path)
            dataset = JsonlDatasetAdapter(manifest.dataset, root)
            catalog = CatalogSnapshot.from_pages(
                (json.loads((FIXTURES / "models-page-0.json").read_text()),)
            )
            report = asyncio.run(run_preflight(manifest, dataset, catalog))
            self.assertTrue(report.passed)
            self.assertGreater(report.estimated_cost, Decimal("0"))
            self.assertEqual("pass", report.to_json()["status"])

    def test_runner_retries_transport_without_changing_experimental_attempt(
        self,
    ) -> None:
        class RateLimitError(Exception):
            status_code = 429

            def __init__(self) -> None:
                self.response = SimpleNamespace(headers={"Retry-After": "0"})

        class FlakyProvider(ModelProvider):
            name = "flaky"
            max_concurrency = 1

            def __init__(self) -> None:
                self.calls = 0

            async def complete(self, request: ProviderRequest) -> ProviderResponse:
                self.calls += 1
                if self.calls == 1:
                    raise RateLimitError()
                return ProviderResponse(
                    response_id="retry-success",
                    raw_response={"id": "retry-success"},
                    parsed_answer="answer",
                    finish_reason="stop",
                    input_tokens=1,
                    output_tokens=1,
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_path = root / "cases.jsonl"
            dataset_path.write_text(
                '{"case_id":"1","prompt":"hello","expected":"answer"}\n',
                encoding="utf-8",
            )
            import hashlib

            revision = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
            manifest_path = root / "manifest.yaml"
            manifest_path.write_text(
                f"""
experiment_id: retry-v1
dataset:
  adapter: jsonl
  revision: sha256:{revision}
  split: validation
  sample_seed: 1
  options:
    path: cases.jsonl
models:
  - catalog_id: flaky-model
    provider: flaky
    provider_model: flaky/model-2026
    aa_snapshot: 2026-07-01
generation:
  temperature: 0
  max_output_tokens: 16
conditions: [baseline]
prompt_version: retry-v1
scorers: [answer_exact_match]
retries:
  transport_retries: 1
  backoff_seconds: 0
""",
                encoding="utf-8",
            )
            manifest = load_manifest(manifest_path)
            provider = FlakyProvider()
            with SqliteRunStore(root / "runs.sqlite3") as store:
                summary = asyncio.run(
                    CalibrationRunner(
                        manifest,
                        manifest_path,
                        store,
                        ArtifactStore(root / "objects"),
                        providers={"flaky": provider},
                    ).run(code_commit="test")
                )
                self.assertEqual(2, provider.calls)
                self.assertEqual(1, summary["attempts"])
                events = store._connection.execute(
                    "SELECT event_type FROM transport_events ORDER BY created_utc"
                ).fetchall()
                self.assertEqual(["retry", "success"], [row[0] for row in events])
                repeat_index = store._connection.execute(
                    "SELECT repeat_index FROM attempts"
                ).fetchone()[0]
                self.assertEqual(0, repeat_index)


if __name__ == "__main__":
    unittest.main()
