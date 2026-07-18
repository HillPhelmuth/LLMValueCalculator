from __future__ import annotations

import asyncio
import random
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from calibration.datasets.base import DatasetAdapter
from calibration.datasets.jsonl import JsonlDatasetAdapter
from calibration.manifest import ExperimentManifest, ModelConfig
from calibration.models import CanonicalCase, ProviderRequest, ProviderResponse
from calibration.providers.base import ModelProvider
from calibration.providers.fake import FakeProvider
from calibration.runner.limiter import AsyncTokenBucket
from calibration.scorers.base import Scorer
from calibration.scorers.deterministic import ExactMatchScorer, TokenF1Scorer
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
        max_workers: int = 8,
        transport_retries: int = 2,
    ) -> None:
        self.manifest = manifest
        self.manifest_path = Path(manifest_path).resolve()
        self.store = store
        self.artifacts = artifacts
        self.dataset = dataset or self._create_dataset()
        self.providers = providers or {"fake": FakeProvider()}
        self.scorers = scorers or {
            "answer_exact_match": ExactMatchScorer(),
            "answer_token_f1": TokenF1Scorer(),
        }
        self.max_workers = max_workers
        self.transport_retries = transport_retries
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
        if max_cases is not None:
            cases = cases[:max_cases]
        self._validate_cases(cases)

        if resume_run_id:
            run_id = resume_run_id
            resolved_manifest = self.manifest.validate_for_queue(
                tuple(case.case_id for case in cases)
            )
            self.store.resume_run(run_id, self.manifest, resolved_manifest)
        else:
            resolved_manifest = self.manifest.validate_for_queue(
                tuple(case.case_id for case in cases)
            )
            run_id = self.store.create_run(
                self.manifest,
                code_commit=code_commit or _git_commit(self.manifest_path.parent),
                resolved_manifest=resolved_manifest,
                dependency_lock=self.manifest_path.parent / "uv.lock",
            )

        for case in cases:
            self.store.put_case_features(run_id, self.dataset.metadata(case))

        completed = self.store.completed_request_hashes(run_id)
        work_items = self._build_work_items(cases)
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

    async def _run_item(
        self,
        run_id: str,
        item: WorkItem,
        completed: set[str],
    ) -> None:
        request = ProviderRequest(
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
        )
        if request.request_hash in completed:
            return

        raw_request_uri = self.artifacts.put_json(asdict(request))
        cached_uri = self.store.cached_response_uri(request.request_hash)
        if cached_uri:
            response = ProviderResponse.from_json(self.artifacts.get_json(cached_uri))
            from_cache = True
        else:
            response = await self._call_provider(request)
            normalized_response_uri = self.artifacts.put_json(response.to_json())
            self.store.cache_response(request, normalized_response_uri, response)
            from_cache = False

        raw_response_uri = self.artifacts.put_json(response.raw_response)
        scores = tuple(
            self.scorers[name].score(item.case, response)
            for name in self.manifest.scorers
        ) + self.dataset.score(item.case, response)
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
        completed.add(request.request_hash)

    async def _call_provider(self, request: ProviderRequest) -> ProviderResponse:
        provider = self.providers[request.provider]
        for transport_attempt in range(self.transport_retries + 1):
            await self._provider_limiters[request.provider].acquire()
            try:
                async with self._provider_semaphores[request.provider]:
                    return await provider.complete(request)
            except Exception:
                if transport_attempt >= self.transport_retries:
                    raise
                await asyncio.sleep(0.25 * (2**transport_attempt))
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
