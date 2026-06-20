![image](/LlmRoiCalc-icon-256.jpg)

# Interactive LLM EV Calculator and Model Selection Tool

Interactive LLM EV Calculator is a workload-specific model selection tool. It helps compare LLMs by combining Artificial Analysis [intelligence](https://artificialanalysis.ai/#intelligence) and [cost priors](https://artificialanalysis.ai/#price-and-cost) with your own estimate of task difficulty, quality requirements, failure tolerance, retry behavior, review cost, and business value.

The goal is not to declare one universal best model. The goal is to make the tradeoff explicit for a specific use case.

## What It Helps Answer

Use the analyzer when you want to estimate:

- Which models are likely to meet a required success rate.
- Which models stay under an acceptable critical-failure rate.
- How retries, validation, and human review change the economics.
- Whether a cheaper model has enough quality for the workload.
- Whether a higher-quality model earns back its higher direct cost.
- How task category and guardrails affect the recommendation.

The output should be treated as a planning estimate. Production decisions should be validated with real evals and real usage data.

## Basic Workflow

1. Choose a task category.

   Categories act as presets and priors. For example, extraction and classification start with lower modeled difficulty than research or agentic workflows.

2. Apply or ignore the category defaults.

   The app may offer recommended defaults for context size, reasoning depth, tool use, verifiability, output format, validation, and retries. Applying defaults is optional so category changes do not silently overwrite your scenario.

3. Tune the workload inputs.

   Adjust the base difficulty, context, reasoning, domain specificity, tool use, verifiability, and output constraints until they describe the actual work rather than the broad category label.

4. Configure guardrails.

   Mark whether the workload has representative evals, deterministic validation, RAG or supplied domain context, strict structured output, silent-failure risk, customer-facing exposure, or human approval for high-risk actions.

5. Set eligibility thresholds.

   Required success rate and allowed critical-failure rate are hard filters. A model can have attractive cost or expected value and still be excluded if it fails these thresholds.

6. Enter economics.

   Business value per success, failure cost, human review cost, operational retry cost, monthly volume in 1,000-task batches, and cost multiplier determine expected value and monthly impact.

7. Compare recommendations.

   The analyzer highlights eligible models and shows expected success, critical-failure rate, expected attempts, direct cost per 1,000 tasks, cost per 1,000 successful tasks, success per dollar, expected value per 1,000 tasks, and monthly expected value.

## Methodology

The analyzer builds a scenario-specific difficulty score, then compares each model against that score.

At a high level:

```text
effective difficulty =
  base task difficulty
  + workload adjustments
  + category prior
  - guardrail reductions
```

Workload adjustments include context size/noise, reasoning depth, domain specificity, tool use, verifiability, and output constraints.

Guardrails can reduce modeled difficulty or critical-failure exposure when they make failures easier to detect or recover from. Examples include representative eval sets, deterministic validation, strict schema output, supplied domain context, and human approval for high-risk actions.

The model comparison then estimates:

```text
single-attempt success = sigmoid((adjusted model intelligence - effective difficulty) / tau)
```

`tau` is controlled by the sensitivity setting:

- Soft: quality changes more gradually across models.
- Normal: default slope.
- Sharp: small intelligence differences matter more.

If retries are allowed, the analyzer estimates effective success across multiple attempts. It also estimates expected attempts, retry overhead, model cost, review cost, direct cost, and cost per 1,000 successful tasks on a 1,000-task basis.

Expected value is modeled as:

```text
expected value per 1000 tasks =
   1000 * (
      value from successful outcomes
      - expected failure cost
      - expected direct cost
   )
```

Monthly expected value multiplies that per-1,000-task estimate by monthly volume, where monthly volume is entered in 1,000-task batches.

## Task Categories

Task category is intentionally not the whole score. A category provides a starting point, not a final truth.

For example, code generation can range from a simple DTO to a production deployment pipeline. Research can range from a short grounded comparison to a high-stakes synthesis over weak evidence. The detailed inputs should carry most of that distinction.

The app currently supports:

- Extraction
- Classification / routing
- Summarization
- Code generation
- Agentic workflow
- Drafting / writing
- Research / analysis
- Other

Each category can contribute:

- Recommended defaults for workload and guardrail inputs.
- A small residual difficulty adjustment.
- Warnings when inputs look mismatched for the selected category.
- Category-specific guardrail behavior.

Examples:

- Extraction with strict structured output and deterministic validation slightly reduces modeled critical-failure exposure.
- Classification/routing without a representative eval set is flagged because labeled examples are usually important.
- Summarization may warn about omission or factual drift when silent-failure risk is disabled.
- Code generation with deterministic validation and retries receives a lower category penalty because tests and compile checks improve recoverability.
- Research/analysis without grounding warns about synthesis and hallucination risk.
- Agentic workflows with weak tool-use settings or irreversible actions without human approval are flagged.

## How To Interpret Results

Eligible models meet both hard thresholds:

- Estimated effective success is at or above the required success rate.
- Estimated critical-failure rate is at or below the allowed critical-failure rate.

Rankings are then based on economics and quality:

- Best expected value: highest estimated value per 1,000 tasks after costs and failures.
- Cheapest eligible: lowest expected direct cost per 1,000 tasks among models that meet thresholds.
- Highest quality eligible: strongest estimated success among positive-value eligible models.
- Best success per dollar: most modeled success per unit cost.

No single ranking is always correct. For low-risk internal automation, success per dollar may matter most. For customer-facing or regulated workflows, eligibility, critical-failure exposure, and human review assumptions may matter more than direct cost.

## Important Assumptions

The analyzer uses Artificial Analysis intelligence and cost values as priors. They are useful starting points, but they are not substitutes for workload-specific evaluation.

The success curve assumes that adjusted model intelligence and effective workload difficulty can be compared on a shared scale. This is a simplification.

The cost inputs are modeled into 1,000-task batch outputs, not necessarily per API call. Real cost should include token usage, tool calls, retries, latency, caching, orchestration, review labor, incident handling, and vendor-specific pricing.

Critical failures are estimated from overall failure probability, a configured share of failures that are critical, and exposure multipliers from risk factors and guardrails.

Retries are modeled as independent attempts. Real retries may be correlated if the model fails for the same reason repeatedly.

Guardrails are modeled as coarse effects. A strong validator, weak validator, human reviewer, or eval set can have very different real-world impact depending on implementation quality.

## Limitations

This is a decision-support calculator, not a benchmark.

It does not run prompts against models.

It does not measure latency, context-window limits, rate limits, availability, privacy posture, data residency, contractual terms, or operational maturity.

It does not know your actual prompt quality, retrieval quality, test coverage, review rubric, or user tolerance for errors.

It does not replace representative evals. For production use, replace the estimated success curve with measured results from your own examples.

It assumes the model catalog values are current enough for planning. Update the catalog when Artificial Analysis data, vendor pricing, or available models change.

## Minimal Developer Notes

This is a .NET/Blazor solution. The main analyzer UI is in `AAInteractiveValueAnalyzer/AAInteractiveValueAnalyzer.Client/Pages/Analyzer.razor`, with supporting logic in:

- `AAInteractiveValueAnalyzer/AAInteractiveValueAnalyzer.Client/Services/RecommendationEngine.cs`
- `AAInteractiveValueAnalyzer/AAInteractiveValueAnalyzer.Client/Services/ModelCatalog.cs`
- `AAInteractiveValueAnalyzer/AAInteractiveValueAnalyzer.Client/Models/UseCaseInputs.cs`
- `AAInteractiveValueAnalyzer/AAInteractiveValueAnalyzer.Client/Models/AnalyzerOptions.cs`

To run locally from the solution root:

```powershell
dotnet run --project .\AAInteractiveValueAnalyzer\AAInteractiveValueAnalyzer\AAInteractiveValueAnalyzer.csproj
```

Update `ModelCatalog.cs` when the model list, intelligence values, or cost priors need to change.
