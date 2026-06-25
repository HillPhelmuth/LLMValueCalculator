using AAInteractiveValueAnalyzer.Client.Services;

namespace AAInteractiveValueAnalyzer.Client.Models;

public sealed class UseCaseInputs
{
    public UseCaseInputs(bool preventDefaults = false)
    {
        if (preventDefaults)
        {
            return;
        }

        var profile = RecommendationEngine.ResolveTaskCategoryProfile(TaskCategory);
        ApplyCategoryDefaults(profile);
    }

    public string UseCaseName { get; set; } = "Support-ticket analysis";
    public TaskCategoryOption TaskCategory { get; set; } = TaskCategoryOption.ClassificationRouting;
    public TaskCategoryOption LastAppliedTaskCategory { get; set; } = TaskCategoryOption.ClassificationRouting;

    public double BaseDifficulty { get; set; }
    public ContextRequirementOption ContextRequirement { get; set; }
    public ReasoningDepthOption ReasoningDepth { get; set; }
    public DomainSpecificityOption DomainSpecificity { get; set; } = DomainSpecificityOption.GeneralKnowledge;
    public ToolUseOption ToolUse { get; set; }
    public VerifiabilityOption Verifiability { get; set; }
    public OutputConstraintOption OutputConstraint { get; set; }
    public DifficultySensitivityOption DifficultySensitivity { get; set; } = DifficultySensitivityOption.Normal;

    public bool HasRepresentativeEvalSet { get; set; } = true;
    public int EvalSetSize { get; set; } = 100;
    public bool HasDeterministicValidation { get; set; } = true;
    public bool HasRagOrDomainContext { get; set; } = true;
    public bool RequiresStrictStructuredOutput { get; set; } = true;
    public bool HasSilentFailureRisk { get; set; } = false;
    public bool CustomerFacing { get; set; } = false;
    public bool HumanApprovalForHighRiskActions { get; set; } = false;

    public double RequiredSuccessRate { get; set; } = 95;
    public double AllowedCriticalFailureRate { get; set; } = 2;
    public double CriticalFailureShareOfFailures { get; set; } = 20;

    public double BusinessValuePerSuccessUsd { get; set; } = 0.5;

    // Failure is no longer charged as a single flat penalty. A failed task is either *critical*
    // (genuinely harmful: a customer saw a wrong answer, an irreversible action fired, a silent
    // error propagated) or *benign* (caught and retried, or otherwise cheap). The engine already
    // computes the critical share of failures and every guardrail multiplier acts on it, so the two
    // costs are priced separately and the asymmetric-cost philosophy actually moves the ranking
    // instead of only the advisory WorstCaseFailureCostUsd metric.
    //
    // FailureCostUsd is the cost of a *critical* failure. (Name retained for compatibility; it has
    // always been the unit cost behind WorstCaseFailureCostUsd, which is FailureCostUsd x critical
    // rate.) This is the expensive tail and is usually the larger of the two.
    public double FailureCostUsd { get; set; } = 0.5;

    // BenignFailureCostUsd is the cost of a non-critical failure: the retry/throwaway cost of a task
    // that failed but was caught. Defaults equal to FailureCostUsd so that, until a user
    // distinguishes them, total failure cost is FailureCostUsd x (1 - success) -- numerically
    // identical to the old single-term model. Lower it to express that benign failures are cheap.
    public double BenignFailureCostUsd { get; set; } = 0.5;

    public double HumanReviewCostUsd { get; set; } = 0;
    public double OperationalRetryCostUsd { get; set; } = 0;
    public int MonthlyVolume { get; set; } = 1;
    public double CostMultiplier { get; set; } = 1.0;

    // Latency. Two channels, mirroring how success has both an EV contribution and a hard gate.
    // Both default to latency-neutral so existing analyses are unchanged until a user opts in.
    //
    // LatencyCostPerSecondUsd: soft cost. The dollar value of a second of wall-clock per task. For
    // interactive / customer-facing work this is the cost of a user waiting; leave at 0 for batch
    // work where nobody is blocked on any single task. Multiplied by expected end-to-end seconds
    // and expected attempts inside the engine, so a model that retries waits twice.
    public double LatencyCostPerSecondUsd { get; set; } = 0;

    // MaxAcceptableLatencySeconds: hard gate. A model whose expected end-to-end time exceeds this is
    // ineligible regardless of quality or cost, the same way a model below RequiredSuccessRate is.
    // Default is effectively "no ceiling". Models with no latency data are never gated out.
    public double MaxAcceptableLatencySeconds { get; set; } = double.PositiveInfinity;

    public bool RetriesAllowed { get; set; } = true;
    public int MaxAttempts { get; set; } = 2;

    public void ApplyCategoryDefaults(TaskCategoryProfile profile)
    {
        TaskCategory = profile.Category;
        LastAppliedTaskCategory = profile.Category;

        if (profile.DefaultBaseDifficulty is { } baseDifficulty)
        {
            BaseDifficulty = baseDifficulty;
        }

        ContextRequirement = profile.DefaultContextRequirement ?? ContextRequirement;
        ReasoningDepth = profile.DefaultReasoningDepth ?? ReasoningDepth;
        DomainSpecificity = profile.DefaultDomainSpecificity ?? DomainSpecificity;
        ToolUse = profile.DefaultToolUse ?? ToolUse;
        Verifiability = profile.DefaultVerifiability ?? Verifiability;
        OutputConstraint = profile.DefaultOutputConstraint ?? OutputConstraint;

        HasRepresentativeEvalSet = profile.DefaultHasRepresentativeEvalSet ?? HasRepresentativeEvalSet;
        HasDeterministicValidation = profile.DefaultHasDeterministicValidation ?? HasDeterministicValidation;
        HasRagOrDomainContext = profile.DefaultHasRagOrDomainContext ?? HasRagOrDomainContext;
        RequiresStrictStructuredOutput = profile.DefaultRequiresStrictStructuredOutput ?? RequiresStrictStructuredOutput;
        HasSilentFailureRisk = profile.DefaultHasSilentFailureRisk ?? HasSilentFailureRisk;
        CustomerFacing = profile.DefaultCustomerFacing ?? CustomerFacing;
        HumanApprovalForHighRiskActions = profile.DefaultHumanApprovalForHighRiskActions ?? HumanApprovalForHighRiskActions;
        RetriesAllowed = profile.DefaultRetriesAllowed ?? RetriesAllowed;

        if (profile.DefaultMaxAttempts is { } maxAttempts)
        {
            MaxAttempts = maxAttempts;
        }
        else if (!RetriesAllowed)
        {
            MaxAttempts = 1;
        }
    }
}
