# Experiment 1 capability-curve runbook

Experiment 1 changes only the capability curve and proportional `8:5:3` tau values. The recommendation engine applies the profile once:

`raw AA index -> profile curve -> success probability -> expected value`

Difficulty, business value, critical-failure, retry, and provisional error-floor inputs remain unchanged.

## Prerequisites

- Python 3.12 environment from `uv sync --frozen`.
- `OPENROUTER_API_KEY` with enough credit for the approved run.
- `HF_TOKEN` after accepting the GPQA access conditions and granting the token gated-repository read access. The preparation command authenticates both services before acquiring any dataset.
- A reviewed ten-model panel JSON containing exactly the `PanelCandidate` fields accepted by `calibration.experiment1`, with two provider-diverse models in each accepted band from 10 through 50.

## Freeze data and run plan

```powershell
$env:HF_TOKEN = [Environment]::GetEnvironmentVariable("HF_TOKEN", "User")
$env:OPENROUTER_API_KEY = [Environment]::GetEnvironmentVariable("OPENROUTER_API_KEY", "User")
.\.venv\Scripts\python.exe -m calibration prepare-experiment-1
.\.venv\Scripts\python.exe -m calibration plan-experiment-1 .calibration-runs\experiment-1\reviewed-panel.json `
  --cases .calibration-runs\experiment-1\dataset\experiment-1-cases.jsonl `
  --repeat-cases .calibration-runs\experiment-1\dataset\experiment-1-repeat-cases.jsonl `
  --dataset-lock .calibration-runs\experiment-1\dataset\experiment-1-dataset-lock.json
```

Review the source hashes, exclusions, 10 selected model mappings, three model holdouts, projected token count, and spend. Both manifests use `max_output_tokens: 2048`; this budget includes provider reasoning tokens and leaves room for a final answer. The main run contains 20,000 scored calls and the stochastic repeat run contains 12,000 scored calls. Manifest transport ceilings separately reserve up to two retries (60,000 and 36,000 provider attempts); retries do not add experiment cells. Planning refuses a worst-case estimate above $250 and never reduces sample size or model count.

Run `preflight` for both generated manifests using the same frozen OpenRouter catalog. Execute the `main` manifest first and the `repeats` manifest second. Use `--resume-run-id` after interruption. Export and audit each completed run before fitting.

## LLM-judge rescore

The approved Experiment 1 decision uses blinded semantic judgments from
`deepseek/deepseek-v4-flash`, not the stored exact-match labels. This is an explicit
exception to the repository's normal validated-judge policy: the judge is
unvalidated and blind-self-judges DeepSeek source rows. Every evidence artifact and
calibration card must state those limitations.

```powershell
.\.venv\Scripts\python.exe -m calibration prepare-experiment-1-judge <source arguments>
.\.venv\Scripts\python.exe -m calibration preflight <judge-main.yaml> --catalog <catalog.json> --output <main-preflight.json>
.\.venv\Scripts\python.exe -m calibration preflight <judge-repeats.yaml> --catalog <catalog.json> --output <repeat-preflight.json>
.\.venv\Scripts\python.exe -m calibration run <judge-main.yaml> --catalog <catalog.json> --output <main-run>
.\.venv\Scripts\python.exe -m calibration run <judge-repeats.yaml> --catalog <catalog.json> --output <repeat-run>
```

The judge sees only the reference answer and untrusted generated response, but never
the underlying question, source model identity, or old deterministic score. It is
instructed to compare those two values directly without solving the original task.
It returns a structured `correct`, `incorrect`, or `abstain` verdict. The primary fit excludes abstentions;
the decision must remain stable when all abstentions are treated as incorrect and
then correct. Judge confidence is diagnostic only.

If either run has a failed transport cell, malformed JSON, or a length-truncated
judgment, generate and run `prepare-experiment-1-judge-recovery`. It selects only
those cells, changes the prompt version so malformed cached output cannot win,
raises targeted recovery to 16,384 output tokens, and checks worst-case retries
against the separate $25 judge ceiling.

## Fit and promotion

Build the judge-only fitting lock and fit the four registered alternatives:

```powershell
.\.venv\Scripts\python.exe -m calibration build-experiment-1-judge-fitting-data <judge arguments>
.\.venv\Scripts\python.exe -m calibration fit-experiment-1-judge <judge-fitting-data.jsonl> `
  --lock <judge-fitting-lock.json> --bootstrap-replicates 100 --output <fit-output>
dotnet run --project ..\AAInteractiveValueAnalyzer\AAInteractiveValueAnalyzer.ScenarioRunner `
  -- <baseline-profile.json> <candidate-profile.json> <scenario-diff.json>
```

The fitter applies dataset, model, and item nuisance effects and reports the single
prompt effect as non-estimable. It uses positive monotone slope transforms,
log-scale tau with wide bounds, separate complete model and dataset holdouts,
profile-likelihood intervals, and 100 model plus 100 case-group refits.

Promotion evidence must bind the candidate and baseline hashes and include at least 2% relative held-out log-loss improvement, 1% relative held-out Brier improvement, 80% grouped-bootstrap sign agreement, scenario diffs, provenance, limitations, and explicit reviewer approval. Failed evidence retains the candidate but cannot update the active index.

Promote into `AAInteractiveValueAnalyzer/AAInteractiveValueAnalyzer/CalibrationProfiles`. The server validates the active path, profile semantics, and canonical SHA-256 hash and serves it at `GET /api/calibration-profile` with an ETag. Rollback points the append-only index to a prior immutable profile.

## Result-to-EV interpretation

| Result | Engine adjustment | EV effect |
|---|---|---|
| Higher band slope | More adjusted capability per AA point | Widens evidence-supported success and EV differences |
| Lower band slope | Less adjusted capability per AA point | Compresses unsupported model EV gaps |
| Larger proportional tau | Flatter success transition | Reduces EV sensitivity near the difficulty threshold |
| Smaller proportional tau | Steeper success transition | Increases threshold-crossing impact on EV |
| Provisional repeat floor | Evidence only; active floor stays 0.01 | Avoids premature high-reliability EV changes before Experiment 7 |
| Failed acceptance gate | Candidate remains inactive | Production recommendations continue using the prior profile |
