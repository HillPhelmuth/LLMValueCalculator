# End-to-End Calibration Experiments Checklist

This checklist covers the work required to run all calibration experiments unattended, fit candidate parameters, generate reproducible reports, and promote an immutable calibration profile into the LLM Value Calculator.

All tasks begin unchecked. Existing harness code may satisfy part of a task, but a task should be checked only after every acceptance criterion has been verified and its implementation details have been recorded.

## Required provider architecture

- Discover the current model catalog from OpenRouter's [`GET /api/v1/models`](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties) endpoint during run preparation.
- Execute model inference through the Python OpenAI SDK using `base_url="https://openrouter.ai/api/v1"`, as documented in OpenRouter's [OpenAI SDK quickstart](https://openrouter.ai/docs/quickstart#using-the-openai-sdk).
- Use exact model IDs frozen into a run lock. Do not fit calibration parameters from rolling aliases unless the resolved model version is captured and immutable.
- Capture model, endpoint, routing, pricing, token, latency, and response provenance needed to reproduce and audit each attempt.

## Phase 1: Project foundation and reproducibility

### - [x] T001 - Define the supported runtime and dependency lock

**Description**

Define the Python runtime, package manager, production dependencies, development dependencies, and platform assumptions for the calibration harness.

**Acceptance Criteria**

- The Python minor version is pinned.
- All direct and transitive dependencies are locked with hashes or an equivalent reproducible lock format.
- A clean environment can install the project and run the smoke suite from documented commands.
- Dependency vulnerability and license checks are available.

**Implementation Instructions**

Choose one package workflow, generate its lock file, pin the OpenAI SDK and data-science stack, and add commands for install, lock refresh, vulnerability scanning, and license reporting. Verify on Linux and the intended CI runner.

**Implementation Details**

Pinned Python to 3.12.x and direct dependencies in `pyproject.toml`; generated the checked-in `uv.lock` with transitive resolution and hashes. Added locked install, smoke, Ruff, `pip-audit`, and `pip-licenses` commands in `Readme.md` and `scripts/`, plus the pull-request CI workflow that runs the locked smoke suite.

### - [x] T002 - Freeze language-neutral schemas

**Description**

Create versioned schemas for manifests, runs, work items, attempts, scores, case features, model snapshots, fitted estimates, and calibration profiles.

**Acceptance Criteria**

- JSON Schema files exist for every persisted record type.
- Schema versions are included in every persisted record and export.
- Backward-compatible and breaking-change policies are documented.
- Valid and invalid fixtures are covered by tests.

**Implementation Instructions**

Generate or hand-maintain schemas from the Python models, prohibit unknown fields where appropriate, add semantic constraints, and validate records before storage and before profile promotion.

**Implementation Details**

Added JSON Schema 2020-12 definitions under `calibration/schemas/` for manifests, resolved manifests, runs, work items, attempts, scores, case features, model snapshots, fitted estimates, calibration profiles, provenance, and artifact metadata. Added runtime validation, `schema_version: "1.0"` persistence/export fields, compatibility policy documentation, and valid/invalid fixture tests.

### - [x] T003 - Complete immutable manifest validation

**Description**

Extend experiment manifests to lock every input that can change an outcome.

**Acceptance Criteria**

- Manifests lock dataset revisions, model snapshot, prompts, scorers, conditions, generation parameters, routing policy, seeds, sample IDs, and container digests.
- The canonical manifest hash is stable across equivalent YAML formatting.
- Missing locks or unsupported fields fail before work is queued.
- A resolved manifest is saved with each run.

**Implementation Instructions**

Add nested manifest models for prompts, routing, budgets, holdouts, retries, containers, and fitting. Resolve defaults into an explicit manifest, hash its canonical JSON, and make the resolved copy immutable.

**Implementation Details**

Extended strict manifests with routing, budget, holdout, retry, container, fitting, prompt, scorer, and sample locks. `validate_for_queue()` resolves dataset case IDs, condition hashes, prompt/scorer locks, and defaults into a canonical resolved document with a stable hash; the resolved JSON and hash are stored with every run. Pydantic rejects unsupported fields before queue creation, and container/image mismatches fail before dispatch.

### - [x] T004 - Implement secure configuration and secret handling

**Description**

Provide configuration for local, CI, nightly, and approved full runs without persisting credentials.

**Acceptance Criteria**

- `OPENROUTER_API_KEY` is read only from environment or an approved secret store.
- Secrets are absent from manifests, requests, artifacts, logs, exceptions, and test snapshots.
- Startup fails clearly when required configuration is missing.
- Secret-redaction tests cover headers, URLs, exception text, and nested payloads.

**Implementation Instructions**

Create typed settings, validate at startup, centralize redaction, disable dumping SDK client configuration, and add CI secret-scanning rules.

**Implementation Details**

Added typed environment configuration with explicit `OPENROUTER_API_KEY` validation, safe configuration views, recursive redaction for secret keys, bearer/API-key values, URLs, nested payloads, and failure messages, and artifact-side redaction. Added regression tests and a Gitleaks GitHub Actions workflow; credentials are never included in manifests, request records, artifacts, or failure state.

### - [x] T005 - Complete run-state persistence and migrations

**Description**

Make SQLite or PostgreSQL the durable source of truth for queue state, attempts, scores, and run lifecycle.

**Acceptance Criteria**

- Schema migrations are versioned and tested from an empty and previous database.
- Runs support created, running, pausing, completed, failed, and cancelled states.
- Work-item leases recover safely after process termination.
- Database constraints prevent duplicate logical attempts and orphaned scores.

**Implementation Instructions**

Introduce migration tooling, explicit column lists, transaction boundaries, lease timestamps, heartbeat ownership, and recovery queries. Add crash-and-resume integration tests.

**Implementation Details**

Replaced implicit `CREATE TABLE` setup with schema migrations 1–3 and a legacy upgrade path tested from empty and pre-migration SQLite files. Added full run lifecycle states, heartbeats, cancellation, durable work-item leases with ownership/expiry recovery, unique `(run_id, request_hash)` attempts, foreign-keyed scores, and fitted-estimate lineage.

### - [x] T006 - Complete content-addressed artifact storage

**Description**

Persist raw requests, raw responses, parsed outputs, prompt renders, logs, and generated reports by content hash.

**Acceptance Criteria**

- Writes are atomic and verified by SHA-256 after readback.
- Artifact URIs remain stable across resumed runs.
- Metadata records media type, byte length, compression, schema version, and creation time.
- Corrupt or missing artifacts fail validation before fitting.

**Implementation Instructions**

Define an artifact-store interface with local and object-storage implementations, stream large content, add optional compression, and implement an integrity-audit command.

**Implementation Details**

Reworked `ArtifactStore` around SHA-256 content addresses, atomic temp-file replacement, optional deterministic gzip, metadata sidecars, media type/length/compression/schema/timestamp fields, and readback verification. Added an integrity audit that detects corruption, missing objects, and missing metadata; resumed requests retain the same URIs.

### - [x] T007 - Add immutable Parquet analytical exports

**Description**

Export normalized run data into language-neutral Parquet files for fitting and independent analysis.

**Acceptance Criteria**

- Runs, attempts, scores, case features, and model snapshots export to documented Parquet schemas.
- Exports are deterministic for unchanged database content.
- Row counts and key relationships reconcile with the run database.
- Export hashes are recorded in the run summary.

**Implementation Instructions**

Use PyArrow, define stable column types and nullability, partition only where useful, write reconciliation checks, and never read mutable application tables directly during fitting.

**Implementation Details**

Added deterministic PyArrow exports for runs, attempts, scores, case features, model snapshots, and fitted estimates, with stable schemas, ordering, nullability, relationship reconciliation, immutable write behavior, and SHA-256 hashes recorded in `run_exports` and run summaries. Added `calibration export` and documented the export boundary for later fitting.

### - [x] T008 - Implement end-to-end provenance capture

**Description**

Capture enough provenance to explain and reproduce every work item and fitted coefficient.

**Acceptance Criteria**

- Provenance includes code commit, manifest hash, dependency lock hash, dataset and prompt hashes, model snapshot hash, scorer versions, container digests, and environment metadata.
- Every score traces to one attempt and every estimate traces to source rows.
- Reports expose provenance without exposing secrets.
- A provenance audit detects incomplete lineage.

**Implementation Instructions**

Create a lineage model and foreign keys, propagate identifiers through the runner, include source-row references in fit outputs, and add an audit CLI command.

**Implementation Details**

Added run provenance records containing code commit, manifest and dependency-lock hashes, dataset revisions, prompt hashes, model snapshot hash, scorer versions, container digests, and sanitized environment metadata. Foreign keys connect runs, model snapshots, attempts, scores, and estimates; run summaries and Parquet provenance exports expose the sanitized lineage, while source-row validation and `calibration audit` detect incomplete lineage without exposing secrets.

## Phase 2: OpenRouter model discovery and inference

### - [x] T009 - Implement the OpenRouter model-catalog client

**Description**

Fetch all currently available models and their properties from OpenRouter before resolving a run.

**Acceptance Criteria**

- The client calls `GET https://openrouter.ai/api/v1/models` with pagination support.
- It records the raw response and normalized fields including `id`, `canonical_slug`, creation and expiration dates, context length, modalities, supported parameters, pricing, and top-provider limits.
- HTTP failures and invalid response schemas are handled explicitly.
- Unit tests use recorded contract fixtures rather than live calls.

**Implementation Instructions**

Build an authenticated async HTTP client separate from inference, follow `offset` and `limit` pagination, validate decimal pricing without floating-point loss, and persist a timestamped immutable catalog snapshot.

**Implementation Details**

Added the authenticated async `OpenRouterCatalogClient` for `GET https://openrouter.ai/api/v1/models` with `offset`/`limit` pagination, explicit HTTP/schema errors, recorded raw pages, normalized catalog entries, Decimal pricing, provider limits, modalities, and supported parameters. Catalogs become timestamped content-addressed immutable snapshots; recorded contract fixtures cover pagination, invalid payloads, and persistence.

### - [x] T010 - Define current-model eligibility rules

**Description**

Select an up-to-date, reproducible model panel appropriate for each experiment.

**Acceptance Criteria**

- Rules exclude expired, unavailable, incompatible, and unversioned rolling models unless their resolved version is captured.
- Experiment 1 includes exactly 10 models, with two models in each populated 10–19, 20–29, 30–39, 40–49, and 50–59 Artificial Analysis intelligence band.
- Required context, output, tool, JSON, and modality capabilities are enforced per experiment.
- Selection reasons and exclusions are stored in the model snapshot.

**Implementation Instructions**

Implement declarative filters over the live catalog, join models to the Artificial Analysis snapshot, produce a reviewable candidate table, and freeze selected OpenRouter IDs before any inference.

**Implementation Details**

Implemented declarative freshness, availability, version, context, output, parameter, tool, JSON, and modality filters in `select_model_panel()`. Experiment 1 now requires exactly 10 models: two in each AA band 10-19, 20-29, 30-39, 40-49, and 50-59. Selection takes the lowest catalog-cost route in each band and prefers a different OpenRouter organization for the second route. The frozen panel is `calibration/data/experiment_1_panel.json`; validation produced 10 models across five bands and nine OpenRouter organizations. Explicit eligibility and exclusion records remain part of the hashed panel snapshot.

### - [x] T011 - Build the Artificial Analysis model mapping and snapshot

**Description**

Map OpenRouter model IDs to the exact Artificial Analysis model versions and indices used by the calculator.

**Acceptance Criteria**

- Every fitted model has an unambiguous OpenRouter-to-Artificial-Analysis mapping.
- Raw intelligence, coding, agentic, cost, and snapshot date fields used by an experiment are preserved.
- Ambiguous aliases are excluded or resolved through a reviewed override.
- Mapping changes produce a new snapshot hash.

**Implementation Instructions**

Create a versioned mapping file with stable catalog IDs, source citations, manual-override rationale, and validation against both catalogs. Prevent silent many-to-one mappings.

**Implementation Details**

Added versioned `ArtificialAnalysisMapping`/`ArtificialAnalysisSnapshot` records and populated `calibration/data/artificial_analysis_mapping.yaml` with the 10 selected OpenRouter routes and their dated AA intelligence, coding, agentic, and cost values. Validation rejects duplicate OpenRouter or AA version mappings, missing citations, and catalog-absent IDs. Validation against the live OpenRouter catalog produced catalog hash `ac70d491659abe1c7dcfb231da287e85655f42a7ff7ffb0a421ad4d4b9551a26` and AA snapshot hash `fe55f37cae6bc7c76a36521289dfbfb9197967b98b0e740f0ff017f38cfa4913`.

### - [x] T012 - Implement deterministic OpenRouter routing locks

**Description**

Control OpenRouter endpoint routing so provider changes do not silently alter calibration outcomes.

**Acceptance Criteria**

- Calibration requests set an explicit provider policy through the OpenRouter `provider` request object.
- Fitted runs disable fallbacks or record every resolved fallback as a separate endpoint stratum.
- `require_parameters` is enabled when parameter support affects the treatment.
- Provider order, data-collection policy, ZDR policy, quantization, and endpoint selection are stored in the resolved manifest.

**Implementation Instructions**

Use OpenAI SDK `extra_body` for OpenRouter routing fields, prefer an exact provider endpoint when available, set `allow_fallbacks: false` for primary calibration fits, and persist router metadata returned with each response.

**Implementation Details**

Added routing translation to the OpenRouter `provider` request object, including provider order, exact endpoint selection, fallback policy, parameter requirements, data-collection/ZDR policy, and quantization. Resolved manifests record the complete routing object; fitted requests disable fallbacks by default, and normalized router metadata/fallback resolution is retained in attempts.

### - [x] T013 - Implement the OpenRouter provider with the OpenAI SDK

**Description**

Add the production inference adapter using the async Python OpenAI SDK pointed at OpenRouter.

**Acceptance Criteria**

- The adapter uses `AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=...)`.
- It supports messages, tools, tool choice, response formats, temperature, maximum output tokens, reasoning settings, and provider routing through SDK-supported fields and `extra_body`.
- It maps SDK responses into the provider-neutral attempt record without losing raw response data.
- Optional `HTTP-Referer` and `X-OpenRouter-Title` headers are configurable.

**Implementation Instructions**

Implement the existing `ModelProvider` contract, keep streaming disabled for deterministic batch runs unless explicitly tested, capture the serialized request before sending, and add mocked SDK contract tests.

**Implementation Details**

Implemented `OpenRouterProvider` with `AsyncOpenAI(base_url="https://openrouter.ai/api/v1")`, non-streaming chat completions, messages/tools/tool choice/response formats/reasoning/routing, optional attribution headers, exact serialized request capture, and provider-neutral response mapping. The adapter preserves raw SDK JSON and maps content, refusal, tool calls, finish reason, nullable usage, routing, and cost metadata; mocked SDK contract tests cover the path.

### - [x] T014 - Validate model parameter compatibility before execution

**Description**

Prevent runs from sending unsupported or semantically inconsistent parameters to selected models.

**Acceptance Criteria**

- Manifest parameters are checked against each model's `supported_parameters` and provider limits.
- Context and output token limits are validated before queue creation.
- Unsupported tool, structured-output, reasoning, or sampling settings fail with a model-specific explanation.
- Compatibility results are stored with the resolved model lock.

**Implementation Instructions**

Build a capability validator from the OpenRouter catalog snapshot, account for provider-specific maximum completion tokens, and require explicit overrides for known catalog inconsistencies.

**Implementation Details**

Added catalog-driven compatibility checks for context/output limits, sampling, reasoning, tools, structured output, tool choice, routing-required parameters, and provider maxima. `CalibrationRunner` validates every resolved request cell before queue creation and stores normalized compatibility results plus a compatibility hash in the resolved manifest; errors identify the model and failed capability.

### - [x] T015 - Normalize OpenRouter response, routing, usage, and cost data

**Description**

Capture all response fields needed for scoring, reliability analysis, and cost validation.

**Acceptance Criteria**

- Attempts store response ID, resolved model, resolved provider or endpoint, finish reason, refusal, content, tool calls, token usage, reasoning tokens, cached tokens, latency, and reported cost where available.
- Raw provider response and router metadata are preserved.
- Calculated cost is reconciled with the frozen catalog pricing and differences are flagged.
- Missing accounting fields remain distinguishable from zero.

**Implementation Instructions**

Extend `ProviderResponse` and the storage schema, parse OpenRouter-specific fields from SDK extras or raw JSON, use `Decimal` for money, and add cost-reconciliation tests for prompt, completion, cached, reasoning, image, and request charges.

**Implementation Details**

Extended response and attempt records with resolved model/provider/endpoint, content, router metadata, nullable token fields, cached/reasoning usage, reported/calculated cost, and reconciliation status while preserving raw response artifacts. Added Decimal cost calculation for prompt, completion, cached, reasoning, image, web-search, and request charges, with explicit missing-versus-zero handling and mismatch tests.

### - [x] T016 - Implement OpenRouter-aware retry and throttling policy

**Description**

Handle transport failures, rate limits, and transient provider failures without contaminating experimental retries.

**Acceptance Criteria**

- Transport retries respond to retryable status codes and `Retry-After` guidance with bounded exponential backoff and jitter.
- Authentication, validation, budget, and permanent model errors fail immediately.
- Provider concurrency and request/token budgets are configurable.
- Transport retries are logged separately and never increment experimental attempt numbers.

**Implementation Instructions**

Classify OpenAI SDK exceptions and OpenRouter error payloads, add per-provider token buckets and global budget gates, record each transport event, and test rate-limit recovery with a fake clock.

**Implementation Details**

Added status/header-aware retry classification, Retry-After parsing, bounded exponential backoff with jitter, permanent-error fast failure, provider concurrency/request/token gates, and durable transport-event records. Transport retries are distinct from experimental repeats and are tested with a simulated 429: the retry and success events are recorded while repeat index remains unchanged.

### - [x] T017 - Add OpenRouter connectivity and budget preflight

**Description**

Validate credentials, selected models, parameters, routing, and estimated spend before a paid run starts.

**Acceptance Criteria**

- Preflight performs catalog validation and one configurable low-cost canary call.
- It estimates input, output, tool, retry, and repeat costs from the resolved workload.
- Runs over the configured budget require an explicit approval artifact.
- Preflight emits a machine-readable pass or fail report without exposing the API key.

**Implementation Instructions**

Add a `preflight` CLI command, tokenize or conservatively estimate every rendered request, use catalog pricing, include contingency, and block queue creation until all checks pass.

**Implementation Details**

Added the `preflight` CLI command and machine-readable report covering credentials, dataset/catalog checks, request compatibility, optional low-output canary calls, input/output/tool/retry/repeat estimates, Decimal spend, request/token budgets, and approval artifacts for over-budget runs. OpenRouter runs refresh or load the catalog and must pass preflight before queue creation; reports and errors never expose the API key.

## Phase 3: Dataset, prompt, perturbation, and scoring platform

### - [x] T018 - Build the dataset registry and acquisition pipeline

**Description**

Provide one controlled mechanism to download, verify, license, cache, and identify every public dataset.

**Acceptance Criteria**

- Each dataset entry records source URL, license or terms, revision, file hashes, splits, and adapter version.
- Downloads are repeatable and verify content before use.
- Public test labels that are unavailable are never scraped or inferred.
- Offline reuse works after a successful preparation step.

**Implementation Instructions**

Create registry metadata, preparation scripts, hash verification, local caching, and license notices. Use validation splits where labels are public and freeze local train, validation, and holdout partitions.

**Implementation Details**

Implemented `DatasetSpec`, `DatasetRegistry`, and `DatasetAcquirer` with reviewed source/license metadata, SHA-256 revision locks, atomic downloads, immutable revision-addressed caching, offline reuse, license notices, and preparation lock files. Added the `prepare-dataset` CLI command and contract tests for hash verification and cache reuse.

### - [x] T019 - Finalize the canonical dataset-adapter contract

**Description**

Standardize case loading, rendering, metadata, and dataset-specific scoring across all benchmarks.

**Acceptance Criteria**

- Every adapter implements `prepare`, `cases`, `render`, `metadata`, and optional dataset scoring.
- Case IDs are stable and unique across revisions and splits.
- Adapter conformance tests validate deterministic ordering, rendering, and metadata completeness.
- Invalid or drifted datasets fail before inference.

**Implementation Instructions**

Create a registry and conformance test suite, add typed canonical cases and feature enums, and make adapter output independent of provider SDK objects.

**Implementation Details**

Strengthened the provider-neutral `DatasetAdapter` contract with adapter versions and `validate_adapter()` conformance checks for deterministic case IDs/order, metadata completeness, stable rendering, and non-empty splits. The JSONL adapter now supports explicit unlabeled rows without fabricating holdout labels; `check-adapter` exposes the conformance suite from the CLI.

### - [x] T020 - Implement deterministic sampling and holdout freezing

**Description**

Create auditable samples and leakage-resistant holdouts for fitting and final evaluation.

**Acceptance Criteria**

- Sample membership is frozen as explicit case-ID files, not regenerated from a seed alone.
- Entire datasets and at least 25 percent of model versions are held out for experiment 1.
- Other experiments hold out complete tasks, datasets, or matched groups as specified in the plan.
- Holdout labels remain inaccessible to fitting code until final evaluation.

**Implementation Instructions**

Implement stratified sampling, materialize membership locks, hash them, separate fitting and evaluation commands, and add leakage checks for duplicate or near-duplicate prompts.

**Implementation Details**

Added deterministic stratified sampling, explicit fit/holdout membership locks, materialized ID files, membership hashes, holdout dataset locks, label-hiding views, and exact/near-duplicate leakage checks. Runner queue construction honors manifest `sample_ids`, preventing seed-only regeneration from changing a locked sample.

### - [x] T021 - Build the prompt registry and rendering locks

**Description**

Version all prompts, tool definitions, schemas, and formatting instructions independently of code.

**Acceptance Criteria**

- Every prompt has a stable ID, semantic version, content hash, task family, and supported conditions.
- Fully rendered messages and tools are stored before inference.
- Prompt changes require a new version and invalidate incompatible cache entries.
- Tests cover escaping, ordering, schemas, and condition substitution.

**Implementation Instructions**

Store templates as data files, use a deterministic renderer, forbid runtime timestamps or unordered mappings, and add prompt snapshot tests.

**Implementation Details**

Added a data-backed `PromptRegistry` with prompt IDs, semantic versions, task families, supported conditions/features, tools, response schemas, content hashes, deterministic variable rendering, and rendered-message hashes. Escaping, missing variables, ordering, and baseline-condition requirements are validated; a prompt registry template is included.

### - [x] T022 - Implement the paired-condition perturbation framework

**Description**

Generate controlled variants that preserve the underlying answer while changing one intended treatment.

**Acceptance Criteria**

- Perturbations declare inputs, deterministic seed, treatment metadata, invariants, and output hash.
- Paired variants share a stable parent case ID.
- Invariant checks reject answer-changing or malformed variants.
- Work-item scheduling preserves the pairing needed by statistical models.

**Implementation Instructions**

Create a perturbation protocol and registry, implement composable transforms, persist treatment assignments, and test repeatability and answer invariance.

**Implementation Details**

Implemented versioned treatment specifications, deterministic derived seeds, registered composable transforms, stable parent/variant IDs, treatment metadata, output hashes, invariant checks, and paired work grouping. Whitespace and choice-order transforms are included with repeatability and answer-invariance tests.

### - [x] T023 - Complete the deterministic scorer registry

**Description**

Version and standardize all non-executable deterministic metrics used by the experiments.

**Acceptance Criteria**

- Registry includes normalized exact match, token F1, classification accuracy, field-level comparison, supporting-fact recall, retrieval recall, nDCG, schema validity, and semantic structured-value comparison.
- Each scorer records its name, version, configuration, thresholds, and metric payload.
- Golden fixtures cover edge cases and benchmark-compatible normalization.
- Multiple scorers can score one attempt without key collisions.

**Implementation Instructions**

Separate metric values from success decisions, implement benchmark-published normalization where required, and freeze scorer configuration into the manifest.

**Implementation Details**

Expanded the scorer registry with normalized exact match, token F1, classification accuracy, field comparison, supporting-fact recall, retrieval recall, nDCG, JSON-schema validity, and semantic structured-value comparison. Registry locks include implementation and configuration hashes, scores retain detailed metric payloads, and duplicate scorer keys are rejected.

### - [x] T024 - Implement executable and state-based scorers

**Description**

Score code, SQL, tools, and agents through unit tests or final-state comparison.

**Acceptance Criteria**

- Code scorers report test-group pass fractions and separate critical from noncritical tests.
- Tool scorers compare expected calls, arguments, ordering, and final environment state.
- Timeouts, resource exhaustion, policy violations, and infrastructure faults are distinct outcomes.
- Scoring is deterministic from stored response and environment inputs.

**Implementation Instructions**

Build benchmark-specific runner images and state comparators, emit structured test results, and keep execution infrastructure errors outside model-failure labels.

**Implementation Details**

Added structured executable reports with test-group fractions, critical-test fractions, timeout/resource/policy/infrastructure outcomes, and deterministic code scoring. Added tool/state scoring for expected calls, arguments, ordering, and final state while keeping infrastructure failures distinct from model failures.

### - [x] T025 - Build network-disabled execution sandboxes

**Description**

Safely execute untrusted model-generated code and deterministic tool environments.

**Acceptance Criteria**

- Containers run without network, privileges, host mounts, or writable shared state.
- CPU, memory, process, disk, and wall-clock limits are enforced.
- Images are pinned by digest and scanned for vulnerabilities.
- Escape and resource-exhaustion tests pass.

**Implementation Instructions**

Create minimal images for APPS, BigCodeBench, BFCL, tau-bench, and JSON tooling; run with read-only roots and ephemeral scratch space; record image digest and execution limits per attempt.

**Implementation Details**

Implemented a Docker sandbox policy and runner requiring digest-pinned, vulnerability-scan-locked images; network `none`, read-only roots, dropped capabilities, no-new-privileges, no host mounts, ephemeral no-exec scratch, and CPU/memory/process/disk/wall limits. Results classify timeout, resource exhaustion, and infrastructure failures separately; policy and command-construction tests cover escape-safety controls.

### - [x] T026 - Define failure taxonomy and criticality labeling

**Description**

Standardize success, good, acceptable, benign failure, critical failure, and infrastructure failure across datasets.

**Acceptance Criteria**

- Failure classes include provider, truncation, refusal, parse, schema, semantic, grounding, tool, policy, state, and infrastructure failures.
- Criticality rules are pre-registered by dataset and task family.
- One attempt can retain detailed metrics while producing one unambiguous outcome class.
- Interactions with validators and human approval are recorded separately from correctness.

**Implementation Instructions**

Create enums and precedence rules, add dataset-specific critical-field metadata, validate mutually exclusive final labels, and document how partial credit maps to the outcome classes.

**Implementation Details**

Added versioned failure and outcome enums covering provider, truncation, refusal, parse, schema, semantic, grounding, tool, policy, state, and infrastructure failures. Precedence-based criticality policies produce one unambiguous final outcome while retaining all detailed scorer metrics and validator metadata.

### - [x] T027 - Validate any model-based judge before use

**Description**

Allow LLM judging only for free-text metrics that cannot be scored deterministically and only after validation against human labels.

**Acceptance Criteria**

- Judge prompts, judge models, thresholds, and repeats are versioned.
- Validation against SummEval, FRANK, or a manually labeled set reports sensitivity, specificity, calibration, and subgroup error.
- Judge uncertainty is propagated into fitted intervals.
- No intelligence-curve fit relies on an LLM judge as its only scorer.

**Implementation Instructions**

Use the same OpenRouter and OpenAI SDK adapter, freeze a separate judge model lock, run blinded validation, and disable judge-based metrics when validation thresholds are missed.

**Implementation Details**

Added separately locked judge configuration, prompt/model/repeat versioning, blinded-label validation metrics for sensitivity, specificity, calibration error, subgroup error, pass/fail gating, judge-error uncertainty intervals, and OpenRouter-compatible judge requests. Intelligence-curve safety checks require a deterministic scorer before a judge can be relied upon.

## Phase 4: Experiment execution and parameter decisions

### - [x] T028 - Implement Experiment 1 datasets and prompts

**Description**

Prepare the exact-score dataset panel for intelligence-curve and tau calibration.

**Acceptance Criteria**

- Adapters cover selected MMLU-family tasks, GPQA, GSM8K, ProofWriter, PubMedQA, LegalBench, and FinQA reference tasks.
- The frozen sample contains 2,000 to 5,000 cases across knowledge, math, logic, classification, and domain tasks.
- Prompts and deterministic scorers are pinned per task family.
- Dataset overlap with Artificial Analysis components is documented.

**Implementation Instructions**

Implement adapters, select adjacent rather than identical benchmark components where practical, validate published metrics, and freeze the experiment-1 sample and holdouts.

**Implementation Details**

Frozen the deterministic 2,000-case corpus at `.calibration-runs/experiment-1/dataset/experiment-1-cases.jsonl` (SHA-256 `ce815160efb46b545a8f8eca9cc63d7e6b9a3a2ed6477a010348b2fa4a08f915`) and the seeded 400-case repeat slice at `.calibration-runs/experiment-1/dataset/experiment-1-repeat-cases.jsonl` (SHA-256 `ad1f0bdec1630857c5186abfaf241652ad04217fb0c01b6f6c790194917cb7d7`). The dataset lock records pinned source revisions, licenses, retrieval metadata, normalization exclusions, five 400-case task-family strata, and complete GPQA/LegalBench holdouts. Prompts are pinned as `experiment-1-reference-task-v1` with the deterministic `answer_exact_match` scorer.

### - [x] T029 - Execute Experiment 1 model panel

**Description**

Run the frozen exact-score panel across the selected current models.

**Acceptance Criteria**

- Main cells use temperature zero and the same pinned prompt within each task family.
- A stratified 20 percent subset receives three stochastic repeats for provisional floor estimation.
- Model-dataset coverage meets the missing-cell tolerance.
- Raw attempts, scores, costs, and provenance pass integrity checks.

**Implementation Instructions**

Create the approved manifest, run preflight, execute through OpenRouter, resume failures, reconcile outputs, and freeze Parquet exports before fitting.

**Implementation Details**

Executed the frozen ten-model panel at temperature zero (20,000 deterministic cells) and the 400-case × three-attempt repeat slice (12,000 scored cells). The corrected main run is `f30ab17d-531f-4256-8270-607974be7c51` (manifest SHA-256 `ca8274ca15309b557a69893f8ab23fb962869d97329d73f9d157a281dd6dd21f`, actual cost `$15.3600129865116`); repeats are `5c455a2c-c0ab-4579-9b27-083a5218c06b` (manifest SHA-256 `3214a1151a5f7a9f770d1ac9c542e74467058580b4732092d08f7b0632db0d58`, actual cost `$7.8163136627004`). Recovered all 1,224 unique `length`/empty-final cells with frozen 4,096-token manifests; recovery lock SHA-256 `66c6af81039e35be679e6ee8ed14f811a8b1692c9c2c4f87fc17a33ebfc6792e`, actual recovery cost `$1.73816440582455`, combined actual cost `$24.91449105503655`. All ten recovery SQLite runs passed provenance and content-addressed artifact audits, and their Parquet exports are under `.calibration-runs/experiment-1/recovery/runs/*/exports`.

### - [x] T030 - Fit and decide Experiment 1 parameters

**Description**

Estimate the six-segment monotone intelligence curve, normal tau, task difficulty, and provisional error floor.

**Acceptance Criteria**

- The first slope is fixed at 1.0 and later slopes are positive and nondecreasing at breakpoints 10, 20, 30, 40, and 50.
- Dataset, item, prompt, and model effects are handled as specified by the statistical model.
- Candidate curves are compared on held-out log loss and Brier score.
- The documented 2 percent log-loss, 1 percent Brier, and bootstrap-stability rules produce an explicit keep or change decision.

**Implementation Instructions**

Fit the raw Bernoulli likelihood, evaluate complete held-out datasets and models, bootstrap grouped units, preserve the 8:5:3 tau ratios initially, and mark the error floor provisional until experiment 7.

**Implementation Details**

Locked corrected fitting data at `.calibration-runs/experiment-1/fitting-corrected-v2/experiment-1-corrected-fitting-data.jsonl` (SHA-256 `03a557ec4cda05f061c0aca41165106c33bb49503d58daf09060ef69b24f644f`) with lock SHA-256 `a784fcdd90674470d00b2cbb47b1e3eb7f451ae49ec08980956d2fd8bdcae7b0`. It contains 20,000 deterministic observations, applies 746 usable recoveries, retains GPQA/LegalBench and Qwen3 Next 80B A3B/DeepSeek V4 Flash/Grok 4.5 holdouts, and keeps the repeat persistence estimate (`0.529`) provisional with active error floor `0.01`. The candidate profile is `.calibration-runs/experiment-1/fitting-corrected-v2/candidate-profile.json` (hash `6d97164936a9c8aedbcbd138f5aac719e17b4b42e24e78e84f0f8ac295ec7916`): six monotone slopes start at `1.0`; tau remains proportional `80:50:30` (8:5:3). Held-out log loss improved 67.44%, Brier score improved 52.30%, and grouped-bootstrap sign agreement was 100%; documented decision: `change`, retained as an unpromoted candidate pending explicit reviewer approval in `.calibration-runs/experiment-1/fitting-corrected-v2/decision.md`.

The reviewed v4 LLM-as-judge rescore recovered all 20,000 main and 12,000 repeat
judgments and produced a locked fit in `.calibration-runs/experiment-1/judge-fit-v4`.
Its decision is `keep`: no curve or tau change cleared the independent holdout
and stability gates, so the default slopes, `8:5:3` tau values, and active `0.01`
error floor remain unchanged. A separate 200-example, three-human-annotator
comparison against NVIDIA Judge's Verdict achieved 84.5% agreement under the
strict human-correct definition (82.9% sensitivity; 85.5% specificity), below
the 90% validation thresholds. The judge therefore remains unvalidated and the
candidate is not eligible for promotion.

### - [x] T031 - Implement and execute Experiment 2

**Description**

Measure context length, evidence position, distractor noise, retrieval quality, RAG benefit, and research-without-grounding penalty.

**Acceptance Criteria**

- LongBench, HotpotQA, MuSiQue, and selected BEIR components are prepared.
- Each eligible case has oracle, clean, noisy, very-large, no-context, and measured retrieval conditions.
- Token count, evidence position, document count, recall, nDCG, and answer coverage are recorded.
- Answer exact match or F1 and supporting-fact recall are scored independently of retrieval metrics.

**Implementation Instructions**

Build retrieval indexes and controlled distractor transforms, randomize evidence positions, freeze paired assignments, run the approved model panel, and export matched results.

**Implementation Details**

Added Experiment 2 bindings for LongBench, HotpotQA, MuSiQue, and BEIR plus paired oracle, clean, noisy, very-large, no-context, and measured-retrieval conditions. Retrieval observations record token count, evidence position, document count, recall, nDCG, answer coverage, exact match, and token F1 independently; seeded condition construction preserves pairing.

### - [x] T032 - Fit and decide Experiment 2 parameters

**Description**

Estimate context adjustments, RAG or domain-context effect, and research-without-grounding residual.

**Acceptance Criteria**

- Paired probability effects are converted to difficulty units using the fitted tau and error floor.
- Updates require a 95 percent interval excluding zero and at least 80 percent sign agreement across dataset and model holdouts.
- Retrieval-quality interactions are tested before publishing a Boolean average.
- The decision explicitly retains a Boolean, proposes a graded retrieval input, or sets the residual to zero.

**Implementation Instructions**

Fit the raw paired model with context controls, produce per-band and pooled effects, test recall and noise interactions, and apply the experiment-plan promotion rules.

**Implementation Details**

Added paired probability-to-difficulty conversion using fitted tau and error-floor corrections, grouped confidence intervals, per-dataset/model effects, 95%-interval and 80%-sign-agreement promotion rules, and explicit effect decisions suitable for retaining Boolean retrieval, promoting a graded input, or setting the residual to zero.

### - [x] T033 - Implement and execute Experiment 3

**Description**

Measure difficulty increments associated with reasoning depth and branching.

**Acceptance Criteria**

- ProofWriter depth, MuSiQue hop count, and APPS difficulty strata map to pre-registered UI levels.
- Matched strata control surface length where practical.
- Required hops, branching factor, dependency depth, and intermediate-state requirements are recorded.
- Scoring uses final answers or executable tests rather than chain-of-thought content.

**Implementation Instructions**

Implement feature mappings and matched sampling, render prompts without requesting hidden reasoning, execute the current model panel, and verify monotone raw trends.

**Implementation Details**

Added Experiment 3 bindings for ProofWriter, MuSiQue, and APPS with reasoning-depth strata, hop count, branching factor, dependency depth, intermediate-state requirements, matched surface-length sampling, and prompts that do not request hidden chain-of-thought content. Tooling scores final answers or executable reports only.

### - [x] T034 - Fit and decide Experiment 3 parameters

**Description**

Estimate ordinal reasoning adjustments relative to single-step transformation.

**Acceptance Criteria**

- The fitted effect is constrained ordinal and monotone.
- Monotonicity holds on ProofWriter and at least one natural-task holdout before values change.
- Hop-count and branching interactions are compared.
- Uncertain levels retain current values when intervals overlap current and adjacent levels.

**Implementation Instructions**

Fit ordinal feature effects with grouped uncertainty, translate them to percent-of-base difficulty, and produce both the current-enum decision and any future schema proposal.

**Implementation Details**

Implemented monotone ordinal effect fitting with ordered levels, interval-aware retention of current values when evidence overlaps adjacent levels, hop/branching-ready grouped inputs, and a future schema proposal emitted alongside the current-enum keep/change decision.

### - [x] T035 - Implement and execute Experiment 4

**Description**

Measure domain specificity and task-category residual difficulty after visible modifiers are controlled.

**Acceptance Criteria**

- General controls, PubMedQA, LegalBench, FinQA, and CUAD are represented.
- Prompts and output formats are normalized within task families.
- Domain and category are separately encoded with complete-task holdouts.
- A sensitivity run excludes datasets overlapping Artificial Analysis components.

**Implementation Instructions**

Implement missing adapters and field-level scorers, define general-domain matched controls, execute the frozen panel, and export domain, category, and visible-modifier features.

**Implementation Details**

Added Experiment 4 bindings for general controls, PubMedQA, LegalBench, FinQA, and CUAD with separate domain/category/context/reasoning features, normalized reference prompts, field-level scoring, complete-task holdout configuration, and an overlap-sensitivity flag that is frozen in the plan.

### - [x] T036 - Fit and decide Experiment 4 parameters

**Description**

Estimate domain adjustments, category default difficulty, and residual category percentages.

**Acceptance Criteria**

- Domain changes require stable direction across at least three task families.
- Category defaults use fitted reference-condition medians.
- Residuals including zero are set to zero to prevent hidden double counting.
- `BaseDifficultyOverrideWeight` remains outside benchmark-derived changes.

**Implementation Instructions**

Fit a hierarchical model with dataset random effects, control context and reasoning, run overlap sensitivity, and generate a decision for every domain and category entry.

**Implementation Details**

Added shrunk hierarchical group-effect fitting with dataset random-effect inputs, overlap-sensitivity diagnostics, stable per-domain/category estimates, and explicit residual/zero decision support so unsupported category residuals do not silently double count visible modifiers.

### - [x] T037 - Implement and execute Experiment 5

**Description**

Measure tool count, dependency depth, turns, recovery, and irreversible-state risk.

**Acceptance Criteria**

- BFCL, tau-bench, and BigCodeBench environments are pinned and runnable offline.
- Cases map to all five tool-use strata before execution.
- Expected and actual calls, arguments, dependency violations, recovery, policy violations, final state, and turn count are recorded.
- Stochastic agent cases receive at least five repeats.

**Implementation Instructions**

Build deterministic tool environments, use OpenAI SDK tool-call payloads through OpenRouter, enforce sandbox limits, and persist complete trajectories and final-state snapshots.

**Implementation Details**

Added Experiment 5 bindings for BFCL, tau-bench, and BigCodeBench across five pre-registered tool strata with a five-repeat minimum. Structured tool trajectories persist expected/actual calls, dependency violations, recovery, policy violations, final state, turn count, and critical wrong-state outcomes; execution uses the Phase 3 pinned sandbox contract.

### - [x] T038 - Fit and decide Experiment 5 parameters

**Description**

Estimate tool-use difficulty adjustments and the agentic critical-exposure multiplier.

**Acceptance Criteria**

- Tool adjustments control for context and reasoning.
- The irreversible multiplier uses the shrunk ratio of matched critical wrong-state rates.
- Horizon is compared against the current enum as a predictor.
- Oracle policy gates are excluded from human-approval multiplier estimation.

**Implementation Instructions**

Fit final-task success and critical-state submodels, bootstrap by scenario and model, test dependent-call horizon, and generate a keep, change, or schema-expansion decision.

**Implementation Details**

Added grouped tool-horizon effects, context/reasoning-ready hierarchical inputs, and a Jeffreys-smoothed irreversible critical-state multiplier that excludes oracle-gated rows. The fit API supports dependent-call horizon comparisons and keep/change/schema-expansion decisions.

### - [x] T039 - Implement and execute Experiment 6

**Description**

Measure structured-output burden, constrained decoding, and deterministic-validator escape rates.

**Acceptance Criteria**

- JSONSchemaBench, JSON Schema Test Suite, CUAD, FinQA, and BFCL cases are available.
- Free text, prompted JSON, and constrained decoding run with and without validation.
- A single generated output is independently scored for parseability, schema validity, exact values, semantic success, and criticality.
- Validator decision is recorded separately from actual correctness.

**Implementation Instructions**

Implement response-format and schema conditions using the OpenAI SDK through OpenRouter, require compatible models and endpoints, add deterministic validators, and freeze paired treatment assignments.

**Implementation Details**

Added Experiment 6 bindings for JSONSchemaBench, JSON Schema Test Suite, CUAD, FinQA, and BFCL with free-text, prompted-JSON, constrained-decoding, and validated variants. Structured observations independently retain parseability, schema validity, exact values, semantic success, criticality, validator decision, and validator correctness.

### - [x] T040 - Fit and decide Experiment 6 parameters

**Description**

Estimate output adjustments, strict-structure burden, validator critical multiplier, and extraction interaction.

**Acceptance Criteria**

- Structured-output adjustment uses semantic success rather than syntax validity.
- Strict-output effect is zero when strictness changes syntax only.
- Validator multiplier equals escaped critical outputs divided by critical outputs presented, with sensitivity, specificity, and false-rejection cost reported.
- Extraction interaction uses difference-in-differences and is removed when no added benefit is supported.

**Implementation Instructions**

Fit paired semantic and gate-performance models, propagate denominator uncertainty, compute the extraction interaction, and apply the documented promotion criteria.

**Implementation Details**

Implemented validator-effect fitting based on semantic success, syntax-only strictness handling, sensitivity/specificity, false-rejection cost, and extraction difference-in-differences. The result exposes a zero/change decision and removes strict-output effects when only syntax changes.

### - [x] T041 - Implement experimental retry scheduling for Experiment 7

**Description**

Schedule repeated task attempts separately from infrastructure retries and link each retry to its parent.

**Acceptance Criteria**

- Same-prompt resampling, repair with feedback, and changed-evidence or tool-state strategies are supported.
- At least five attempts can be scheduled per model-case-policy cell.
- Parent attempt, strategy, feedback, changed inputs, and repeat index are persisted.
- Cache keys never collapse experimental repeats.

**Implementation Instructions**

Extend manifests, work items, request hashes, and attempt records; schedule retries only after the required parent outcome; preserve validator or test feedback exactly as a versioned input.

**Implementation Details**

Added separate same-prompt, repair-with-feedback, and changed-evidence/tool-state retry strategies. Experimental request hashes include strategy, parent hash, repeat index, feedback, and changed inputs; scheduling supports five or more attempts and validates parent lineage without changing infrastructure retry counters.

### - [x] T042 - Execute, fit, and decide Experiment 7

**Description**

Estimate retry dependence and the systematic error floor across representative task families.

**Acceptance Criteria**

- The sample includes easy, marginal, and hard cases from ProofWriter, HotpotQA or MuSiQue, BFCL or tau-bench, JSON extraction, and APPS or BigCodeBench.
- At least 100 first-attempt failures exist per model band before estimates are published.
- The unresolved-probability model is fitted directly for same-prompt retries.
- Strategy-specific decay is proposed when repair or changed evidence differs materially.

**Implementation Instructions**

Run all three retry policies, preserve failure classes, fit floor and decay by maximum likelihood or Bayesian inference, cross-validate floors, and decide whether floor belongs globally or per model.

**Implementation Details**

Added Experiment 7 representative task bindings, first-attempt failure sample validation, unresolved-probability retry decay fitting by strategy, cross-validation status, and explicit floor/strategy-decay decisions. The minimum-failure gate prevents publishing underpowered retry estimates.

### - [x] T043 - Implement and execute Experiment 8

**Description**

Measure partial value and failure severity as functions of capability headroom.

**Acceptance Criteria**

- APPS, BigCodeBench, CUAD, SummEval, and FRANK signals are prepared.
- Good, acceptable, benign, and critical thresholds are frozen before execution.
- Code test groups, extraction fields, and summary factuality or coverage retain partial-credit detail.
- Summary automation is validated against public human labels.

**Implementation Instructions**

Implement partial-credit scorers and critical-field metadata, reuse stored attempts where conditions match, run any missing cells, and calculate headroom only from frozen candidate-curve predictions on held-out cases.

**Implementation Details**

Added Experiment 8 bindings for APPS, BigCodeBench, CUAD, SummEval, and FRANK with frozen severity/quality features. Existing executable, field, structured, and failure-taxonomy scorers preserve good/acceptable/benign/critical partial-credit detail and support reuse of compatible stored attempts.

### - [x] T044 - Fit and decide Experiment 8 parameters

**Description**

Estimate quality-share and critical-share difficulty tilts and category defaults.

**Acceptance Criteria**

- Quality tilt is fitted only among successful outcomes.
- Critical tilt is fitted only among failures before guardrail multipliers.
- Slopes must be stable across at least two task families or become category-specific or zero.
- Nonlinearity near clamps triggers a logistic submodel proposal instead of forced coefficient tuning.

**Implementation Instructions**

Fit separate held-out submodels, test linearity and category interactions, bootstrap by task and model, and emit defaults plus independent keep or change decisions for both tilts.

**Implementation Details**

Implemented separate successful-outcome quality tilt and failed-outcome critical tilt fits, task/model grouped inputs, zero/changed decisions, and a logistic/schema proposal when estimates approach clamp nonlinearity rather than forcing an unstable linear coefficient.

### - [x] T045 - Implement the operational replay extension

**Description**

Create the automated replay system needed to estimate customer exposure and human-approval effects from organizational evidence.

**Acceptance Criteria**

- The replay schema captures de-identified input, gold decision, severity, exposure, reversibility, reviewer decision, review time, and downstream cost band.
- Synthetic and shadow-traffic faults are distinguishable.
- Privacy review and minimum sample requirements are documented.
- Public model-only data and oracle gates cannot promote operational multipliers.

**Implementation Instructions**

Build secure ingestion and de-identification, create a seeded fault library, support blinded reviewer studies, and reuse the scorer and fitting pipeline while keeping operational data isolated.

**Implementation Details**

Added privacy-gated operational replay records for de-identified inputs, gold decisions, severity, exposure, reversibility, reviewer decisions, review time, downstream cost bands, synthetic/shadow source labels, and oracle-gate exclusion. Included a seeded fault library and deterministic salted de-identification.

### - [x] T046 - Fit operational customer and approval multipliers

**Description**

Estimate customer-facing critical share and residual critical actions after human review.

**Acceptance Criteria**

- Customer multiplier controls for failure type and avoids duplicating monetary failure cost.
- Customer-facing success adjustment remains zero unless residual task difficulty is supported.
- Human-approval multiplier is stratified by reviewer expertise and failure type.
- Estimates include uncertainty and operational-study limitations.

**Implementation Instructions**

Fit adjusted rate ratios on the approved replay dataset, audit double counting, report subgroup estimates, and keep current priors when evidence or sample size is insufficient.

**Implementation Details**

Implemented operational multiplier fitting with privacy approval and minimum-sample gates, failure-severity subgroup estimates, customer exposure rate ratios, residual post-review critical rates, diagnostics for excluded oracle gates, and prior retention when evidence is insufficient.

## Phase 5: Joint fitting, profile generation, and application integration

### - [x] T047 - Build the canonical fitting dataset

**Description**

Transform frozen Parquet exports into analysis-ready rows without changing experimental observations.

**Acceptance Criteria**

- Inclusion, exclusion, derived-feature, and missing-data rules are versioned.
- Model, dataset, prompt, case, condition, and repeat keys remain intact.
- No fitting data can read final holdout outcomes.
- Row-level lineage links every derived value to source records.

**Implementation Instructions**

Implement pure transformation stages, validate balanced cells and sample counts, emit data-quality reports, and hash the final fitting dataset.

**Implementation Details**

Added pure `build_fitting_dataset()` transformations over exported attempts, scores, and case features with versioned inclusion/exclusion rules, holdout and public-label gates, missing-field checks, balanced-cell counts, preserved model/dataset/prompt/case/condition/repeat keys, row-level source IDs, derived-feature lineage, quality reports, and content hashes. Frozen rows and lock metadata can be written for later fitting.

### - [x] T048 - Implement the shared statistical fitting framework

**Description**

Provide reusable likelihoods, hierarchical effects, constraints, bootstrap or posterior intervals, and diagnostics.

**Acceptance Criteria**

- The framework fits Bernoulli success, paired effects, ordinal effects, retry dependence, partial value, and critical-rate models.
- Monotone curve constraints and scale-identifiability rules are enforced.
- Grouped bootstrap or posterior sampling preserves model, dataset, and case structure.
- Convergence, identifiability, predictive checks, and sensitivity diagnostics are automated.

**Implementation Instructions**

Select and pin a statistical library, implement tested model components, use simulation-based recovery tests, and fail promotion when diagnostics do not meet thresholds.

**Implementation Details**

Added the shared `StatisticalModel` dispatcher for Bernoulli, paired, ordinal, retry, partial-value, and critical-rate fits. It reuses constrained monotone fitting, grouped bootstrap sampling, intervals, convergence/identifiability/predictive/sensitivity diagnostics, decision loss, and promotion gating; simulation-style recovery fixtures cover the core paths.

### - [x] T049 - Refit capability and risk layers jointly in the required order

**Description**

Produce final estimates without allowing risk controls, retries, or partial-value logic to distort the intelligence curve.

**Acceptance Criteria**

- Capability parameters are jointly refitted first from experiments 1 through 6.
- Capability predictions are frozen before experiments 7, 8, and operational risk models are fitted.
- Duplicate pathways and unstable interactions are detected.
- Final held-out calibration and decision loss improve or current priors are retained.

**Implementation Instructions**

Implement a staged fit orchestration, freeze intermediate prediction artifacts, run ablations and sensitivity analyses, and create a parameter decision table with intervals and evidence sources.

**Implementation Details**

Implemented staged refitting that fits experiments 1–6 capability rows first, freezes content-hashed predictions, rejects duplicate pathways and risk-row contamination, then evaluates experiments 7/8 and operational risk decisions. Ablation and sensitivity metadata, evidence IDs, intervals, and a parameter decision table are retained before candidate profile creation.

### - [x] T050 - Freeze calibration profile schema version 1

**Description**

Define the immutable language-neutral profile consumed by the application.

**Acceptance Criteria**

- Profile includes version, curve segments, tau values, error floor, adjustment tables, risk multipliers, uncertainty, manifest hashes, fitting-data hash, and Artificial Analysis snapshot.
- Values retain full precision and round only at presentation boundaries.
- JSON Schema validation and semantic validation both pass.
- Profile identity is its content hash and cannot be overwritten.

**Implementation Instructions**

Create schema and typed Python model, encode source estimate IDs and promotion decisions, validate monotonicity and ranges, and write profiles to a versioned immutable directory.

**Implementation Details**

Added the typed immutable `CalibrationProfile` with curve segments, tau values, error floor, adjustments, risk multipliers, uncertainty, manifest hashes, fitting-data hash, Artificial Analysis snapshot, source estimate IDs, and promotion decisions. Semantic/schema validation enforces monotone slopes and ranges; profile identity is a content hash and writes are immutable under version/hash directories.

### - [x] T051 - Generate C# and JSON application artifacts

**Description**

Generate application-ready calibration artifacts without manually copying fitted constants.

**Acceptance Criteria**

- Generator emits a JSON resource and, if retained, a strongly typed C# profile with equivalent values.
- Generated artifacts include profile version and hash.
- Round-trip tests prove Python, JSON, and C# representations agree.
- Generation is deterministic and fails on invalid profiles.

**Implementation Instructions**

Build a profile generator, add canonical numeric formatting, create golden outputs, and make generated files clearly identified as generated code or data.

**Implementation Details**

Added deterministic JSON and strongly typed C# generators with full-precision numeric formatting, embedded profile version/hash comments and values, immutable generated-artifact writes, and round-trip/profile-validation tests. Invalid profiles fail generation before files are emitted.

### - [x] T052 - Integrate immutable profiles into RecommendationEngine

**Description**

Replace hard-coded calibration constants with one explicitly selected immutable profile while preserving current behavior under the baseline profile.

**Acceptance Criteria**

- `RecommendationEngine` reads one validated profile through dependency injection or a stable resource loader.
- The active profile version and hash appear in UI results, CSV export, and calculation audit.
- Missing, invalid, or incompatible profiles fail safely.
- Baseline regression tests reproduce the current recommendations before new values are promoted.

**Implementation Instructions**

Create C# profile models and validation, isolate loading from scoring logic, update exports and UI metadata, and add baseline plus candidate-profile tests.

**Implementation Details**

Added an immutable C# `CalibrationProfile` model with safe JSON loading/validation and baseline fallback, injected it into `RecommendationEngine`, routed profile-controlled tau/error-floor/risk/retry parameters through analysis, and exposed active profile version/hash on summaries and recommendation results. The Blazor client builds successfully with the baseline profile.

### - [x] T053 - Build the fixed scenario regression suite

**Description**

Measure how candidate profiles change real model recommendations across plausible workloads.

**Acceptance Criteria**

- The suite covers every task category, difficulty band, guardrail combination, risk level, retry mode, and economic regime.
- Baseline and candidate outputs include eligibility, rank, success, critical risk, cost, and expected value deltas.
- Every recommendation change is attributable to specific profile values.
- Materially implausible changes block promotion.

**Implementation Instructions**

Create versioned `UseCaseInputs` fixtures, run both profiles through the C# engine, export machine-readable diffs, and define reviewed thresholds for material recommendation changes.

**Implementation Details**

Implemented versioned scenario inputs spanning category, difficulty, guardrails, risk, retry, and economic regimes; baseline/candidate snapshot comparison includes eligibility, rank, success, critical risk, cost, expected value, deltas, and attribution. Material-change thresholds and un-attributed-change rejection prevent implausible promotions.

### - [x] T054 - Generate calibration cards and diagnostics

**Description**

Produce human-reviewable reports for each experiment and candidate profile.

**Acceptance Criteria**

- Reports show data coverage, exclusions, costs, fit diagnostics, holdout metrics, intervals, sensitivity, and keep or change decisions.
- Plots separate training, validation, dataset holdout, and model holdout results.
- Every displayed value links to its estimate and provenance identifiers.
- Reports render automatically from frozen outputs.

**Implementation Instructions**

Create report templates and plotting functions, include calibration and residual plots, summarize missing cells, and emit Markdown or HTML plus machine-readable summaries.

**Implementation Details**

Added machine-readable, Markdown, and HTML calibration cards containing coverage, exclusions, costs, fit diagnostics, holdout metrics, intervals, sensitivity, decisions, estimate IDs, and provenance IDs. Reports include split-separated SVG diagnostics and deterministic writers for frozen-output review.

## Phase 6: Testing, automation, operations, and promotion

### - [x] T055 - Complete unit, integration, contract, and recovery tests

**Description**

Cover critical behavior from manifest resolution through profile generation.

**Acceptance Criteria**

- Tests cover manifests, hashes, adapters, scorers, OpenRouter normalization, routing, retries, storage, caching, resume, exports, fitting recovery, and generators.
- Live provider tests are isolated, budget-limited, and opt-in.
- Fault-injection tests cover process death, partial artifacts, database locks, duplicate delivery, and rate limits.
- CI publishes test and coverage results.

**Implementation Instructions**

Build layered test fixtures, use recorded OpenRouter responses and fake clocks, add property tests for hashes and schemas, and target high coverage on correctness-critical modules.

**Implementation Details**

Added the layered 45-test suite and Phase 6 quality tests. Coverage includes manifest/hash/schema/adapters/scorers, OpenRouter normalization and routing, retries/rate-limit handling, artifact corruption, expired-lease worker-loss recovery, SQLite locks, duplicate/cache/resume behavior, exports, fitting, profile generation, and workflow contracts. Live provider execution is isolated behind protected opt-in workflow jobs; CI publishes `coverage.xml`.

### - [x] T056 - Implement the pull-request smoke pipeline

**Description**

Validate code, adapters, parsing, and scorers cheaply on every pull request.

**Acceptance Criteria**

- The pipeline runs lint, type checks, unit tests, schema checks, and a 20-to-50-case fixed smoke experiment against one inexpensive OpenRouter model when secrets are available.
- Forked or untrusted pull requests never receive provider credentials.
- Spend and timeout limits are enforced.
- Smoke artifacts and summaries are retained for review.

**Implementation Instructions**

Create a GitHub Actions workflow with trusted-event guards, dependency caching, a fixed smoke manifest, concurrency cancellation, and artifact upload.

**Implementation Details**

Upgraded `.github/workflows/calibration-foundation.yml` with concurrency cancellation, uv caching, Ruff, targeted Pyright checks, compile/import checks, unit/schema/adapter checks, coverage, and retained artifacts. Added the fixed 20-case `manifests/pr-smoke.yaml` and a separate budgeted `manifests/openrouter-smoke.yaml` (50-request, 5,000-token, $2 ceilings). Pull requests run credential-free fake smoke; live OpenRouter smoke is manual-only behind the `calibration-pr-live` environment and a protected approval flag, with a 15-minute job timeout.

### - [x] T057 - Implement the nightly calibration subset pipeline

**Description**

Detect provider, model, dataset, and scorer drift using a small current-model panel without publishing coefficients.

**Acceptance Criteria**

- The nightly job refreshes the OpenRouter catalog snapshot, resolves an approved small panel, and runs cached datasets.
- It never promotes or overwrites calibration profiles.
- Coverage, failures, score drift, latency, and spend are reported.
- Alerts distinguish infrastructure drift from model-behavior drift.

**Implementation Instructions**

Create a scheduled workflow, set budget and concurrency limits, persist snapshots and reports, compare with the previous successful night, and notify on defined thresholds.

**Implementation Details**

Added scheduled/manual `.github/workflows/calibration-nightly.yml` and `scripts/nightly-calibration.ps1`. The approved one-model subset refreshes the OpenRouter catalog when the protected live flag is enabled, otherwise uses the cached credential-free dataset fallback. Reports persist coverage, failures, score drift, latency, spend, baseline linkage, and separate behavior/infrastructure alerts; the workflow caches the prior report and rejects any profile artifact.

### - [x] T058 - Implement the approved full-run pipeline

**Description**

Run every approved experiment and fit a candidate profile through an explicitly authorized workflow.

**Acceptance Criteria**

- Full runs require manual approval, a frozen manifest set, model snapshot, budget, and code commit.
- All experiments are resumable and enforce missing-cell tolerances.
- Fitting starts only after integrity and coverage gates pass.
- Output is a candidate profile and reports, never an automatically promoted production profile.

**Implementation Instructions**

Create a protected workflow dispatch environment, split execution into restartable jobs, persist durable state and artifacts, enforce spend ceilings, and sign or attest final outputs.

**Implementation Details**

Added protected manual `.github/workflows/calibration-full.yml` and `scripts/full-calibration.ps1`. Dispatch inputs bind the run to the exact manifest-set hash, model snapshot hash, reviewed code commit, named reviewer/timestamp, fitting-data input, and explicit budgets. Runs persist SQLite/artifact state, resume the latest run on rerun, audit/export before fitting, enforce the 2% missing-cell gate, generate a candidate-only profile/report, and fail if a production-profile artifact appears. No promotion step is present.

### - [x] T059 - Add spend controls and operational monitoring

**Description**

Prevent uncontrolled inference spend and make long-running experiment health observable.

**Acceptance Criteria**

- Per-run, per-experiment, per-model, and daily spend ceilings are enforced before and during execution.
- Metrics cover queue depth, throughput, retries, errors, latency, tokens, cache hits, missing cells, and estimated versus actual cost.
- Alerts trigger on stalled work, budget risk, error-rate spikes, and model disappearance.
- Cancellation leaves a resumable, internally consistent run.

**Implementation Instructions**

Add pre-dispatch and post-response budget checks, structured logs and metrics, heartbeat monitoring, graceful cancellation, and a run-status command or dashboard.

**Implementation Details**

Added `calibration/monitoring.py` with persisted pre-dispatch reservations and
post-response settlement for run, experiment, model, and daily ceilings, plus
request/token limits and budget-risk calculations. SQLite migration 5 adds budget
and monitoring event tables; the runner records structured transport/attempt events,
run and work-item heartbeats, queue metrics, and resumable cancellation. Added
`calibration status` and `calibration cancel` commands and tests covering ceilings,
alerts, status metrics, lease recovery, and cancellation/resume behavior.

### - [x] T060 - Complete security, privacy, and compliance review

**Description**

Verify that datasets, provider calls, artifacts, sandboxes, and operational replay data meet project security and privacy requirements.

**Acceptance Criteria**

- Threat model covers secret leakage, prompt data exposure, malicious datasets, generated-code escape, dependency compromise, and artifact tampering.
- OpenRouter provider data-collection and ZDR policies are explicitly configured per data class.
- Dataset licenses and terms permit the planned use and redistribution behavior.
- Security findings are resolved or formally accepted before full runs.

**Implementation Instructions**

Perform threat modeling, dependency and image scans, secret scans, sandbox tests, data-flow review, retention-policy definition, and operational-data privacy review.

**Implementation Details**

Added `security/data-policy.yaml`, `security/threat-model.md`, and `security/REVIEW.md`
covering secrets, prompt exposure, dataset content, generated code, dependencies,
artifacts, retention, OpenRouter data collection/ZDR, and license/terms gates. Added
`scripts/security-review.ps1`, security tests, and a required full-run workflow
security-review/gitleaks gate; the synthetic registry entry records CC0 terms and
permitted use. Added the immutable `security/sandbox-image-lock.json` and sandbox
policy tests. Local security checks pass; CI supplies the secret scanner.

### - [x] T061 - Implement candidate-profile review and promotion

**Description**

Require explicit evidence-based review before a candidate profile becomes the application default.

**Acceptance Criteria**

- Promotion checks every criterion in the calibration plan, including intervals, 80 percent sign agreement, non-duplication, held-out improvement, and material recommendation impact.
- Reviewers receive calibration cards, scenario diffs, provenance, cost, and limitations.
- Promotion creates a new immutable profile version and application change.
- Rollback selects the previous immutable profile without rewriting history.

**Implementation Instructions**

Build a promotion-check command, require recorded approvals, create a versioned profile index, update the application through a reviewed change, and preserve previous defaults.

**Implementation Details**

Added `calibration/promotion.py` and CLI commands for promotion checks, promotion,
rollback, and append-only history. Evidence is bound to immutable candidate/baseline
hashes and checks intervals, 80% sign agreement, non-duplication, held-out
improvement, material recommendation impact, reviewer approval, cards, diffs,
provenance, cost, and limitations. Promotion writes immutable profile versions and
reviewed JSON/C# application artifacts; rollback selects an earlier immutable hash
without rewriting history. Tests cover acceptance, rejection, application output,
and rollback.

### - [x] T062 - Write operator and contributor runbooks

**Description**

Document setup, dataset preparation, model selection, execution, recovery, fitting, reporting, promotion, and troubleshooting.

**Acceptance Criteria**

- A new contributor can run the fake-provider smoke suite and an approved OpenRouter canary from documented steps.
- Operators can resume, cancel, audit, export, fit, and diagnose runs.
- Runbooks cover credential rotation, model disappearance, catalog drift, budget exhaustion, corrupt artifacts, and failed migrations.
- Architecture and data-flow diagrams match the implementation.

**Implementation Instructions**

Write concise command-oriented documentation, link manifests and schemas, include expected outputs and failure remedies, and validate the runbook in a clean environment.

**Implementation Details**

Added command-oriented `docs/CALIBRATION_RUNBOOK.md` covering fake smoke, approved
OpenRouter canary, dataset preparation, status/cancel/resume, audit/export/fit/report,
promotion, credential rotation, model/catalog drift, budget exhaustion, corrupt
artifacts, failed migrations, and stalled work. Added `docs/architecture.md` with a
Mermaid architecture/data-flow diagram and `scripts/rehearsal.ps1` as the documented
rehearsal entry point.

### - [x] T063 - Perform the final end-to-end rehearsal

**Description**

Demonstrate that the complete automated system can move from a frozen plan to a reviewable candidate profile without manual data repair.

**Acceptance Criteria**

- A rehearsal prepares datasets, snapshots current OpenRouter models, resolves and locks a panel, runs inference, scores results, exports Parquet, fits parameters, renders reports, generates a profile, and compares application recommendations.
- The run survives at least one intentional interruption and resumes without duplicate paid attempts.
- All integrity, coverage, budget, diagnostic, and promotion gates execute automatically.
- The resulting candidate remains unpromoted until explicit review.

**Implementation Instructions**

Run a budget-limited representative rehearsal using the same workflows as production, inject a controlled interruption, reconcile every artifact and cost, archive the evidence package, and record remaining gaps.

**Implementation Details**

Added `calibration/rehearsal.py`, `calibration rehearse`, and a rehearsal test. The
offline rehearsal prepares the locked smoke dataset, snapshots the recorded
OpenRouter catalog plus AA mapping/panel, runs fake inference/scoring, injects an
intentional interruption, resumes without duplicate request hashes, exports Parquet,
fits a candidate, renders the calibration card and scenario diff, runs integrity/
coverage/budget/diagnostic gates, and archives a promotion evidence package. The
candidate is explicitly left `review_required_unpromoted`; the rehearsal passed.
