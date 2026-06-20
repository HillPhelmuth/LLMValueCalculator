namespace AAInteractiveValueAnalyzer.Client.Models;

public sealed record TaskCategoryProfile(
    TaskCategoryOption Category,
    double BaseDifficultyPercentResidual,
    double? DefaultBaseDifficulty = null,
    ContextRequirementOption? DefaultContextRequirement = null,
    ReasoningDepthOption? DefaultReasoningDepth = null,
    DomainSpecificityOption? DefaultDomainSpecificity = null,
    ToolUseOption? DefaultToolUse = null,
    VerifiabilityOption? DefaultVerifiability = null,
    OutputConstraintOption? DefaultOutputConstraint = null,
    bool? DefaultHasRepresentativeEvalSet = null,
    bool? DefaultHasDeterministicValidation = null,
    bool? DefaultHasRagOrDomainContext = null,
    bool? DefaultRequiresStrictStructuredOutput = null,
    bool? DefaultHasSilentFailureRisk = null,
    bool? DefaultCustomerFacing = null,
    bool? DefaultHumanApprovalForHighRiskActions = null,
    bool? DefaultRetriesAllowed = null,
    int? DefaultMaxAttempts = null)
{
    public string Name => Category.DisplayName();

    public bool HasPresetDefaults =>
        DefaultBaseDifficulty.HasValue ||
        DefaultContextRequirement.HasValue ||
        DefaultReasoningDepth.HasValue ||
        DefaultDomainSpecificity.HasValue ||
        DefaultToolUse.HasValue ||
        DefaultVerifiability.HasValue ||
        DefaultOutputConstraint.HasValue ||
        DefaultHasRepresentativeEvalSet.HasValue ||
        DefaultHasDeterministicValidation.HasValue ||
        DefaultHasRagOrDomainContext.HasValue ||
        DefaultRequiresStrictStructuredOutput.HasValue ||
        DefaultHasSilentFailureRisk.HasValue ||
        DefaultCustomerFacing.HasValue ||
        DefaultHumanApprovalForHighRiskActions.HasValue ||
        DefaultRetriesAllowed.HasValue ||
        DefaultMaxAttempts.HasValue;
}