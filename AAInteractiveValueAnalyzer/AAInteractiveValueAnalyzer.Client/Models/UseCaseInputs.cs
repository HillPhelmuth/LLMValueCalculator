namespace AAInteractiveValueAnalyzer.Client.Models;

/// <summary>
/// User-controlled workload, risk, and economic assumptions.
/// Percent properties use the user-facing 0-100 scale.
/// </summary>
public sealed class UseCaseInputs
{
    public string UseCaseName { get; set; } = "Example workload";
    public TaskCategoryOption TaskCategory { get; set; } = TaskCategoryOption.SimpleRag;
    public TaskCategoryOption LastAppliedTaskCategory { get; set; } = TaskCategoryOption.SimpleRag;
    public DifficultySensitivityOption DifficultySensitivity { get; set; } = DifficultySensitivityOption.Normal;
    public double BaseDifficulty { get; set; } = 10;
    public ContextRequirementOption ContextRequirement { get; set; } = ContextRequirementOption.MediumMostlyRelevant;
    public ReasoningDepthOption ReasoningDepth { get; set; } = ReasoningDepthOption.Light;
    public DomainSpecificityOption DomainSpecificity { get; set; } = DomainSpecificityOption.GeneralKnowledge;
    public ToolUseOption ToolUse { get; set; } = ToolUseOption.None;
    public VerifiabilityOption Verifiability { get; set; } = VerifiabilityOption.MostlyVerifiableByReviewer;
    public OutputConstraintOption OutputConstraint { get; set; } = OutputConstraintOption.FreeText;

    public bool HasRepresentativeEvalSet { get; set; }
    public int EvalSetSize { get; set; } = 100;
    public bool HasDeterministicValidation { get; set; }
    public bool HasRagOrDomainContext { get; set; }
    public bool RequiresStrictStructuredOutput { get; set; }
    public bool HasSilentFailureRisk { get; set; } = true;
    public bool CustomerFacing { get; set; }
    public bool HumanApprovalForHighRiskActions { get; set; }
    public bool RetriesAllowed { get; set; } = true;
    public int MaxAttempts { get; set; } = 2;

    public double RequiredSuccessRate { get; set; } = 90;
    public double AllowedCriticalFailureRate { get; set; } = 1;
    public double CriticalFailureShareOfFailures { get; set; } = 20;
    public double CostMultiplier { get; set; } = 1;
    /// <summary>Business value of 1,000 fully-correct successful outcomes.</summary>
    public double BusinessValuePerThousandSuccessesUsd { get; set; } = 500;

    /// <summary>Business value of 1,000 degraded-but-acceptable successful outcomes.</summary>
    public double AcceptableValuePerThousandSuccessesUsd { get; set; } = 100;
    public double GoodOutcomeShareOfSuccesses { get; set; } = 75;
    public double FailureCostUsd { get; set; } = 1;
    /// <summary>Economic cost of 1,000 non-critical failed outcomes.</summary>
    public double BenignFailureCostPerThousandFailuresUsd { get; set; } = 25;
    public double HumanReviewCostUsd { get; set; }
    public double OperationalRetryCostUsd { get; set; } = 0.02;
    public double LatencyCostPerSecondUsd { get; set; }
    public double MaxAcceptableLatencySeconds { get; set; } = double.PositiveInfinity;

    public void ApplyCategoryDefaults(TaskCategoryProfile profile)
    {
        if (profile.Category != TaskCategory)
        {
            throw new ArgumentException("The preset category must match the selected task category.", nameof(profile));
        }

        BaseDifficulty = profile.DefaultBaseDifficulty ?? BaseDifficulty;
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
        MaxAttempts = profile.DefaultMaxAttempts ?? MaxAttempts;
        LastAppliedTaskCategory = TaskCategory;
    }
}
