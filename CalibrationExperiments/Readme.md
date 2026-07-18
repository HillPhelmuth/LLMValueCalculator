# Empirical Calibration Plan

## Objective

This plan turns the calculator's priors into versioned, reproducible calibration profiles. Every proposed experiment is designed to:

1. run unattended after model credentials and dataset access are configured;
2. use public datasets and their deterministic scorers where practical;
3. preserve raw model responses, scores, costs, and provenance;
4. estimate a named constant, curve segment, adjustment-table entry, or category profile in `RecommendationEngine`; and
5. produce an explicit keep/change decision with uncertainty, rather than a subjective recommendation.

The convex intelligence transform is a retained design premise. The initial calibration profile should use the agreed six-segment curve:

```csharp
[
    new Segment(10, 1.0),
    new Segment(20, 1.4),
    new Segment(30, 1.8),
    new Segment(40, 2.2),
    new Segment(50, 2.6),
    new Segment(double.PositiveInfinity, 3.0)
]
```

Experiments may refine those slopes and later test alternative breakpoints. They should not replace the convex relationship with an identity transform unless broad held-out evidence clearly rejects convexity.

## Calibration targets

|Experiment|Public data|Primary engine targets|
|-|-|-|
|1. Intelligence curve and success slope|MMLU-family tasks, GPQA, GSM8K, ProofWriter, selected exact-score domain tasks|`IntelligenceCurve`, `TauBySensitivity`, `BaseErrorFloorRate`|
|2. Context and retrieval|LongBench, HotpotQA, MuSiQue, BEIR retrieval corpora|`ContextAdjustments`, `RagOrDomainContextPercent`, `ResearchWithoutGroundingPercent`|
|3. Reasoning depth|ProofWriter depth splits, MuSiQue hop counts, APPS difficulty strata|`ReasoningAdjustments`|
|4. Domain and category priors|PubMedQA, LegalBench, FinQA, CUAD, general-domain controls|`DomainAdjustments`, `TaskCategoryProfile.DefaultBaseDifficulty`, `BaseDifficultyPercentResidual`|
|5. Tool horizon and agent risk|BFCL, tau-bench, BigCodeBench|`ToolAdjustments`, agentic `1.35` critical-exposure multiplier|
|6. Structure and deterministic validation|JSONSchemaBench, CUAD, FinQA, BFCL|`OutputAdjustments`, `StrictStructuredOutputPercent`, `DeterministicValidationCriticalMultiplier`, Extraction `0.85` interaction|
|7. Retry dependence and systematic failures|Repeated runs of exact-score subsets from experiments 1, 3, 5, and 6|`RetryCorrelationDecay`, `BaseErrorFloorRate`|
|8. Partial value and failure severity|APPS, BigCodeBench, CUAD, SummEval, FRANK|`QualityShareDifficultyTilt`, `CriticalShareDifficultyTilt`, default good/critical shares|

`CustomerFacingPercent`, `CustomerFacingCriticalShareMultiplier`, and `HumanApprovalCriticalMultiplier` cannot be identified credibly from model-only public benchmarks. A fully automated replay experiment for them is described later, but the necessary consequence and reviewer evidence must come from an operational study.

## Test harness

### Architecture

Use a separate calibration repository or solution so benchmark dependencies and provider credentials never enter the Blazor application. A practical layout is:

```text
calibration/
  manifests/             immutable experiment YAML files
  datasets/              download scripts, adapters, and version locks
  providers/             OpenAI-compatible, Anthropic, Google, local/vLLM adapters
  prompts/               versioned prompt templates and tool definitions
  scorers/               exact match, F1, JSON Schema, unit test, state, and rubric scorers
  perturbations/         context, retrieval, output, retry, and fault transforms
  runner/                queue, rate limiting, caching, resumability, and tracing
  fitting/               hierarchical models, bootstrap, diagnostics, and profile generation
  schemas/               run, attempt, score, and calibration-profile schemas
  containers/            pinned Docker images for executable benchmarks
  reports/               generated calibration cards and holdout plots
```

Python is the most economical runner language because the public evaluation ecosystem already provides dataset and scorer integrations. The output should remain language-neutral JSON/Parquet, and a small generator should emit a C# `CalibrationProfile` or JSON resource consumed by the application.

### Immutable experiment manifest

Every execution begins from a committed manifest. For example:

```yaml
experiment_id: context-rag-v1
dataset:
  adapter: musique
  revision: <commit-or-dataset-hash>
  split: validation
  sample_seed: 1847
models:
  - catalog_id: <stable-model-id>
    provider: <provider-adapter-name>
    provider_model: <dated-provider-model-id>
    aa_snapshot: 2026-07-01
generation:
  temperature: 0
  max_output_tokens: 512
  reasoning_effort: medium
  repeats: 1
prompt_version: context-rag-v1
conditions:
  - no_context
  - oracle_context
  - retrieved_top_5
  - retrieved_top_5_plus_distractors
scorers:
  - answer_exact_match
  - answer_token_f1
  - supporting_fact_recall
  - retrieval_ndcg_at_10
```

The manifest hash becomes part of every result row and every generated calibration profile.

### Dataset adapter contract

Each adapter must expose:

```text
prepare()                  download and verify a pinned dataset revision
cases(split)               yield canonical cases with stable IDs
render(case, condition)    produce provider-neutral messages/tools/schema
score(case, response)      return deterministic metric values and failure labels
metadata(case)             return category, depth, domain, context, and criticality features
```

Dataset downloads must verify a revision or content hash and retain the source license/terms metadata. Public test labels that are unavailable should be replaced with validation splits; the fitting holdout is then created locally and frozen before any coefficient tuning.

### Model adapter contract

All providers should return the same attempt record:

```text
model\_id, dated\_model\_version, provider, request\_hash, response\_id,
raw\_request, raw\_response, parsed\_answer, finish\_reason, refusal,
input\_tokens, cached\_tokens, output\_tokens, reasoning\_tokens,
tool\_calls, latency\_ms, provider\_cost, attempt\_number, created\_utc
```

Pin dated model versions when providers expose them. Record the exact Artificial Analysis snapshot and raw index used for that model. Exclude aliases that silently move between versions from curve fitting unless the resolved version is captured.

### Runner behavior

The runner should:

* create one work item per model, case, condition, prompt version, and repeat;
* randomize work-item order within provider rate limits;
* use bounded asynchronous workers and provider-specific token buckets;
* cache by the complete request hash;
* resume without re-running completed attempts;
* distinguish transport retries from experimental task retries;
* store raw responses before parsing or scoring;
* execute untrusted code and tool environments in network-disabled, resource-limited containers;
* redact secrets from stored requests and logs; and
* fail the run when the dataset, scorer, prompt, or model version differs from the manifest lock.

Transport failures should be retried by infrastructure policy but must not count as the task's second attempt. Experimental retries are separately scheduled conditions with their own parent attempt ID.

### Storage schema

Store at least four normalized tables:

```text
runs(run\_id, experiment\_id, manifest\_hash, code\_commit, started\_utc, completed\_utc)

attempts(attempt\_id, run\_id, case\_id, condition\_id, model\_id, model\_version,
         prompt\_version, repeat\_index, parent\_attempt\_id, request\_hash,
         raw\_request\_uri, raw\_response\_uri, latency\_ms, token\_counts,
         provider\_cost, finish\_reason, created\_utc)

scores(attempt\_id, scorer\_version, success, good, acceptable, critical,
       schema\_valid, semantic\_score, grounded\_score, tool\_state\_score,
       failure\_class, metric\_json)

case\_features(case\_id, dataset\_id, dataset\_revision, split, category,
              base\_difficulty\_stratum, context\_band, reasoning\_depth,
              domain\_band, tool\_horizon, verifiability\_band,
              output\_band, criticality\_band, feature\_json)
```

Parquet is suitable for immutable analytical outputs. SQLite or PostgreSQL is suitable for queue and run state. Raw requests and responses can live in content-addressed object storage.

### Scoring policy

Prefer deterministic scorers in this order:

1. executable unit tests or final database-state comparison;
2. exact structured-value comparison;
3. normalized exact match, token F1, or labeled classification accuracy;
4. published benchmark metrics with pinned implementations;
5. model-based judging only when validated against an existing human-labeled set.

An LLM judge must never be the only scorer used to fit the intelligence curve. If a judge is necessary for summaries or free text, validate its threshold and error rate against SummEval, FRANK, or a manually scored calibration subset and propagate judge uncertainty into the coefficient interval.

### Fitting and profile generation

The shared single-attempt model should be fitted directly:

```text
P(success for model m on case i) =
    (1 - error\_floor\[m]) \* sigmoid((curve(AA\[m]) - difficulty\[i]) / tau)
```

Case difficulty is modeled from category and modifier features with a case random effect. Model, dataset, and prompt-version random effects absorb systematic offsets that should not be forced into a UI modifier.

The curve and tau have a scale-identifiability problem. Resolve it by:

* fixing the first segment slope at `1.0`;
* constraining later slopes to be nondecreasing and positive;
* retaining the agreed breakpoints at 10, 20, 30, 40, and 50 for the first fit; and
* estimating task/dataset intercepts jointly with the curve.

For a paired condition, translate the fitted probability change into engine difficulty units:

```text
z(p) = logit(p / (1 - error\_floor))
delta\_difficulty = -tau \* (z(p\_variant) - z(p\_control))
modifier\_percent = 100 \* delta\_difficulty / normalized\_base\_difficulty
```

Fit the raw probability model rather than calculating this expression from aggregate percentages; the expression is the audit conversion used to explain the resulting modifier.

Generate a profile containing estimates and uncertainty:

```json
{
  "profileVersion": "2026.1",
  "curveSegments": \[{"upper": 10, "slope": 1.0}],
  "tau": {"soft": 8.1, "normal": 5.0, "sharp": 3.1},
  "contextAdjustments": {},
  "reasoningAdjustments": {},
  "riskMultipliers": {},
  "confidenceIntervals": {},
  "experimentManifestHashes": \[],
  "aaSnapshot": "2026-07-01"
}
```

The application should load one immutable profile and include its version in the UI, CSV export, and calculation audit.

### Automated pipeline

Use three pipeline tiers:

1. Pull-request smoke run: 20 to 50 fixed cases against one inexpensive model; validate adapters, parsing, and scorers.
2. Nightly calibration subset: cached datasets, a small model panel, and no coefficient publication.
3. Approved full run: all models, conditions, and repeats; fit the profile, render diagnostics, and require review before promoting it.

Container digests, dependency locks, dataset hashes, and prompt hashes must be recorded. A profile is promoted only from a completed run with no missing model-condition cells beyond a predefined tolerance.

## Experiment 1: Intelligence curve and tau

### Question

Do the agreed convex slopes predict unseen model-task outcomes better than nearby convex curves, and what normal tau best calibrates the success probability?

### Data

Use exact-score tasks supported by the [Language Model Evaluation Harness](https://github.com/EleutherAI/lm-evaluation-harness), supplemented with [ProofWriter](https://arxiv.org/abs/2012.13048), [PubMedQA](https://arxiv.org/abs/1909.06146), [LegalBench](https://arxiv.org/abs/2308.11462), and [FinQA](https://arxiv.org/abs/2109.00122). Choose at least 12 dated models spanning the raw Artificial Analysis range, with at least two models represented in each populated ten-point band.

Avoid fitting to the exact component scores used by Artificial Analysis when possible. The goal is to predict adjacent workload behavior, not reconstruct the index from its own ingredients.

### Execution

* Sample 2,000 to 5,000 cases across knowledge, math, logical reasoning, classification, and domain tasks.
* Use a single pinned prompt template per task family.
* Run temperature 0 for the main fit and three stochastic repeats on a 20% subset for floor estimation.
* Hold out entire datasets and at least 25% of model versions. Holding out random rows alone is too easy because the model and benchmark are already known.
* Fit the six-segment monotone curve, normal tau, dataset intercepts, item difficulty, and model random effects by maximum likelihood or Bayesian inference.

### Application decision

* Keep the agreed slopes if an alternative does not improve held-out log loss by at least 2% and Brier score by at least 1%, or if adjacent-slope ordering is unstable in more than 20% of bootstrap fits.
* Otherwise update the six `IntelligenceCurveConfig.Default` slopes to the median fitted slopes, rounded to one decimal only after prediction is evaluated.
* Update `TauBySensitivity\[Normal]` to the fitted normal tau. Preserve the current soft/normal/sharp ratios (`8:5:3`) initially, scaling Soft and Sharp from the new Normal value. Fit three independent tau values only if product testing establishes distinct, observable meanings for the three UI selections.
* Estimate `BaseErrorFloorRate` provisionally from repeated easy cases, but do not publish it until experiment 7 confirms retry persistence.

## Experiment 2: Context and retrieval

### Question

How much difficulty is added by context length/noise, and when does retrieval or supplied domain context reduce rather than increase difficulty?

### Data

Use [LongBench](https://arxiv.org/abs/2308.14508) for standardized long-context tasks, [HotpotQA](https://arxiv.org/abs/1809.09600) and [MuSiQue](https://arxiv.org/abs/2108.00573) for questions with supporting evidence, and [BEIR](https://arxiv.org/abs/2104.08663) components for retrieval evaluation.

### Paired conditions

For each base question, generate conditions without changing the answer:

1. minimal oracle evidence;
2. short clean context;
3. medium mostly relevant context;
4. large clean context;
5. large context with controlled irrelevant distractors;
6. very large cross-document context;
7. no supplied context;
8. retrieved top-k context at measured recall/precision levels.

Place the same evidence at beginning, middle, and end in a randomized subset. Record token count, relevant-token position, document count, retrieval recall, nDCG, and answer coverage.

### Scoring

Use answer exact match/F1 plus supporting-fact recall. Retrieval metrics describe the treatment; they are not substitutes for answer success.

### Application decision

* Convert paired condition effects to percent-of-base difficulty and update each `ContextAdjustments` entry with the pooled median effect when its 95% interval excludes zero and sign agreement exceeds 80% across dataset/model holdouts.
* If the retrieval effect is stable after controlling for context band, set `RagOrDomainContextPercent` to that effect.
* If retrieval benefit depends strongly on recall, noise, or answer coverage, replace the Boolean RAG modifier with a graded retrieval-quality input and table. Do not average good and bad retrieval into one value.
* Set `ResearchWithoutGroundingPercent` from the additional no-context penalty in research/synthesis tasks after the general context effect is removed. Set it to zero if no research-specific residual remains.

## Experiment 3: Reasoning depth

### Question

What difficulty increments correspond to light, moderate, deep conditional, and research-grade reasoning?

### Data

Use ProofWriter's labeled proof depths, MuSiQue's 2-to-4-hop construction, and [APPS](https://arxiv.org/abs/2105.09938) difficulty strata. ProofWriter supplies the cleanest depth intervention; the other datasets test whether it generalizes beyond synthetic deduction.

### Execution

* Pre-register mappings from dataset depth metadata to the five UI levels.
* Hold surface length approximately constant within paired or matched strata.
* Record required hops, branching factor, dependency depth, and whether intermediate results must be carried forward.
* Score final answer accuracy or executable tests, not the presence of a chain-of-thought explanation.

### Application decision

* Fit depth as an ordinal monotone effect and convert each level relative to `SingleStepTransformation`.
* Update `ReasoningAdjustments` only when monotonicity holds on both a ProofWriter holdout and at least one natural-task holdout.
* If hop count and branching have materially different effects, retain the current single table using the conservative upper pooled effect and add a future schema proposal for separate depth and branching inputs.
* Leave any level unchanged when its interval overlaps both the current value and adjacent levels.

## Experiment 4: Domain specificity and category priors

### Question

After capability, context, and reasoning are controlled, how much residual difficulty belongs to specialized or regulated domains and to each task category?

### Data

Use general-domain controls plus [PubMedQA](https://arxiv.org/abs/1909.06146), [LegalBench](https://arxiv.org/abs/2308.11462), [FinQA](https://arxiv.org/abs/2109.00122), and [CUAD](https://arxiv.org/abs/2103.06268). These datasets have automated labels or span/value comparisons and represent biomedical, legal, and financial work.

### Execution

* Normalize prompts and output formats within each task family.
* Fit domain and category as separate hierarchical effects with dataset random effects.
* Hold out complete tasks within each domain, not only individual rows.
* Run a sensitivity analysis excluding any dataset that substantially overlaps an Artificial Analysis component.

### Application decision

* Update `DomainAdjustments` from the pooled domain residuals only when direction is stable across at least three task families. Different subject matter alone is not enough to infer the effect of regulation or professional review.
* Set each `TaskCategoryProfile.DefaultBaseDifficulty` to the fitted median difficulty of the category's reference condition.
* Set `BaseDifficultyPercentResidual` to the category residual remaining after all visible modifiers are applied. If the residual interval includes zero, set it to zero to prevent hidden double counting.
* Do not estimate `BaseDifficultyOverrideWeight` from benchmark accuracy. That constant expresses how much the application trusts a user's explicit scenario judgment and requires a separate product calibration using user predictions and observed workload results.

## Experiment 5: Tool horizon and agentic risk

### Question

How do function count, dependency depth, conversational turns, and irreversible state changes affect success and critical-failure exposure?

### Data

Use the public [Berkeley Function Calling Leaderboard](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard) for single, parallel, multiple, and multi-turn calls; [tau-bench](https://arxiv.org/abs/2406.12045) for final database-state evaluation in tool-agent-user interactions; and [BigCodeBench](https://arxiv.org/abs/2406.15877) for executable multi-library code tasks.

### Execution

* Map cases before execution to no tools, one/two deterministic tools, multiple validated tools, autonomous sequence, and irreversible-action strata.
* Record expected and actual calls, invalid arguments, unnecessary calls, dependency violations, recovery, policy violations, final state, and turn count.
* Run the agent benchmarks in pinned containers with deterministic tool implementations.
* Repeat each stochastic agent case at least five times because trajectory reliability is part of the treatment.

### Application decision

* Update `ToolAdjustments` from final-task success effects after context and reasoning are controlled.
* Estimate the agentic `1.35` multiplier as the critical wrong-state rate for irreversible cases divided by the matched reversible-case rate. Use a shrunk ratio and cap only through an explicit modeling policy.
* If horizon predicts failure better than the current enum, replace or supplement the enum with expected dependent tool calls.
* An oracle policy gate can establish the maximum possible benefit of approval, but it must not be used to set `HumanApprovalCriticalMultiplier`; an oracle is not a human reviewer.

## Experiment 6: Structure and deterministic validation

### Question

How much does structured output change semantic success, and what fraction of critical failures escapes deterministic validation?

### Data

Use [JSONSchemaBench](https://arxiv.org/abs/2501.10868) and the official [JSON Schema Test Suite](https://github.com/json-schema-org/JSON-Schema-Test-Suite) for syntax/constraint coverage. Pair them with CUAD, FinQA, and BFCL cases where field values or function arguments can be compared with ground truth.

### Conditions

1. free text;
2. prompted JSON without constrained decoding;
3. provider-native or library constrained decoding;
4. each of the above with and without a deterministic validator/gate.

Generate the model output once, then score it independently for parseability, schema validity, exact field values, semantic task success, and criticality. The validator's decision must be logged separately from actual correctness.

### Application decision

* Set `OutputAdjustments\[StructuredJsonOrSchema]` from the semantic-success difference, not the schema-validity difference.
* Set `StrictStructuredOutputPercent` from any additional semantic burden of strictness after ordinary structured output is controlled. Set it to zero when strictness only changes syntax compliance.
* Set `DeterministicValidationCriticalMultiplier` to the escaped-critical fraction: critical outputs allowed through divided by critical outputs presented to the validator. Report sensitivity, specificity, and false-rejection cost alongside it.
* Estimate the Extraction `0.85` interaction with a difference-in-differences comparison: strict+validator benefit minus the sum of strict-only and validator-only effects. Remove the interaction if its interval includes no additional benefit.

## Experiment 7: Retry dependence and systematic error floor

### Question

How much independent information does each retry add, and what failures persist regardless of model capability or repeated attempts?

### Data

Take a stratified exact-score subset from ProofWriter, HotpotQA/MuSiQue, BFCL/tau-bench, JSON extraction, and APPS/BigCodeBench. Include easy, marginal, and hard cases for every model.

### Execution

Run at least five attempts per model-case cell under three policies:

1. same prompt resampling;
2. a repair prompt containing the previous answer and validator/test feedback;
3. a fresh attempt with changed retrieved evidence or tool state where applicable.

Record failure class and whether the same failure recurs. Provider errors, truncation, refusal, parser failure, semantic failure, tool failure, and policy failure must remain separate.

### Fitting

Fit the engine's unresolved-probability model directly:

```text
E\_n = sum(k = 0..n-1, decay^k)
P(unresolved after n) = floor + (1 - floor) \* capability\_failure^E\_n
```

### Application decision

* Update `RetryCorrelationDecay` from the maximum-likelihood decay for same-prompt resampling, because that is the current generic retry behavior.
* If repair prompts or changed evidence have meaningfully different decay, add a `RetryStrategyOption` and a decay table rather than blending interventions.
* Set `BaseErrorFloorRate` to the cross-validated retry-resistant floor. If floors vary materially by provider/model, move the value to `ModelProfile` or the calibration profile instead of retaining one global constant.
* Require at least 100 first-attempt failures per model band before publishing retry estimates.

## Experiment 8: Partial value and failure severity

### Question

Does capability headroom predict the share of fully good outcomes among passes and the share of critical outcomes among failures?

### Data

Use [APPS](https://arxiv.org/abs/2105.09938) and [BigCodeBench](https://arxiv.org/abs/2406.15877) test-case pass fractions, [CUAD](https://arxiv.org/abs/2103.06268) field-level scores with pre-tagged critical clauses, and public summary annotations from [SummEval](https://arxiv.org/abs/2007.12626) and [FRANK](https://arxiv.org/abs/2104.13346). Public annotations can validate automated summary metrics; they should not be replaced silently with an unvalidated judge.

### Execution

* Define good, acceptable, benign failure, and critical failure thresholds before model execution.
* For code, derive partial credit from test groups, keeping security/data-loss tests separate from cosmetic or edge-case tests.
* For extraction, score each field and identify critical fields in dataset metadata.
* For summaries, validate automated factuality/coverage thresholds against the human-labeled public sets.
* Calculate model headroom with the candidate curve and fitted task difficulty, using only held-out cases.

### Application decision

* Fit `good\_share = base\_good\_share + headroom \* QualityShareDifficultyTilt` on successful outcomes. Update `QualityShareDifficultyTilt` when the slope is stable across at least two task families; otherwise use category-specific tilts or zero.
* Fit `critical\_share = base\_critical\_share - headroom \* CriticalShareDifficultyTilt` on failed outcomes before guardrail multipliers. Apply the same stability rule independently; do not force it to equal the quality tilt.
* Set category default good and critical shares from observed reference-condition rates. Preserve user-editable values as scenario overrides.
* If linearity fails near the clamps, replace the linear share model with a logistic submodel in the engine rather than tuning a coefficient that cannot fit the data.

## Operational extension: customer exposure and human approval

Public benchmark outcomes do not contain the organization's incident cost, customer visibility, reviewer expertise, or automation bias. These modifiers require operational evidence, although the replay and analysis can still be automated.

### Automated replay harness

Create a de-identified fault library from shadow traffic and seeded synthetic failures. For every item record the gold decision, severity, customer exposure, reversibility, reviewer decision, review time, and downstream cost band. After decisions are collected, the same harness can replay scoring and fit multipliers unattended.

### Application decision

* Set `CustomerFacingCriticalShareMultiplier` to the adjusted ratio of critical consequence among customer-visible versus internal failures. Keep direct monetary impact in `FailureCostUsd`; avoid counting the same consequence in both the multiplier and cost.
* Keep `CustomerFacingPercent` at zero unless evidence shows customer-facing wording or policy constraints reduce model task success after all other features are controlled.
* Set `HumanApprovalCriticalMultiplier` to residual critical actions after review divided by critical actions before review, stratified by reviewer expertise and failure type.
* Do not update these values from an oracle gate, an LLM reviewer, or public model-only data.

## Promotion criteria

A modifier or curve update is eligible for promotion only when:

* its direction and magnitude are pre-specified or estimated on a training split;
* the final value improves held-out calibration or decision loss;
* its bootstrap or posterior interval is reported;
* its sign agrees across at least 80% of dataset/model holdouts;
* it is not duplicating another modifier's pathway;
* the improvement is large enough to change at least one plausible model-selection decision; and
* the profile, data revisions, prompts, scorers, and model versions are fully reproducible.

If evidence is inconclusive, retain the current prior and label its interval. If effects differ materially by category or implementation quality, prefer a profile table or a more specific UI input to one global average.

## Recommended implementation sequence

1. Build the manifest, provider, storage, and deterministic scoring core.
2. Implement experiment 1 and freeze calibration profile schema version 1.
3. Add paired perturbation support for experiments 2, 3, and 6.
4. Add containerized executable/tool environments for experiments 5 and 8.
5. Add repeated-run scheduling and fit experiment 7.
6. Refit all capability-layer parameters jointly, then fit risk-layer multipliers on frozen capability predictions.
7. Generate a candidate C# or JSON calibration profile and compare application recommendations before/after on a fixed scenario suite.
8. Promote only after holdout metrics and recommendation changes are reviewed.

This order prevents risk controls, retries, or partial-value logic from distorting the intelligence curve fit.

## Harness implementation status

The first implementation slice lives in this folder and covers the shared core needed before experiment-specific integrations:

- strict YAML manifest validation and canonical SHA-256 manifest hashes;
- a provider-neutral request and response contract;
- dataset and scorer adapter contracts;
- a hash-verified JSONL dataset adapter;
- normalized SQLite run, attempt, score, case-feature, and response-cache tables;
- content-addressed, atomic raw request and response storage;
- bounded asynchronous execution, provider concurrency limits, rate-limit hooks, transport retries, caching, and resumability;
- normalized exact-match and token-F1 scorers; and
- a credential-free fake provider and smoke manifest that exercise the complete path.

Run the tests from `CalibrationExperiments`:

```bash
PYTHONPATH=. python -m unittest discover -s tests -v
```

Run the smoke experiment:

```bash
PYTHONPATH=. python -m calibration run manifests/smoke.yaml
```

Run state is written to `.calibration-runs/runs.sqlite3`. Raw and normalized provider artifacts are stored by content hash under `.calibration-runs/objects`. Use `--resume-run-id <id>` to continue an interrupted run without executing completed work items again.

The foundation also provides:

- Python 3.12.x pinned in `pyproject.toml` and a checked-in `uv.lock`; `uv sync --locked` creates the clean environment.
- JSON Schema 2020-12 records under `calibration/schemas/` for manifests, runs, work items, attempts, scores, case features, model snapshots, fitted estimates, profiles, provenance, and artifact metadata.
- Resolved manifests with explicit sample IDs, prompt/scorer locks, condition hashes, routing, budgets, holdouts, retries, container digests, fitting seed, and a second resolved-manifest hash saved with each run.
- Environment-only `OPENROUTER_API_KEY` handling and recursive redaction for headers, URLs, nested payloads, artifacts, and errors. Credentials are never part of a manifest or database record.
- Versioned SQLite migrations, lifecycle states (`created`, `running`, `pausing`, `completed`, `failed`, `cancelled`), lease recovery, unique logical attempts, foreign-keyed scores, model snapshots, fitted-estimate lineage, and run provenance.
- Atomic content-addressed artifacts with metadata sidecars and SHA-256 readback checks. `llm-value-calibration audit <run-id>` audits both lineage and artifacts.
- Deterministic immutable Parquet snapshots for normalized runs, attempts, scores, case features, model snapshots, and fitted estimates.

Useful locked commands:

```bash
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m calibration run manifests/smoke.yaml
uv run --locked python -m calibration export <run-id>
uv run --locked python -m calibration audit <run-id>
uv run --locked pip-audit
uv run --locked pip-licenses --format=markdown --with-urls --with-authors
```

The next implementation slice should add the first real provider adapter and the initial exact-score benchmark adapter for experiment 1.
