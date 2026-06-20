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
    public double FailureCostUsd { get; set; } = 0.5;
    public double HumanReviewCostUsd { get; set; } = 0;
    public double OperationalRetryCostUsd { get; set; } = 0;
    public int MonthlyVolume { get; set; } = 1;
    public double CostMultiplier { get; set; } = 1.0;

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
