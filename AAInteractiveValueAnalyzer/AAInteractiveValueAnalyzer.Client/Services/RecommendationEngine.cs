using AAInteractiveValueAnalyzer.Client.Models;

namespace AAInteractiveValueAnalyzer.Client.Services;

public static class RecommendationEngine
{
    public const int TaskBatchSize = 1000;
    public const double StrictStructuredOutputPercent = 12;
    public const double CustomerFacingPercent = 8;
    public const double SilentFailureRiskPercent = 20;
    public const double DeterministicValidationPercent = -12;
    public const double RagOrDomainContextPercent = -12;
    public const double ResearchWithoutGroundingPercent = 4;

    public static IReadOnlyDictionary<ContextRequirementOption, double> ContextAdjustments { get; } = new Dictionary<ContextRequirementOption, double>
    {
        [ContextRequirementOption.ShortClean] = 0,
        [ContextRequirementOption.MediumMostlyRelevant] = 5,
        [ContextRequirementOption.LargeClean] = 10,
        [ContextRequirementOption.LargeNoisy] = 15,
        [ContextRequirementOption.VeryLargeNoisyCrossDocument] = 20
    };

    public static IReadOnlyDictionary<ReasoningDepthOption, double> ReasoningAdjustments { get; } = new Dictionary<ReasoningDepthOption, double>
    {
        [ReasoningDepthOption.SingleStepTransformation] = 0,
        [ReasoningDepthOption.Light] = 5,
        [ReasoningDepthOption.ModerateMultiStep] = 10,
        [ReasoningDepthOption.DeepConditional] = 18,
        [ReasoningDepthOption.ResearchGradeSynthesisPlanning] = 25
    };

    public static IReadOnlyDictionary<DomainSpecificityOption, double> DomainAdjustments { get; } = new Dictionary<DomainSpecificityOption, double>
    {
        [DomainSpecificityOption.GeneralKnowledge] = 0,
        [DomainSpecificityOption.SomeDomainSpecificTerminology] = 5,
        [DomainSpecificityOption.SpecializedProfessionalDomain] = 13,
        [DomainSpecificityOption.ExpertOrRegulatedDomain] = 20
    };

    public static IReadOnlyDictionary<ToolUseOption, double> ToolAdjustments { get; } = new Dictionary<ToolUseOption, double>
    {
        [ToolUseOption.None] = 0,
        [ToolUseOption.OneOrTwoDeterministicTools] = 5,
        [ToolUseOption.MultipleToolsWithValidation] = 13,
        [ToolUseOption.AutonomousToolSequence] = 20,
        [ToolUseOption.AgenticWorkflowWithIrreversibleActions] = 28
    };

    public static IReadOnlyDictionary<VerifiabilityOption, double> VerifiabilityAdjustments { get; } = new Dictionary<VerifiabilityOption, double>
    {
        [VerifiabilityOption.DeterministicallyTestable] = 0,
        [VerifiabilityOption.MostlyVerifiableByReviewer] = 5,
        [VerifiabilityOption.PartlySubjective] = 13,
        [VerifiabilityOption.HardToDetectWrongAnswers] = 20
    };

    public static IReadOnlyDictionary<OutputConstraintOption, double> OutputAdjustments { get; } = new Dictionary<OutputConstraintOption, double>
    {
        [OutputConstraintOption.FreeText] = 0,
        [OutputConstraintOption.StructuredJsonOrSchema] = 5,
        [OutputConstraintOption.CodeSqlOrExecutableArtifact] = 13,
        [OutputConstraintOption.ExternalFacingOrRegulatedArtifact] = 18
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
                BaseDifficultyPercentResidual: -4,
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
                BaseDifficultyPercentResidual: -4,
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
                DefaultBaseDifficulty: 15,
                DefaultContextRequirement: ContextRequirementOption.LargeClean,
                DefaultReasoningDepth: ReasoningDepthOption.ModerateMultiStep,
                DefaultToolUse: ToolUseOption.None,
                DefaultVerifiability: VerifiabilityOption.MostlyVerifiableByReviewer,
                DefaultOutputConstraint: OutputConstraintOption.FreeText,
                DefaultHasSilentFailureRisk: true),
            [TaskCategoryOption.CodeGeneration] = new(
                Category: TaskCategoryOption.CodeGeneration,
                BaseDifficultyPercentResidual: 4,
                DefaultBaseDifficulty: 25,
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
                BaseDifficultyPercentResidual: 12,
                DefaultBaseDifficulty: 35,
                DefaultContextRequirement: ContextRequirementOption.LargeClean,
                DefaultReasoningDepth: ReasoningDepthOption.DeepConditional,
                DefaultToolUse: ToolUseOption.MultipleToolsWithValidation,
                DefaultVerifiability: VerifiabilityOption.MostlyVerifiableByReviewer,
                DefaultOutputConstraint: OutputConstraintOption.StructuredJsonOrSchema,
                DefaultHasRagOrDomainContext: true,
                DefaultHasSilentFailureRisk: true),
            [TaskCategoryOption.DraftingWriting] = new(
                Category: TaskCategoryOption.DraftingWriting,
                BaseDifficultyPercentResidual: 4,
                DefaultBaseDifficulty: 17,
                DefaultContextRequirement: ContextRequirementOption.MediumMostlyRelevant,
                DefaultReasoningDepth: ReasoningDepthOption.ModerateMultiStep,
                DefaultToolUse: ToolUseOption.None,
                DefaultVerifiability: VerifiabilityOption.PartlySubjective,
                DefaultOutputConstraint: OutputConstraintOption.FreeText,
                DefaultHasSilentFailureRisk: true),
            [TaskCategoryOption.ResearchAnalysis] = new(
                Category: TaskCategoryOption.ResearchAnalysis,
                BaseDifficultyPercentResidual: 8,
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
        var criticalFailureExposureMultiplier = 1d;

        var difficulty = NormalizeBaseDifficulty(inputs.BaseDifficulty, categoryProfile, out var baseDifficultyFactor);
        var normalizedBaseDifficulty = difficulty;
        difficultyFactors.Add(baseDifficultyFactor);

        AddPercentAdjustment(difficultyFactors, "Context", inputs.ContextRequirement, ContextAdjustments, normalizedBaseDifficulty, ref difficulty);
        AddPercentAdjustment(difficultyFactors, "Reasoning", inputs.ReasoningDepth, ReasoningAdjustments, normalizedBaseDifficulty, ref difficulty);
        AddPercentAdjustment(difficultyFactors, "Domain", inputs.DomainSpecificity, DomainAdjustments, normalizedBaseDifficulty, ref difficulty);
        AddPercentAdjustment(difficultyFactors, "Tool use", inputs.ToolUse, ToolAdjustments, normalizedBaseDifficulty, ref difficulty);
        AddPercentAdjustment(difficultyFactors, "Verifiability", inputs.Verifiability, VerifiabilityAdjustments, normalizedBaseDifficulty, ref difficulty);
        AddPercentAdjustment(difficultyFactors, "Output", inputs.OutputConstraint, OutputAdjustments, normalizedBaseDifficulty, ref difficulty);

        var categoryResidualPercent = GetCategoryResidualPercent(inputs, categoryProfile);
        ApplyPercentDelta(difficultyFactors, $"Task category: {categoryProfile.Name}", categoryResidualPercent, normalizedBaseDifficulty, ref difficulty, "category prior");

        if (inputs.RequiresStrictStructuredOutput)
        {
            ApplyPercentDelta(difficultyFactors, "Strict structured output", StrictStructuredOutputPercent, normalizedBaseDifficulty, ref difficulty);
        }

        if (inputs.CustomerFacing)
        {
            ApplyPercentDelta(difficultyFactors, "Customer-facing output", CustomerFacingPercent, normalizedBaseDifficulty, ref difficulty);
        }

        if (inputs.HasSilentFailureRisk)
        {
            ApplyPercentDelta(difficultyFactors, "Silent failure risk", SilentFailureRiskPercent, normalizedBaseDifficulty, ref difficulty);
        }

        if (inputs.HasDeterministicValidation)
        {
            ApplyPercentDelta(guardrailFactors, "Deterministic validation", DeterministicValidationPercent, normalizedBaseDifficulty, ref difficulty);
            guardrailFactors[^1] += " and reduces critical-failure exposure.";
        }

        if (inputs.HasRagOrDomainContext)
        {
            ApplyPercentDelta(guardrailFactors, "RAG or supplied domain context", RagOrDomainContextPercent, normalizedBaseDifficulty, ref difficulty);
        }

        if (inputs.HumanApprovalForHighRiskActions)
        {
            guardrailFactors.Add("Human approval reduces modeled critical-failure exposure for high-risk actions.");
        }

        if (inputs.HasRepresentativeEvalSet)
        {
            guardrailFactors.Add($"Representative eval set available: {inputs.EvalSetSize:n0} examples.");
        }
        else
        {
            guardrailFactors.Add("No representative eval set selected. Treat the recommendation as a planning prior, not a production decision.");
        }

        ApplyCategoryAdjustments(inputs, categoryProfile, difficultyFactors, guardrailFactors, normalizedBaseDifficulty, ref difficulty, ref criticalFailureExposureMultiplier);

        difficulty = Math.Clamp(difficulty, 0, 75);
        var tau = TauBySensitivity.TryGetValue(inputs.DifficultySensitivity, out var configuredTau) ? configuredTau : 5;
        var attempts = inputs.RetriesAllowed ? Math.Clamp(inputs.MaxAttempts, 1, 5) : 1;
        var targetSuccess = inputs.RequiredSuccessRate / 100d;
        var allowedCriticalFailure = inputs.AllowedCriticalFailureRate / 100d;

        var results = ModelCatalog.Models
            .Select(model => AnalyzeModel(model, inputs, difficulty, tau, attempts, targetSuccess, allowedCriticalFailure, criticalFailureExposureMultiplier))
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
        double criticalFailureExposureMultiplier)
    {
        var reasons = new List<string>();
        const double batchSize = TaskBatchSize;
        var singleAttemptSuccess = Sigmoid((model.AdjustedIntelligence - difficulty) / tau);
        var effectiveSuccess = 1 - Math.Pow(1 - singleAttemptSuccess, attempts);
        effectiveSuccess = Math.Clamp(effectiveSuccess, 0.000001, 0.999999);

        var expectedAttempts = ExpectedAttempts(singleAttemptSuccess, attempts);
        var baseModelCost = model.CostPerAaTaskUsd.GetValueOrDefault() * inputs.CostMultiplier;
        var expectedModelCost = model.HasCostData ? baseModelCost * expectedAttempts * batchSize : double.NaN;
        var expectedReviewCost = Math.Max(0, inputs.HumanReviewCostUsd) * batchSize;
        var expectedRetryOverhead = Math.Max(0, expectedAttempts - 1) * Math.Max(0, inputs.OperationalRetryCostUsd) * batchSize;
        var expectedTotalDirectCost = model.HasCostData
            ? expectedModelCost + expectedReviewCost + expectedRetryOverhead
            : double.NaN;

        var criticalFailureShare = Math.Clamp(inputs.CriticalFailureShareOfFailures / 100d, 0, 1);
        if (inputs.HasSilentFailureRisk)
        {
            criticalFailureShare = Math.Min(1, criticalFailureShare * 1.5);
        }

        var criticalFailureRate = (1 - effectiveSuccess) * criticalFailureShare * criticalFailureExposureMultiplier;

        if (inputs.HasDeterministicValidation)
        {
            criticalFailureRate *= 0.45;
        }

        if (inputs.HumanApprovalForHighRiskActions)
        {
            criticalFailureRate *= 0.5;
        }

        var costPerSuccessfulTask = model.HasCostData ? expectedTotalDirectCost / effectiveSuccess : double.NaN;
        var successPerDollar = model.HasCostData ? effectiveSuccess / Math.Max(expectedTotalDirectCost, 0.000001) * batchSize : 0;
        var expectedValue = model.HasCostData
            ? inputs.BusinessValuePerSuccessUsd * effectiveSuccess * batchSize
              - expectedTotalDirectCost
              - inputs.FailureCostUsd * (1 - effectiveSuccess) * batchSize
            : double.NaN;
        var monthlyExpectedValue = model.HasCostData ? expectedValue * Math.Max(0, inputs.MonthlyVolume) : double.NaN;

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
        var recommendationReason = BuildRecommendationReason(model, expectedValue, effectiveSuccess, expectedTotalDirectCost, costPerSuccessfulTask, criticalFailureRate, isEligible, reasons);

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
                return -4;
            }

            if (inputs.HasDeterministicValidation)
            {
                return 0;
            }
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
        ref double difficulty,
        ref double criticalFailureExposureMultiplier)
    {
        switch (profile.Category)
        {
            case TaskCategoryOption.Extraction:
                if (inputs.RequiresStrictStructuredOutput && inputs.HasDeterministicValidation)
                {
                    criticalFailureExposureMultiplier *= 0.85;
                    guardrailFactors.Add("Extraction with strict schema output and deterministic validation slightly reduces modeled critical-failure exposure.");
                }

                if (inputs.OutputConstraint == OutputConstraintOption.FreeText)
                {
                    guardrailFactors.Add("Extraction usually works best with structured output. Free text output weakens validation and makes this category selection less representative.");
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

                if (inputs is { ToolUse: ToolUseOption.AgenticWorkflowWithIrreversibleActions, HumanApprovalForHighRiskActions: false })
                {
                    criticalFailureExposureMultiplier *= 1.35;
                    guardrailFactors.Add("Irreversible agentic actions without human approval materially increase modeled critical-failure exposure.");
                }

                break;
        }
    }

    private static double Sigmoid(double x) => 1d / (1d + Math.Exp(-x));

    private static double ExpectedAttempts(double singleAttemptSuccess, int maxAttempts)
    {
        singleAttemptSuccess = Math.Clamp(singleAttemptSuccess, 0.000001, 0.999999);
        var failure = 1 - singleAttemptSuccess;
        return (1 - Math.Pow(failure, maxAttempts)) / singleAttemptSuccess;
    }

    private static string BuildRecommendationReason(
        ModelProfile model,
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

        return $"Meets the hard constraints with {effectiveSuccess:P1} estimated success, {criticalFailureRate:P2} estimated critical-failure rate, {FormatCurrency(expectedTotalDirectCost)} expected direct cost per {TaskBatchSize:n0} tasks, {FormatCurrency(costPerSuccessfulTask)} cost per {TaskBatchSize:n0} successful tasks, and {FormatCurrency(expectedValue)} expected value per {TaskBatchSize:n0} tasks.";
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
}
