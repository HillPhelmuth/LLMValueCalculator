using AAInteractiveValueAnalyzer.Client.Models;

namespace AAInteractiveValueAnalyzer.Client.Services;

/// <summary>
/// Calculates task difficulty, risk controls, and model recommendations for a supplied use case.
///
/// Design intent:
/// - Difficulty estimates whether the model can produce a correct first answer.
/// - Guardrails estimate how much bad output is detected or contained.
/// - Exposure estimates how costly or dangerous remaining failures are.
///
/// Keeping those concepts separate avoids treating customer-facing or regulated work as if it
/// inherently makes the model less intelligent. It instead makes failure less acceptable.
/// </summary>
public static class RecommendationEngine_ChatGpt
{
    public const int TaskBatchSize = 1000;

    public const double StrictStructuredOutputWithValidationPercent = 1;
    public const double StrictStructuredOutputWithoutValidationPercent = 5;
    public const double DeterministicValidationDifficultyPercent = -3;
    public const double StrongDeterministicValidationDifficultyPercent = -5;
    public const double SilentFailureResidualDifficultyPercent = 5;
    public const double ResearchWithoutGroundingPercent = 8;

    public static IReadOnlyDictionary<ContextRequirementOption, double> ContextAdjustments { get; } = new Dictionary<ContextRequirementOption, double>
    {
        [ContextRequirementOption.ShortClean] = 0,
        [ContextRequirementOption.MediumMostlyRelevant] = 5,
        [ContextRequirementOption.LargeClean] = 8,
        [ContextRequirementOption.LargeNoisy] = 18,
        [ContextRequirementOption.VeryLargeNoisyCrossDocument] = 32
    };

    public static IReadOnlyDictionary<ReasoningDepthOption, double> ReasoningAdjustments { get; } = new Dictionary<ReasoningDepthOption, double>
    {
        [ReasoningDepthOption.SingleStepTransformation] = 0,
        [ReasoningDepthOption.Light] = 5,
        [ReasoningDepthOption.ModerateMultiStep] = 10,
        [ReasoningDepthOption.DeepConditional] = 20,
        [ReasoningDepthOption.ResearchGradeSynthesisPlanning] = 28
    };

    public static IReadOnlyDictionary<DomainSpecificityOption, double> DomainAdjustments { get; } = new Dictionary<DomainSpecificityOption, double>
    {
        [DomainSpecificityOption.GeneralKnowledge] = 0,
        [DomainSpecificityOption.SomeDomainSpecificTerminology] = 5,
        [DomainSpecificityOption.SpecializedProfessionalDomain] = 11,
        [DomainSpecificityOption.ExpertOrRegulatedDomain] = 14
    };

    public static IReadOnlyDictionary<ToolUseOption, double> ToolAdjustments { get; } = new Dictionary<ToolUseOption, double>
    {
        [ToolUseOption.None] = 0,
        [ToolUseOption.OneOrTwoDeterministicTools] = 4,
        [ToolUseOption.MultipleToolsWithValidation] = 11,
        [ToolUseOption.AutonomousToolSequence] = 20,
        [ToolUseOption.AgenticWorkflowWithIrreversibleActions] = 24
    };

    public static IReadOnlyDictionary<VerifiabilityOption, double> VerifiabilityAdjustments { get; } = new Dictionary<VerifiabilityOption, double>
    {
        [VerifiabilityOption.DeterministicallyTestable] = 0,
        [VerifiabilityOption.MostlyVerifiableByReviewer] = 6,
        [VerifiabilityOption.PartlySubjective] = 12,
        [VerifiabilityOption.HardToDetectWrongAnswers] = 22
    };

    public static IReadOnlyDictionary<OutputConstraintOption, double> OutputAdjustments { get; } = new Dictionary<OutputConstraintOption, double>
    {
        [OutputConstraintOption.FreeText] = 0,
        [OutputConstraintOption.StructuredJsonOrSchema] = 2,
        [OutputConstraintOption.CodeSqlOrExecutableArtifact] = 13,
        [OutputConstraintOption.ExternalFacingOrRegulatedArtifact] = 8
    };

    public static IReadOnlyList<TaskCategoryOption> TaskCategories { get; } = AnalyzerOptionDisplay.Values<TaskCategoryOption>();

    public static IReadOnlyDictionary<DifficultySensitivityOption, double> TauBySensitivity { get; } = new Dictionary<DifficultySensitivityOption, double>
    {
        [DifficultySensitivityOption.Soft] = 8,
        [DifficultySensitivityOption.Normal] = 5,
        [DifficultySensitivityOption.Sharp] = 3
    };

    public static IReadOnlyDictionary<TaskCategoryOption, TaskCategoryProfile> TaskCategoryProfiles { get; } =
        new Dictionary<TaskCategoryOption, TaskCategoryProfile>
        {
            [TaskCategoryOption.Extraction] = new(
                Category: TaskCategoryOption.Extraction,
                BaseDifficultyPercentResidual: -2,
                DefaultBaseDifficulty: 10,
                DefaultContextRequirement: ContextRequirementOption.MediumMostlyRelevant,
                DefaultReasoningDepth: ReasoningDepthOption.SingleStepTransformation,
                DefaultToolUse: ToolUseOption.None,
                DefaultVerifiability: VerifiabilityOption.DeterministicallyTestable,
                DefaultOutputConstraint: OutputConstraintOption.StructuredJsonOrSchema,
                DefaultHasDeterministicValidation: true,
                DefaultRequiresStrictStructuredOutput: true,
                DefaultHasSilentFailureRisk: false,
                DefaultRetriesAllowed: true,
                DefaultMaxAttempts: 2),
            [TaskCategoryOption.ClassificationRouting] = new(
                Category: TaskCategoryOption.ClassificationRouting,
                BaseDifficultyPercentResidual: -2,
                DefaultBaseDifficulty: 12,
                DefaultContextRequirement: ContextRequirementOption.ShortClean,
                DefaultReasoningDepth: ReasoningDepthOption.Light,
                DefaultToolUse: ToolUseOption.None,
                DefaultVerifiability: VerifiabilityOption.DeterministicallyTestable,
                DefaultOutputConstraint: OutputConstraintOption.StructuredJsonOrSchema,
                DefaultHasRepresentativeEvalSet: true,
                DefaultHasDeterministicValidation: true,
                DefaultRequiresStrictStructuredOutput: true,
                DefaultHasSilentFailureRisk: false),
            [TaskCategoryOption.Summarization] = new(
                Category: TaskCategoryOption.Summarization,
                BaseDifficultyPercentResidual: 0,
                DefaultBaseDifficulty: 17,
                DefaultContextRequirement: ContextRequirementOption.LargeClean,
                DefaultReasoningDepth: ReasoningDepthOption.ModerateMultiStep,
                DefaultToolUse: ToolUseOption.None,
                DefaultVerifiability: VerifiabilityOption.MostlyVerifiableByReviewer,
                DefaultOutputConstraint: OutputConstraintOption.FreeText,
                DefaultHasSilentFailureRisk: true),
            [TaskCategoryOption.CodeGeneration] = new(
                Category: TaskCategoryOption.CodeGeneration,
                BaseDifficultyPercentResidual: 2,
                DefaultBaseDifficulty: 26,
                DefaultContextRequirement: ContextRequirementOption.MediumMostlyRelevant,
                DefaultReasoningDepth: ReasoningDepthOption.ModerateMultiStep,
                DefaultToolUse: ToolUseOption.OneOrTwoDeterministicTools,
                DefaultVerifiability: VerifiabilityOption.DeterministicallyTestable,
                DefaultOutputConstraint: OutputConstraintOption.CodeSqlOrExecutableArtifact,
                DefaultHasDeterministicValidation: true,
                DefaultHasSilentFailureRisk: false,
                DefaultRetriesAllowed: true,
                DefaultMaxAttempts: 2),
            [TaskCategoryOption.AgenticWorkflow] = new(
                Category: TaskCategoryOption.AgenticWorkflow,
                BaseDifficultyPercentResidual: 6,
                DefaultBaseDifficulty: 34,
                DefaultContextRequirement: ContextRequirementOption.LargeClean,
                DefaultReasoningDepth: ReasoningDepthOption.DeepConditional,
                DefaultToolUse: ToolUseOption.MultipleToolsWithValidation,
                DefaultVerifiability: VerifiabilityOption.MostlyVerifiableByReviewer,
                DefaultOutputConstraint: OutputConstraintOption.StructuredJsonOrSchema,
                DefaultHasRagOrDomainContext: true,
                DefaultHasSilentFailureRisk: true),
            [TaskCategoryOption.DraftingWriting] = new(
                Category: TaskCategoryOption.DraftingWriting,
                BaseDifficultyPercentResidual: 0,
                DefaultBaseDifficulty: 16,
                DefaultContextRequirement: ContextRequirementOption.MediumMostlyRelevant,
                DefaultReasoningDepth: ReasoningDepthOption.ModerateMultiStep,
                DefaultToolUse: ToolUseOption.None,
                DefaultVerifiability: VerifiabilityOption.PartlySubjective,
                DefaultOutputConstraint: OutputConstraintOption.FreeText,
                DefaultHasSilentFailureRisk: true),
            [TaskCategoryOption.ResearchAnalysis] = new(
                Category: TaskCategoryOption.ResearchAnalysis,
                BaseDifficultyPercentResidual: 5,
                DefaultBaseDifficulty: 30,
                DefaultContextRequirement: ContextRequirementOption.LargeNoisy,
                DefaultReasoningDepth: ReasoningDepthOption.ResearchGradeSynthesisPlanning,
                DefaultToolUse: ToolUseOption.MultipleToolsWithValidation,
                DefaultVerifiability: VerifiabilityOption.HardToDetectWrongAnswers,
                DefaultOutputConstraint: OutputConstraintOption.FreeText,
                DefaultHasRagOrDomainContext: true,
                DefaultHasSilentFailureRisk: true),
            [TaskCategoryOption.Other] = new(
                Category: TaskCategoryOption.Other,
                BaseDifficultyPercentResidual: 0)
        };

    public static AnalysisSummary Analyze(UseCaseInputs inputs)
    {
        var difficultyFactors = new List<string>();
        var guardrailFactors = new List<string>();
        var categoryProfile = ResolveTaskCategoryProfile(inputs.TaskCategory);

        var difficulty = NormalizeBaseDifficulty(inputs.BaseDifficulty, categoryProfile, out var baseDifficultyFactor);
        var normalizedBaseDifficulty = difficulty;
        difficultyFactors.Add(baseDifficultyFactor);

        AddPercentAdjustment(difficultyFactors, "Context", inputs.ContextRequirement, ContextAdjustments, normalizedBaseDifficulty, ref difficulty);
        AddPercentAdjustment(difficultyFactors, "Reasoning", inputs.ReasoningDepth, ReasoningAdjustments, normalizedBaseDifficulty, ref difficulty);
        AddPercentAdjustment(difficultyFactors, "Domain complexity", inputs.DomainSpecificity, DomainAdjustments, normalizedBaseDifficulty, ref difficulty);
        AddPercentAdjustment(difficultyFactors, "Tool sequencing", inputs.ToolUse, ToolAdjustments, normalizedBaseDifficulty, ref difficulty);
        AddPercentAdjustment(difficultyFactors, "Verifiability", inputs.Verifiability, VerifiabilityAdjustments, normalizedBaseDifficulty, ref difficulty);
        AddPercentAdjustment(difficultyFactors, "Output format", inputs.OutputConstraint, OutputAdjustments, normalizedBaseDifficulty, ref difficulty);

        var categoryResidualPercent = GetCategoryResidualPercent(inputs, categoryProfile);
        ApplyPercentDelta(difficultyFactors, $"Task category: {categoryProfile.Name}", categoryResidualPercent, normalizedBaseDifficulty, ref difficulty, "category prior");

        if (inputs.RequiresStrictStructuredOutput)
        {
            var strictOutputPercent = inputs.HasDeterministicValidation
                ? StrictStructuredOutputWithValidationPercent
                : StrictStructuredOutputWithoutValidationPercent;

            ApplyPercentDelta(difficultyFactors, "Strict structured output", strictOutputPercent, normalizedBaseDifficulty, ref difficulty,
                inputs.HasDeterministicValidation ? "with validation" : "without validation");
        }

        if (inputs.HasSilentFailureRisk && inputs.Verifiability != VerifiabilityOption.HardToDetectWrongAnswers)
        {
            ApplyPercentDelta(difficultyFactors, "Silent-failure residual difficulty", SilentFailureResidualDifficultyPercent, normalizedBaseDifficulty, ref difficulty,
                "remaining difficulty after verifiability settings");
        }

        if (inputs.HasDeterministicValidation)
        {
            var validationDifficultyPercent = inputs.Verifiability == VerifiabilityOption.DeterministicallyTestable
                ? StrongDeterministicValidationDifficultyPercent
                : DeterministicValidationDifficultyPercent;

            ApplyPercentDelta(guardrailFactors, "Deterministic validation", validationDifficultyPercent, normalizedBaseDifficulty, ref difficulty,
                "difficulty benefit; larger effect is modeled through failure detection");
        }

        if (inputs.HasRagOrDomainContext)
        {
            var groundingAdjustment = GetGroundingAdjustmentPercent(inputs);
            ApplyPercentDelta(guardrailFactors, "Grounding or supplied domain context", groundingAdjustment, normalizedBaseDifficulty, ref difficulty,
                groundingAdjustment <= 0 ? "grounding benefit" : "noisy-context penalty");
        }

        if (inputs.CustomerFacing)
        {
            guardrailFactors.Add("Customer-facing output is modeled as exposure, not capability difficulty.");
        }

        if (inputs.HumanApprovalForHighRiskActions)
        {
            guardrailFactors.Add("Human approval reduces undetected critical-failure exposure, with a conservative default for review overreliance.");
        }

        if (inputs.HasRepresentativeEvalSet)
        {
            guardrailFactors.Add($"Representative eval set available: {inputs.EvalSetSize:n0} examples. This improves confidence in thresholds, but does not automatically improve model output.");
        }
        else
        {
            guardrailFactors.Add("No representative eval set selected. Treat the recommendation as a planning prior, not a production decision.");
        }

        var riskModel = BuildRiskModel(inputs, categoryProfile, guardrailFactors);
        ApplyCategoryAdjustments(inputs, categoryProfile, difficultyFactors, guardrailFactors, normalizedBaseDifficulty, ref difficulty);

        difficulty = Math.Clamp(difficulty, 0, 80);
        var tau = TauBySensitivity.TryGetValue(inputs.DifficultySensitivity, out var configuredTau) ? configuredTau : 5;
        var attempts = inputs.RetriesAllowed ? Math.Clamp(inputs.MaxAttempts, 1, 5) : 1;
        var targetSuccess = Math.Clamp(inputs.RequiredSuccessRate / 100d, 0, 1);
        var allowedCriticalFailure = Math.Clamp(inputs.AllowedCriticalFailureRate / 100d, 0, 1);

        var results = ModelCatalog.Models
            .Select(model => AnalyzeModel(model, inputs, difficulty, tau, attempts, targetSuccess, allowedCriticalFailure, riskModel))
            .OrderByDescending(x => x.IsEligible)
            .ThenByDescending(x => x.ExpectedValuePerTaskUsd)
            .ThenByDescending(x => x.EffectiveSuccessRate)
            .ThenBy(x => x.ExpectedTotalDirectCostUsd)
            .ToList();

        var eligible = results.Where(x => x.IsEligible).ToList();

        return new AnalysisSummary
        {
            EffectiveDifficulty = difficulty,
            Tau = tau,
            DifficultyFactors = difficultyFactors,
            GuardrailFactors = guardrailFactors,
            Results = results,
            EligibleResults = eligible,
            BestExpectedValue = eligible.OrderByDescending(x => x.ExpectedValuePerTaskUsd).FirstOrDefault(),
            CheapestEligible = eligible.OrderBy(x => x.ExpectedTotalDirectCostUsd).FirstOrDefault(),
            HighestQualityEligible = eligible.Where(x => x.ExpectedValuePerTaskUsd > 0).OrderByDescending(x => x.EffectiveSuccessRate).FirstOrDefault(),
            BestSuccessPerDollar = eligible.OrderByDescending(x => x.SuccessPerDollar).FirstOrDefault()
        };
    }

    private static RecommendationResult AnalyzeModel(
        ModelProfile model,
        UseCaseInputs inputs,
        double difficulty,
        double tau,
        int attempts,
        double targetSuccess,
        double allowedCriticalFailure,
        RiskModel riskModel)
    {
        var reasons = new List<string>();
        const double batchSize = TaskBatchSize;

        var singleAttemptSuccess = Sigmoid((model.AdjustedIntelligence - difficulty) / tau);
        var effectiveSuccess = EffectiveSuccessWithCorrelatedRetries(singleAttemptSuccess, attempts, riskModel.RetryCorrelation);
        effectiveSuccess = Math.Clamp(effectiveSuccess, 0.000001, 0.999999);

        var expectedAttempts = ExpectedAttemptsWithCorrelatedRetries(singleAttemptSuccess, attempts, riskModel.RetryCorrelation);
        var baseModelCost = model.CostPerAaTaskUsd.GetValueOrDefault() * inputs.CostMultiplier;
        var expectedModelCost = model.HasCostData ? baseModelCost * expectedAttempts * batchSize : double.NaN;
        var expectedReviewCost = Math.Max(0, inputs.HumanReviewCostUsd) * batchSize;
        var expectedRetryOverhead = Math.Max(0, expectedAttempts - 1) * Math.Max(0, inputs.OperationalRetryCostUsd) * batchSize;
        var expectedTotalDirectCost = model.HasCostData
            ? expectedModelCost + expectedReviewCost + expectedRetryOverhead
            : double.NaN;

        var failureProbability = 1 - effectiveSuccess;
        var criticalFailureShare = Math.Clamp(inputs.CriticalFailureShareOfFailures / 100d, 0, 1);
        var criticalFailureRate = Math.Clamp(
            failureProbability * criticalFailureShare * riskModel.CriticalFailureExposureMultiplier * riskModel.UndetectedCriticalFailureMultiplier,
            0,
            1);

        var expectedFailureCost = Math.Max(0, inputs.FailureCostUsd) * riskModel.FailureCostMultiplier * failureProbability * batchSize;
        var expectedSuccessValue = Math.Max(0, inputs.BusinessValuePerSuccessUsd) * effectiveSuccess * batchSize;
        var expectedValue = model.HasCostData
            ? expectedSuccessValue - expectedTotalDirectCost - expectedFailureCost
            : double.NaN;
        var monthlyExpectedValue = model.HasCostData ? expectedValue * Math.Max(0, inputs.MonthlyVolume) : double.NaN;

        var expectedSuccessfulTasks = Math.Max(effectiveSuccess * batchSize, 0.000001);
        var costPerSuccessfulTask = model.HasCostData ? expectedTotalDirectCost / expectedSuccessfulTasks : double.NaN;
        var successPerDollar = model.HasCostData ? expectedSuccessfulTasks / Math.Max(expectedTotalDirectCost, 0.000001) : 0;

        if (!model.HasCostData)
        {
            reasons.Add("No cost-per-task value was visible in the supplied cost chart.");
        }

        if (effectiveSuccess < targetSuccess)
        {
            reasons.Add($"Estimated success {effectiveSuccess:P1} is below required success {targetSuccess:P1}.");
        }

        if (criticalFailureRate > allowedCriticalFailure)
        {
            reasons.Add($"Estimated critical-failure rate {criticalFailureRate:P2} is above allowed rate {allowedCriticalFailure:P2}.");
        }

        var isEligible = reasons.Count == 0;
        var recommendationReason = BuildRecommendationReason(
            expectedValue,
            effectiveSuccess,
            expectedTotalDirectCost,
            costPerSuccessfulTask,
            criticalFailureRate,
            isEligible,
            reasons);

        return new RecommendationResult
        {
            Model = model,
            EffectiveDifficulty = difficulty,
            Tau = tau,
            SingleAttemptSuccessRate = singleAttemptSuccess,
            EffectiveSuccessRate = effectiveSuccess,
            CriticalFailureRate = criticalFailureRate,
            Attempts = attempts,
            ExpectedAttempts = expectedAttempts,
            ExpectedModelCostUsd = expectedModelCost,
            ExpectedReviewCostUsd = expectedReviewCost,
            ExpectedRetryOverheadUsd = expectedRetryOverhead,
            ExpectedTotalDirectCostUsd = expectedTotalDirectCost,
            CostPerSuccessfulTaskUsd = costPerSuccessfulTask,
            ExpectedValuePerTaskUsd = expectedValue,
            MonthlyExpectedValueUsd = monthlyExpectedValue,
            SuccessPerDollar = successPerDollar,
            IsEligible = isEligible,
            ExclusionReasons = reasons,
            RecommendationReason = recommendationReason
        };
    }

    private static RiskModel BuildRiskModel(UseCaseInputs inputs, TaskCategoryProfile profile, List<string> guardrailFactors)
    {
        var risk = RiskModel.Default;

        if (inputs.CustomerFacing)
        {
            risk = risk with
            {
                FailureCostMultiplier = risk.FailureCostMultiplier * 1.25,
                CriticalFailureExposureMultiplier = risk.CriticalFailureExposureMultiplier * 1.10
            };
            guardrailFactors.Add("Customer-facing exposure: failure cost x1.25; critical-failure exposure x1.10.");
        }

        if (inputs.OutputConstraint == OutputConstraintOption.ExternalFacingOrRegulatedArtifact)
        {
            risk = risk with
            {
                FailureCostMultiplier = risk.FailureCostMultiplier * 1.25,
                CriticalFailureExposureMultiplier = risk.CriticalFailureExposureMultiplier * 1.20
            };
            guardrailFactors.Add("External-facing or regulated artifact: failure cost x1.25; critical-failure exposure x1.20.");
        }

        if (inputs.DomainSpecificity == DomainSpecificityOption.ExpertOrRegulatedDomain)
        {
            risk = risk with
            {
                FailureCostMultiplier = risk.FailureCostMultiplier * 1.15,
                CriticalFailureExposureMultiplier = risk.CriticalFailureExposureMultiplier * 1.10
            };
            guardrailFactors.Add("Expert or regulated domain: failure cost x1.15; critical-failure exposure x1.10.");
        }

        if (inputs.HasSilentFailureRisk)
        {
            risk = risk with
            {
                FailureCostMultiplier = risk.FailureCostMultiplier * 1.10,
                CriticalFailureExposureMultiplier = risk.CriticalFailureExposureMultiplier * 1.20,
                UndetectedCriticalFailureMultiplier = risk.UndetectedCriticalFailureMultiplier * 1.15
            };
            guardrailFactors.Add("Silent-failure exposure: failure cost x1.10; critical-failure exposure x1.20; undetected critical failures x1.15.");
        }

        if (inputs.HasDeterministicValidation)
        {
            var detectionMultiplier = GetValidationDetectionMultiplier(inputs);
            risk = risk with
            {
                UndetectedCriticalFailureMultiplier = risk.UndetectedCriticalFailureMultiplier * detectionMultiplier
            };
            guardrailFactors.Add($"Deterministic validation detection: undetected critical failures x{detectionMultiplier:0.##}.");
        }

        if (inputs.HumanApprovalForHighRiskActions)
        {
            var humanReviewMultiplier = inputs is { HasDeterministicValidation: true, HasRepresentativeEvalSet: true }
                ? 0.65
                : 0.75;

            risk = risk with
            {
                UndetectedCriticalFailureMultiplier = risk.UndetectedCriticalFailureMultiplier * humanReviewMultiplier
            };
            guardrailFactors.Add($"Human approval detection: undetected critical failures x{humanReviewMultiplier:0.##}.");
        }

        if (inputs.ToolUse == ToolUseOption.AgenticWorkflowWithIrreversibleActions)
        {
            var hasApproval = inputs.HumanApprovalForHighRiskActions;
            risk = risk with
            {
                FailureCostMultiplier = risk.FailureCostMultiplier * (hasApproval ? 1.15 : 1.50),
                CriticalFailureExposureMultiplier = risk.CriticalFailureExposureMultiplier * (hasApproval ? 1.20 : 1.75),
                RetryCorrelation = Math.Max(risk.RetryCorrelation, hasApproval ? 0.60 : 0.70)
            };

            guardrailFactors.Add(hasApproval
                ? "Irreversible actions with approval: failure cost x1.15; critical-failure exposure x1.20."
                : "Irreversible actions without approval: failure cost x1.50; critical-failure exposure x1.75.");
        }

        if (!inputs.HasRepresentativeEvalSet && IsEvalSensitive(profile.Category))
        {
            risk = risk with
            {
                CriticalFailureExposureMultiplier = risk.CriticalFailureExposureMultiplier * 1.10
            };
            guardrailFactors.Add("Eval-sensitive category without a representative eval set: critical-failure exposure x1.10 because thresholds are less reliable.");
        }

        return risk;
    }

    private static double GetGroundingAdjustmentPercent(UseCaseInputs inputs)
    {
        return inputs.ContextRequirement switch
        {
            ContextRequirementOption.ShortClean => -6,
            ContextRequirementOption.MediumMostlyRelevant => -8,
            ContextRequirementOption.LargeClean => -10,
            ContextRequirementOption.LargeNoisy => -5,
            ContextRequirementOption.VeryLargeNoisyCrossDocument => 2,
            _ => -6
        };
    }

    private static double GetValidationDetectionMultiplier(UseCaseInputs inputs)
    {
        if (inputs.Verifiability == VerifiabilityOption.DeterministicallyTestable)
        {
            return inputs.RequiresStrictStructuredOutput ? 0.50 : 0.55;
        }

        if (inputs.OutputConstraint is OutputConstraintOption.StructuredJsonOrSchema or OutputConstraintOption.CodeSqlOrExecutableArtifact)
        {
            return 0.65;
        }

        return 0.75;
    }

    private static bool IsEvalSensitive(TaskCategoryOption category)
    {
        return category is TaskCategoryOption.ClassificationRouting
            or TaskCategoryOption.CodeGeneration
            or TaskCategoryOption.AgenticWorkflow
            or TaskCategoryOption.ResearchAnalysis;
    }

    private static void AddPercentAdjustment<TEnum>(
        List<string> notes,
        string label,
        TEnum selected,
        IReadOnlyDictionary<TEnum, double> adjustments,
        double baseDifficulty,
        ref double difficulty)
        where TEnum : struct, Enum
    {
        if (!adjustments.TryGetValue(selected, out var adjustment))
        {
            return;
        }

        var delta = baseDifficulty * (adjustment / 100d);
        difficulty += delta;
        notes.Add($"{label}: {selected.DisplayName()} ({adjustment:+0.##;-0.##;0}% of base = {delta:+0.0;-0.0;0.0})");
    }

    private static double ApplyPercentDelta(List<string> notes, string label, double percent, double baseDifficulty, ref double difficulty, string? suffix = null)
    {
        var delta = baseDifficulty * (percent / 100d);
        difficulty += delta;
        var details = $"{percent:+0.##;-0.##;0}% of base = {delta:+0.0;-0.0;0.0}";
        notes.Add(string.IsNullOrWhiteSpace(suffix)
            ? $"{label}: {details}"
            : $"{label}: {details} {suffix}");
        return delta;
    }

    public static bool TryGetTaskCategoryProfile(TaskCategoryOption category, out TaskCategoryProfile profile)
    {
        if (TaskCategoryProfiles.TryGetValue(category, out profile!))
        {
            return true;
        }

        profile = TaskCategoryProfiles[TaskCategoryOption.Other];
        return false;
    }

    public static TaskCategoryProfile ResolveTaskCategoryProfile(TaskCategoryOption category)
    {
        TryGetTaskCategoryProfile(category, out var profile);
        return profile;
    }

    private static double GetCategoryResidualPercent(UseCaseInputs inputs, TaskCategoryProfile profile)
    {
        if (profile.Category == TaskCategoryOption.CodeGeneration)
        {
            if (inputs.HasDeterministicValidation && inputs.RetriesAllowed)
            {
                return -2;
            }

            if (inputs.HasDeterministicValidation)
            {
                return 0;
            }
        }

        if (profile.Category == TaskCategoryOption.AgenticWorkflow && inputs.ToolUse == ToolUseOption.AgenticWorkflowWithIrreversibleActions)
        {
            return profile.BaseDifficultyPercentResidual + 2;
        }

        return profile.BaseDifficultyPercentResidual;
    }

    private static double NormalizeBaseDifficulty(double selectedBaseDifficulty, TaskCategoryProfile profile, out string factor)
    {
        if (profile.DefaultBaseDifficulty is not { } categoryBaseline)
        {
            factor = $"Base difficulty: {selectedBaseDifficulty:0.0}";
            return selectedBaseDifficulty;
        }

        var overrideDelta = selectedBaseDifficulty - categoryBaseline;
        var normalizedBaseDifficulty = categoryBaseline + (overrideDelta * 0.35);

        if (Math.Abs(overrideDelta) < 0.05)
        {
            factor = $"Base difficulty: {categoryBaseline:0.0} category baseline";
            return normalizedBaseDifficulty;
        }

        factor = $"Base difficulty: {normalizedBaseDifficulty:0.0} ({selectedBaseDifficulty:0.0} selected, {categoryBaseline:0.0} category baseline)";
        return normalizedBaseDifficulty;
    }

    private static void ApplyCategoryAdjustments(
        UseCaseInputs inputs,
        TaskCategoryProfile profile,
        List<string> difficultyFactors,
        List<string> guardrailFactors,
        double baseDifficulty,
        ref double difficulty)
    {
        switch (profile.Category)
        {
            case TaskCategoryOption.Extraction:
                if (inputs.OutputConstraint == OutputConstraintOption.FreeText)
                {
                    guardrailFactors.Add("Extraction usually works best with structured output. Free text weakens validation and makes this category selection less representative.");
                }
                break;

            case TaskCategoryOption.ClassificationRouting:
                if (!inputs.HasRepresentativeEvalSet)
                {
                    guardrailFactors.Add("Classification/routing tasks should usually be evaluated against labeled examples. Without an eval set, the required success threshold is speculative.");
                }
                break;

            case TaskCategoryOption.Summarization:
                if (!inputs.HasSilentFailureRisk)
                {
                    guardrailFactors.Add("Summaries often fail by omission or subtle distortion. Consider enabling silent-failure risk if factual drift would matter.");
                }
                break;

            case TaskCategoryOption.CodeGeneration:
                if (inputs.OutputConstraint == OutputConstraintOption.FreeText)
                {
                    guardrailFactors.Add("Code generation is usually better modeled as code or executable output than free text.");
                }
                break;

            case TaskCategoryOption.ResearchAnalysis:
                if (!inputs.HasRagOrDomainContext)
                {
                    guardrailFactors.Add("Research/analysis without grounding raises synthesis and hallucination risk. Prefer supplied domain context or RAG when possible.");

                    if (inputs.Verifiability != VerifiabilityOption.HardToDetectWrongAnswers)
                    {
                        ApplyPercentDelta(difficultyFactors, "Research without grounding", ResearchWithoutGroundingPercent, baseDifficulty, ref difficulty);
                    }
                }
                break;

            case TaskCategoryOption.AgenticWorkflow:
                if (ToolAdjustments.TryGetValue(inputs.ToolUse, out var toolAdjustment) && toolAdjustment < ToolAdjustments[ToolUseOption.MultipleToolsWithValidation])
                {
                    guardrailFactors.Add("Agentic workflow is mismatched with the selected tool-use level. Recheck whether this task really involves multi-step tool orchestration.");
                }
                break;
        }
    }

    private static double Sigmoid(double x) => 1d / (1d + Math.Exp(-x));

    private static double EffectiveSuccessWithCorrelatedRetries(double singleAttemptSuccess, int maxAttempts, double retryCorrelation)
    {
        singleAttemptSuccess = Math.Clamp(singleAttemptSuccess, 0.000001, 0.999999);
        retryCorrelation = Math.Clamp(retryCorrelation, 0, 0.95);

        var failureProbability = 1d;

        for (var attempt = 1; attempt <= maxAttempts; attempt++)
        {
            var marginalSuccess = singleAttemptSuccess * RetryBenefit(attempt, retryCorrelation);
            marginalSuccess = Math.Clamp(marginalSuccess, 0.000001, 0.999999);
            failureProbability *= 1 - marginalSuccess;
        }

        return 1 - failureProbability;
    }

    private static double ExpectedAttemptsWithCorrelatedRetries(double singleAttemptSuccess, int maxAttempts, double retryCorrelation)
    {
        singleAttemptSuccess = Math.Clamp(singleAttemptSuccess, 0.000001, 0.999999);
        retryCorrelation = Math.Clamp(retryCorrelation, 0, 0.95);

        var expectedAttempts = 0d;
        var reachAttemptProbability = 1d;

        for (var attempt = 1; attempt <= maxAttempts; attempt++)
        {
            expectedAttempts += reachAttemptProbability;
            var marginalSuccess = singleAttemptSuccess * RetryBenefit(attempt, retryCorrelation);
            marginalSuccess = Math.Clamp(marginalSuccess, 0.000001, 0.999999);
            reachAttemptProbability *= 1 - marginalSuccess;
        }

        return expectedAttempts;
    }

    private static double RetryBenefit(int attemptNumber, double retryCorrelation)
    {
        if (attemptNumber <= 1)
        {
            return 1;
        }

        var independentRetryBenefit = attemptNumber switch
        {
            2 => 0.60,
            3 => 0.35,
            4 => 0.20,
            _ => 0.10
        };

        return independentRetryBenefit * (1 - retryCorrelation);
    }

    private static string BuildRecommendationReason(
        double expectedValue,
        double effectiveSuccess,
        double expectedTotalDirectCost,
        double costPerSuccessfulTask,
        double criticalFailureRate,
        bool isEligible,
        IReadOnlyList<string> reasons)
    {
        if (!isEligible)
        {
            return string.Join(" ", reasons);
        }

        return $"Meets the hard constraints with {effectiveSuccess:P1} estimated success, {criticalFailureRate:P2} estimated critical-failure rate, {FormatCurrency(expectedTotalDirectCost)} expected direct cost per {TaskBatchSize:n0} tasks, {FormatCurrency(costPerSuccessfulTask)} cost per successful task, and {FormatCurrency(expectedValue)} expected value per {TaskBatchSize:n0} tasks.";
    }

    public static string FormatCurrency(double value)
    {
        if (double.IsNaN(value) || double.IsInfinity(value))
        {
            return "n/a";
        }

        return value switch
        {
            >= 1000 or <= -1000 => value.ToString("$#,0", System.Globalization.CultureInfo.CurrentCulture),
            >= 100 or <= -100 => value.ToString("$#,0.0", System.Globalization.CultureInfo.CurrentCulture),
            _ => value.ToString("$0.00", System.Globalization.CultureInfo.CurrentCulture)
        };
    }

    private sealed record RiskModel(
        double FailureCostMultiplier,
        double CriticalFailureExposureMultiplier,
        double UndetectedCriticalFailureMultiplier,
        double RetryCorrelation)
    {
        public static RiskModel Default { get; } = new(
            FailureCostMultiplier: 1,
            CriticalFailureExposureMultiplier: 1,
            UndetectedCriticalFailureMultiplier: 1,
            RetryCorrelation: 0.30);
    }
}
