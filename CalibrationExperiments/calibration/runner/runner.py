from __future__ import annotations

import asyncio
import hashlib
import json
import random
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from calibration.datasets.base import DatasetAdapter
from calibration.datasets.jsonl import JsonlDatasetAdapter
from calibration.manifest import ExperimentManifest, ModelConfig
from calibration.models import CanonicalCase, ProviderRequest, ProviderResponse
from calibration.providers.base import ModelProvider
from calibration.providers.compatibility import (
    compatibility_hash,
    estimate_request_tokens,
    validate_requests_against_catalog,
)
from calibration.providers.fake import FakeProvider
from calibration.providers.openrouter import OpenRouterProvider
from calibration.providers.openrouter_catalog import CatalogSnapshot
from calibration.providers.retry import (
    AsyncBudgetGate,
    BackoffPolicy,
    BudgetExceeded,
    classify_exception,
)
from calibration.providers.routing import build_provider_policy
from calibration.runner.limiter import AsyncTokenBucket
from calibration.scorers.base import Scorer
from calibration.scorers.registry import ScorerRegistry
from calibration.storage.artifacts import ArtifactStore
from calibration.storage.sqlite import SqliteRunStore


@dataclass(frozen=True, slots=True)
class WorkItem:
    model: ModelConfig
    case: CanonicalCase
    condition: str
    repeat_index: int


class CalibrationRunner:
    def __init__(
        self,
        manifest: ExperimentManifest,
        manifest_path: str | Path,
        store: SqliteRunStore,
        artifacts: ArtifactStore,
        dataset: DatasetAdapter | None = None,
        providers: dict[str, ModelProvider] | None = None,
        scorers: dict[str, Scorer] | None = None,
        catalog: CatalogSnapshot | None = None,
        max_workers: int = 8,
        transport_retries: int | None = None,
    ) -> None:
        self.manifest = manifest
        self.manifest_path = Path(manifest_path).resolve()
        self.store = store
        self.artifacts = artifacts
        self.dataset = dataset or self._create_dataset()
        if providers is None:
            configured: dict[str, ModelProvider] = {"fake": FakeProvider()}
            if any(model.provider == "openrouter" for model in manifest.models):
                configured["openrouter"] = OpenRouterProvider.from_settings(catalog=catalog)
            self.providers = configured
        else:
            self.providers = providers
        self.scorer_registry = ScorerRegistry(scorers)
        self.scorers = {
            name: self.scorer_registry.get(name) for name in manifest.scorers
        }
        self.catalog = catalog
        self.max_workers = max_workers
        self.transport_retries = (
            manifest.retries.transport_retries
            if transport_retries is None
            else transport_retries
        )
        self.backoff = BackoffPolicy(
            base_seconds=manifest.retries.backoff_seconds,
            max_seconds=30.0,
        )
        self.budget = AsyncBudgetGate(
            max_requests=manifest.budgets.max_requests,
            max_tokens=manifest.budgets.max_tokens,
        )
        self._provider_semaphores = {
            name: asyncio.Semaphore(provider.max_concurrency)
            for name, provider in self.providers.items()
        }
        self._provider_limiters = {
            name: AsyncTokenBucket(provider.requests_per_minute)
            for name, provider in self.providers.items()
        }
        self._validate_registry()

    async def run(
        self,
        resume_run_id: str | None = None,
        max_cases: int | None = None,
        code_commit: str | None = None,
    ) -> dict[str, object]:
        self.dataset.prepare()
        cases = list(self.dataset.cases(self.manifest.dataset.split))
        if self.manifest.dataset.sample_ids:
            by_id = {case.case_id: case for case in cases}
            missing = sorted(set(self.manifest.dataset.sample_ids) - set(by_id))
            if missing:
                raise ValueError(f"Locked sample IDs are missing from the dataset: {missing}")
            cases = [by_id[case_id] for case_id in self.manifest.dataset.sample_ids]
        if max_cases is not None:
            cases = cases[:max_cases]
        self._validate_cases(cases)
        work_items = self._build_work_items(cases)
        resolved_manifest = self.manifest.validate_for_queue(
            tuple(case.case_id for case in cases)
        )
        if self.catalog is not None:
            compatibility = validate_requests_against_catalog(
                tuple(self._request_for_item(item) for item in work_items),
                self.catalog,
            )
            resolved_manifest["model_compatibility"] = [
                result.to_json() for result in compatibility
            ]
            resolved_manifest["catalog_snapshot_hash"] = self.catalog.snapshot_hash
            resolved_manifest["catalog_snapshot_id"] = self.catalog.snapshot_id
            resolved_manifest["compatibility_hash"] = compatibility_hash(compatibility)
            resolved_manifest["resolved_manifest_hash"] = _hash_resolved(resolved_manifest)

        if resume_run_id:
            run_id = resume_run_id
            self.store.resume_run(run_id, self.manifest, resolved_manifest)
        else:
            run_id = self.store.create_run(
                self.manifest,
                code_commit=code_commit or _git_commit(self.manifest_path.parent),
                resolved_manifest=resolved_manifest,
                dependency_lock=_dependency_lock_path(self.manifest_path),
                catalog_snapshot_hash=None if self.catalog is None else self.catalog.snapshot_hash,
            )

        for case in cases:
            self.store.put_case_features(run_id, self.dataset.metadata(case))

        completed = self.store.completed_request_hashes(run_id)
        random.Random(self.manifest.dataset.sample_seed).shuffle(work_items)
        queue: asyncio.Queue[WorkItem] = asyncio.Queue()
        for item in work_items:
            queue.put_nowait(item)

        async def worker() -> None:
            while True:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    await self._run_item(run_id, item, completed)
                finally:
                    queue.task_done()

        try:
            workers = [
                asyncio.create_task(worker())
                for _ in range(min(self.max_workers, max(1, len(work_items))))
            ]
            try:
                await asyncio.gather(*workers)
            except Exception:
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                raise
            self.store.mark_completed(run_id)
        except Exception as error:
            self.store.mark_failed(run_id, f"{type(error).__name__}: {error}")
            raise

        return self.store.run_summary(run_id)

    def _create_dataset(self) -> DatasetAdapter:
        if self.manifest.dataset.adapter == "jsonl":
            return JsonlDatasetAdapter(
                self.manifest.dataset,
                manifest_directory=self.manifest_path.parent,
            )
        raise ValueError(f"Unknown dataset adapter: {self.manifest.dataset.adapter}")

    def _validate_registry(self) -> None:
        missing_providers = {
            model.provider for model in self.manifest.models
        } - self.providers.keys()
        if missing_providers:
            raise ValueError(f"Unregistered providers: {sorted(missing_providers)}")
        missing_scorers = set(self.manifest.scorers) - self.scorers.keys()
        if missing_scorers:
            raise ValueError(f"Unregistered scorers: {sorted(missing_scorers)}")

    @staticmethod
    def _validate_cases(cases: list[CanonicalCase]) -> None:
        if not cases:
            raise ValueError("Dataset produced no cases for the configured split")
        identifiers = [case.case_id for case in cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Dataset case IDs must be unique within a split")

    def _build_work_items(self, cases: list[CanonicalCase]) -> list[WorkItem]:
        return [
            WorkItem(model, case, condition, repeat_index)
            for model in self.manifest.models
            for case in cases
            for condition in self.manifest.conditions
            for repeat_index in range(self.manifest.generation.repeats)
        ]

    def _request_for_item(self, item: WorkItem) -> ProviderRequest:
        metadata = item.case.metadata
        tools = metadata.get("tools", ())
        if not isinstance(tools, (list, tuple)):
            raise ValueError(f"Case {item.case.case_id} tools metadata must be an array")
        response_format = metadata.get("response_format")
        if response_format is not None and not isinstance(response_format, dict):
            raise ValueError(
                f"Case {item.case.case_id} response_format must be an object"
            )
        return ProviderRequest(
            case_id=item.case.case_id,
            model_id=item.model.catalog_id,
            dated_model_version=item.model.provider_model,
            provider=item.model.provider,
            messages=self.dataset.render(item.case, item.condition),
            temperature=self.manifest.generation.temperature,
            max_output_tokens=self.manifest.generation.max_output_tokens,
            reasoning_effort=self.manifest.generation.reasoning_effort,
            condition_id=item.condition,
            prompt_version=self.manifest.prompt_version,
            repeat_index=item.repeat_index,
            tools=tuple(dict(tool) for tool in tools),
            tool_choice=metadata.get("tool_choice"),
            response_format=response_format,
            provider_routing=build_provider_policy(self.manifest.routing),
        )

    async def _run_item(
        self,
        run_id: str,
        item: WorkItem,
        completed: set[str],
    ) -> None:
        request = self._request_for_item(item)
        work_item_id = self.store.create_work_item(run_id, request.request_hash)
        if request.request_hash in completed:
            return
        owner = f"runner-{uuid.uuid4()}"
        if self.store.claim_work_item(
            run_id, owner, work_item_id=work_item_id
        ) != work_item_id:
            return
        raw_request_uri = self.artifacts.put_json(asdict(request))
        cached_uri = self.store.cached_response_uri(request.request_hash)
        if cached_uri:
            response = ProviderResponse.from_json(self.artifacts.get_json(cached_uri))
            from_cache = True
        else:
            response = await self._call_provider(run_id, request)
            serialized_request = getattr(
                self.providers[request.provider], "serialized_request_for", lambda _: None
            )(request.request_hash)
            if serialized_request:
                raw_request_uri = self.artifacts.put_json(
                    json.loads(serialized_request)
                )
            normalized_response_uri = self.artifacts.put_json(response.to_json())
            self.store.cache_response(request, normalized_response_uri, response)
            from_cache = False

        raw_response_uri = self.artifacts.put_json(response.raw_response)
        scores = tuple(
            self.scorers[name].score(item.case, response)
            for name in self.manifest.scorers
        ) + self.dataset.score(item.case, response)
        score_keys = [(score.scorer_name, score.scorer_version) for score in scores]
        if len(score_keys) != len(set(score_keys)):
            raise ValueError("Scorer registry produced duplicate score keys")
        self.store.record_attempt_with_scores(
            run_id=run_id,
            case_id=item.case.case_id,
            model=item.model,
            request=request,
            response=response,
            raw_request_uri=raw_request_uri,
            raw_response_uri=raw_response_uri,
            scores=scores,
            from_cache=from_cache,
        )
        self.store.complete_work_item(work_item_id, owner)
        completed.add(request.request_hash)

    async def _call_provider(
        self, run_id: str, request: ProviderRequest
    ) -> ProviderResponse:
        provider = self.providers[request.provider]
        for transport_attempt in range(self.transport_retries + 1):
            await self._provider_limiters[request.provider].acquire()
            try:
                await self.budget.reserve(
                    tokens=estimate_request_tokens(request) + request.max_output_tokens
                )
                async with self._provider_semaphores[request.provider]:
                    response = await provider.complete(request)
                self.store.record_transport_event(
                    run_id,
                    request.request_hash,
                    request.provider,
                    transport_attempt,
                    "success",
                )
                return response
            except BudgetExceeded:
                self.store.record_transport_event(
                    run_id,
                    request.request_hash,
                    request.provider,
                    transport_attempt,
                    "budget_exhausted",
                    error_type="BudgetExceeded",
                    error_message="provider request budget exhausted",
                )
                raise
            except Exception as error:
                classification = classify_exception(error)
                if not classification.retryable or transport_attempt >= self.transport_retries:
                    self.store.record_transport_event(
                        run_id,
                        request.request_hash,
                        request.provider,
                        transport_attempt,
                        "failed",
                        status_code=classification.status_code,
                        retry_after_seconds=classification.retry_after_seconds,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
                    raise
                delay = self.backoff.delay(
                    transport_attempt,
                    retry_after_seconds=classification.retry_after_seconds,
                )
                self.store.record_transport_event(
                    run_id,
                    request.request_hash,
                    request.provider,
                    transport_attempt,
                    "retry",
                    status_code=classification.status_code,
                    retry_after_seconds=classification.retry_after_seconds,
                    delay_seconds=delay,
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                await asyncio.sleep(delay)
        raise RuntimeError("Provider call exited without a response")


def _git_commit(start: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=start,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _hash_resolved(value: dict[str, object]) -> str:
    material = dict(value)
    material.pop("resolved_manifest_hash", None)
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _dependency_lock_path(manifest_path: Path) -> Path | None:
    for parent in (manifest_path.parent, *manifest_path.parents):
        candidate = parent / "uv.lock"
        if candidate.is_file():
            return candidate
    return None
