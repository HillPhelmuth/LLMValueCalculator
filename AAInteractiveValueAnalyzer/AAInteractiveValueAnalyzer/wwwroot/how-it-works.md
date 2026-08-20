<p style="margin-left:30%">
<img src="/LlmEvIconNew-small.png" alt="image"/>
</p>

# Model Value Analyzer
**An Interactive LLM EV Calculator and Model Selection Tool**

Model Value Analyzer is a workload-specific model selection tool. It helps compare LLMs by combining Artificial Analysis [intelligence](https://artificialanalysis.ai/#intelligence) and [cost](https://artificialanalysis.ai/#price-and-cost) data with your own estimate of task difficulty, quality requirements, failure tolerance, retry behavior, review cost, and business value.

The goal is to make the tradeoff explicit for a specific use case.

## What It Helps Answer

Use the analyzer when you want to estimate:

- Which models are likely to meet a required success rate for your task.
- Which models stay under an acceptable critical-failure rate.
- How retries, validation, and human review change the economics.
- Whether a cheaper model has enough quality for the workload.
- Whether a higher-quality model earns back its higher direct cost.
- How task category and guardrails affect the recommendation.

**The output should be treated as a planning estimate.** Production decisions should be validated with real evals and real usage data.

## Basic Workflow

1. Choose a task category.

   Categories act as presets and assumptions. For example, extraction and classification start with lower modeled difficulty than research or agentic workflows.

2. Tune the workload inputs.

   Adjust the base difficulty, context, reasoning, domain specificity, tool use, verifiability, and output constraints until they describe the actual work rather than the broad category label.

4. Configure guardrails.

   Mark whether the workload has representative evals, deterministic validation, RAG or supplied domain context, strict structured output, silent-failure risk, customer-facing exposure, or human approval for high-risk actions.

5. Set eligibility thresholds.

   Required success rate and allowed critical-failure rate are hard filters. A model can have attractive cost or expected value and still be excluded if it fails these thresholds.

6. Enter economics.

   Business value per 1,000 full successes, value per 1,000 partial successes, cost per 1,000 benign failures, critical-failure cost per incident, human review cost, operational retry cost, and the manual AA adjustment determine expected value. The manual adjustment multiplies the automatic workload-cost factor.

7. Compare recommendations.

   The analyzer highlights eligible models and shows expected success, critical-failure rate, expected attempts, direct cost per 1,000 tasks, cost per 1,000 successful tasks, success per dollar, and expected value per 1,000 tasks.

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

Model cost is also workload-aware. Context, reasoning depth, and tool use feed a log-linear factor fitted to median benchmark-cost shares from the Artificial Analysis Intelligence Index. Each share is normalized by its benchmark's Index weight, then the fitted factor is clamped to the observed range. Domain specificity, verifiability, and output constraints remain success/difficulty inputs rather than unsupported direct-cost assumptions. The manual AA adjustment multiplies this automatic factor, which also scales expected end-to-end latency before retries are applied.

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
   blended value per 1000 successes * effective success rate
   - critical failure cost per incident * critical failure rate * 1000
   - benign failure cost per 1000 failures * benign failure rate
   - direct and latency costs per 1000 tasks
```

## Task Categories

Task category is **mostly** just a combination of preset workload inputs and guardrail assumptions. Each set of assumptions can be tuned to match the actual workload.

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

### The Ranked Eligible Models Tab

Eligible models meet both hard thresholds:

- Estimated effective success is at or above the required success rate.
- Estimated critical-failure rate is at or below the allowed critical-failure rate.

Rankings are then based on economics and quality:

- Best expected value: highest estimated value per 1,000 tasks after costs and failures.
- Cheapest eligible: lowest expected direct cost per 1,000 tasks among models that meet thresholds.
- Highest quality eligible: strongest estimated success among positive-value eligible models.
- Best success per dollar: most modeled success per unit cost.

No single ranking is always correct. For low-risk internal automation, success per dollar may matter most. For customer-facing or regulated workflows, eligibility, critical-failure exposure, and human review assumptions may matter more than direct cost.

### The Full Universe Tab

All models with sufficient data on [Artificial Analysis](https://artificialanalysis.ai) are shown, including those that fail the hard thresholds. This tab is useful for exploring tradeoffs and understanding how close a model is to eligibility.


## Important Assumptions

The analyzer uses Artificial Analysis intelligence and cost values as assumptions. They are useful starting points, but they are not substitutes for workload-specific evaluation.

Critical failures are estimated from overall failure probability, a configured share of failures that are critical, and exposure multipliers from risk factors and guardrails.

Retries are modeled as independent attempts. 

Guardrails are modeled as coarse effects. A strong validator, weak validator, human reviewer, or eval set can have very different real-world impact depending on implementation quality.

