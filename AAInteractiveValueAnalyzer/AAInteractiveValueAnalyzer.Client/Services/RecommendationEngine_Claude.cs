using AAInteractiveValueAnalyzer.Client.Models;

namespace AAInteractiveValueAnalyzer.Client.Services;

/// <summary>
/// Calculates task difficulty, guardrails, and model recommendations for a supplied use case.
/// </summary>
/// <remarks>
/// CALIBRATION NOTE. Two scales meet inside the success model: the Artificial Analysis
/// Intelligence Index (per model) and the modeled task difficulty (built up from the
/// adjustment tables below). They are assumed commensurable on a shared 0-based axis after
/// <see cref="AdjustedIntelligence(double)"/> is applied. That assumption is a *prior*, not a
/// measurement. Until it is fitted against an eval set, every ranking this engine produces is a
/// planning estimate. The constants most responsible for the shape of the output, and therefore
/// the ones to fit first, are <see cref="IntelligenceCurve"/> and <see cref="TauBySensitivity"/>.
/// </remarks>
public static class RecommendationEngine_Claude
{
    /// <summary>
    /// The analysis is normalized around task batches of this size when estimating cost, value, and throughput.
    /// </summary>
    public const int TaskBatchSize = 1000;

    /// <summary>
    /// Additional modeled difficulty applied when a task requires strict structured output.
    /// </summary>
    public const double StrictStructuredOutputPercent = 5;

    /// <summary>
    /// Additional modeled difficulty applied when the output is customer-facing.
    /// </summary>
    public const double CustomerFacingPercent = 8;

    /// <summary>
    /// Additional modeled difficulty applied when silent failures would be especially costly.
    /// </summary>
    /// <remarks>
    /// CHANGED. Silent-failure risk now expresses itself on a single channel. Previously it both
    /// raised difficulty by 20% of base AND multiplied the critical-failure share by 1.5, charging
    /// the same checkbox twice. Silent failure is fundamentally a *detection* problem, not a
    /// *capability* problem: it does not make the task harder to do, it makes failures harder to
    /// catch. So the entire effect now lives on the critical-failure path
    /// (<see cref="SilentFailureCriticalShareMultiplier"/>), and the difficulty contribution is 0.
    /// Kept as a named constant so a future fit can reintroduce a small capability term if the
    /// data justifies it.
    /// </remarks>
    public const double SilentFailureRiskPercent = 0;

    /// <summary>
    /// Multiplier applied to the critical-failure share of failures when silent failures are likely.
    /// This is the sole channel for silent-failure risk.
    /// </summary>
    public const double SilentFailureCriticalShareMultiplier = 1.5;

    /// <summary>
    /// Difficulty reduction applied when deterministic validation is available.
    /// </summary>
    /// <remarks>
    /// CHANGED. Deterministic validation now affects exactly one thing: the critical-failure rate
    /// (see <see cref="DeterministicValidationCriticalMultiplier"/>). A validator does not make the
    /// model smarter, so it should not move the success curve. Previously this single checkbox
    /// reduced difficulty by 12%, cut critical failures by 55% (x0.45), cut Extraction exposure by
    /// a further 15%, and flipped the code-gen residual from +4 to -4 -- four credits for one
    /// control. The difficulty credit is now 0; the over-aggressive x0.45 is relaxed to x0.65
    /// (validators catch schema/syntax failures, not semantic ones); and the code-gen residual no
    /// longer special-cases this flag. The Extraction exposure interaction is retained because it
    /// is conditioned on strict schema output as well, i.e. a genuinely different signal.
    /// </remarks>
    public const double DeterministicValidationPercent = 0;

    /// <summary>
    /// Multiplier applied to the critical-failure rate when deterministic validation is present.
    /// Validators catch deterministic (schema/syntax) failures but miss semantic ones, so this is a
    /// partial cut, not a halving.
    /// </summary>
    public const double DeterministicValidationCriticalMultiplier = 0.65;

    /// <summary>
    /// Multiplier applied to the critical-failure rate when high-risk actions require human approval.
    /// </summary>
    public const double HumanApprovalCriticalMultiplier = 0.5;

    /// <summary>
    /// Difficulty reduction applied when grounded RAG or domain context is provided.
    /// RAG genuinely changes how hard the task is (it supplies the answer substrate), so unlike the
    /// two flags above this legitimately belongs on the difficulty channel.
    /// </summary>
    public const double RagOrDomainContextPercent = -6;

    /// <summary>
    /// Additional difficulty applied to research tasks that lack grounding.
    /// </summary>
    public const double ResearchWithoutGroundingPercent = 4;

    /// <summary>
    /// Fraction of a user's base-difficulty override that is honored against the category baseline.
    /// </summary>
    /// <remarks>
    /// CHANGED. Was a silent 0.35, which overrode an explicit user setting by 65%. Raised to 0.6 so
    /// a deliberate override is mostly respected while the category prior still anchors. Surfaced as
    /// a named constant so the UI can disclose the damping rather than hiding it.
    /// </remarks>
    public const double BaseDifficultyOverrideWeight = 0.6;

    /// <summary>
    /// Configurable convex transform applied to the raw intelligence index before it is compared to
    /// task difficulty. The transform is deliberately convex: a gap near the top of the index
    /// (e.g. 53 -> 56) reflects a larger real capability difference than the same nominal gap near
    /// the bottom (e.g. 23 -> 26), so per-point value rises with the index.
    /// </summary>
    /// <remarks>
    /// CHANGED. The breakpoints (20, 40) and slopes (1, 2, 3) were hard-coded inside
    /// ModelProfile.AdjustedIntelligence. They are now data here so they can be tuned -- ideally
    /// fitted -- without touching the model type. The piecewise shape is retained per design choice.
    /// Caveat preserved from review: piecewise-linear introduces slope *kinks* at each breakpoint,
    /// so two models straddling a breakpoint separate slightly faster than their index gap warrants.
    /// If that artifact ever matters, swap <see cref="AdjustedIntelligence(double)"/> for a smooth
    /// power curve with the same endpoints; the call sites do not change.
    /// </remarks>
    public static IntelligenceCurveConfig IntelligenceCurve { get; } = IntelligenceCurveConfig.Default;

    /// <summary>
    /// Configuration for the piecewise-linear intelligence transform.
    /// Segments must be supplied in ascending order of <see cref="Segment.UpperBoundInclusive"/>.
    /// </summary>
    public sealed record IntelligenceCurveConfig(IReadOnlyList<IntelligenceCurveConfig.Segment> Segments)
    {
        /// <param name="UpperBoundInclusive">Highest raw index this segment covers, inclusive. Use <see cref="double.PositiveInfinity"/> for the final segment.</param>
        /// <param name="Slope">Adjusted points produced per raw index point within this segment.</param>
        public readonly record struct Segment(double UpperBoundInclusive, double Slope);

        /// <summary>
        /// Default curve: identity below 20, 2x from 20-40, 3x above 40. Matches the original
        /// hard-coded transform exactly, so swapping to the configurable path changes no numbers.
        /// </summary>
        public static IntelligenceCurveConfig Default { get; } = new(
        [
            new Segment(20, 1),
            new Segment(40, 2),
            new Segment(double.PositiveInfinity, 3)
        ]);
    }

    /// <summary>
    /// Applies the configured convex transform to a raw intelligence index. This is the engine-side
    /// source of truth for the curve; <c>ModelProfile.AdjustedIntelligence</c> may delegate here or
    /// be removed.
    /// </summary>
    /// <param name="rawIndex">The raw Artificial Analysis Intelligence Index value.</param>
    /// <returns>The convex-adjusted intelligence used in the success model.</returns>
    public static double AdjustedIntelligence(double rawIndex)
    {
        var adjusted = 0d;
        var lowerBound = 0d;

        foreach (var segment in IntelligenceCurve.Segments)
        {
            if (rawIndex <= lowerBound)
            {
                break;
            }

            var segmentTop = Math.Min(rawIndex, segment.UpperBoundInclusive);
            adjusted += (segmentTop - lowerBound) * segment.Slope;
            lowerBound = segment.UpperBoundInclusive;
        }

        return adjusted;
    }

    /// <summary>
    /// Adjustments are expressed as percent of the base difficulty, so they scale with the inherent
    /// difficulty of the task. A 10% context adjustment adds 1 point at base difficulty 10, but 5
    /// points at base difficulty 50.
    /// </summary>
    public static IReadOnlyDictionary<ContextRequirementOption, double> ContextAdjustments { get; } = new Dictionary<ContextRequirementOption, double>
    {
        [ContextRequirementOption.ShortClean] = 0,
        [ContextRequirementOption.MediumMostlyRelevant] = 5,
        [ContextRequirementOption.LargeClean] = 10,
        [ContextRequirementOption.LargeNoisy] = 15,
        [ContextRequirementOption.VeryLargeNoisyCrossDocument] = 20
    };

    /// <summary>
    /// Percent adjustments applied for the selected reasoning depth.
    /// </summary>
    public static IReadOnlyDictionary<ReasoningDepthOption, double> ReasoningAdjustments { get; } = new Dictionary<ReasoningDepthOption, double>
    {
        [ReasoningDepthOption.SingleStepTransformation] = 0,
        [ReasoningDepthOption.Light] = 5,
        [ReasoningDepthOption.ModerateMultiStep] = 10,
        [ReasoningDepthOption.DeepConditional] = 18,
        [ReasoningDepthOption.ResearchGradeSynthesisPlanning] = 25
    };

    /// <summary>
    /// Percent adjustments applied for the selected domain specificity.
    /// </summary>
    public static IReadOnlyDictionary<DomainSpecificityOption, double> DomainAdjustments { get; } = new Dictionary<DomainSpecificityOption, double>
    {
        [DomainSpecificityOption.GeneralKnowledge] = 0,
        [DomainSpecificityOption.SomeDomainSpecificTerminology] = 5,
        [DomainSpecificityOption.SpecializedProfessionalDomain] = 13,
        [DomainSpecificityOption.ExpertOrRegulatedDomain] = 20
    };

    /// <summary>
    /// Percent adjustments applied for the selected level of tool use.
    /// </summary>
    public static IReadOnlyDictionary<ToolUseOption, double> ToolAdjustments { get; } = new Dictionary<ToolUseOption, double>
    {
        [ToolUseOption.None] = 0,
        [ToolUseOption.OneOrTwoDeterministicTools] = 5,
        [ToolUseOption.MultipleToolsWithValidation] = 13,
        [ToolUseOption.AutonomousToolSequence] = 20,
        [ToolUseOption.AgenticWorkflowWithIrreversibleActions] = 28
    };

    /// <summary>
    /// Percent adjustments applied for the selected verifiability level.
    /// </summary>
    public static IReadOnlyDictionary<VerifiabilityOption, double> VerifiabilityAdjustments { get; } = new Dictionary<VerifiabilityOption, double>
    {
        [VerifiabilityOption.DeterministicallyTestable] = 0,
        [VerifiabilityOption.MostlyVerifiableByReviewer] = 5,
        [VerifiabilityOption.PartlySubjective] = 13,
        [VerifiabilityOption.HardToDetectWrongAnswers] = 20
    };

    /// <summary>
    /// Percent adjustments applied for the selected output constraint.
    /// </summary>
    public static IReadOnlyDictionary<OutputConstraintOption, double> OutputAdjustments { get; } = new Dictionary<OutputConstraintOption, double>
    {
        [OutputConstraintOption.FreeText] = 0,
        [OutputConstraintOption.StructuredJsonOrSchema] = 5,
        [OutputConstraintOption.CodeSqlOrExecutableArtifact] = 13,
        [OutputConstraintOption.ExternalFacingOrRegulatedArtifact] = 18
    };

    /// <summary>
    /// All supported task categories in display order.
    /// </summary>
    public static IReadOnlyList<TaskCategoryOption> TaskCategories { get; } = AnalyzerOptionDisplay.Values<TaskCategoryOption>();

    /// <summary>
    /// The sigmoid slope used for each difficulty sensitivity setting.
    /// </summary>
    /// <remarks>
    /// NOTE. tau is expressed in *adjusted* intelligence units. Because the convex transform makes
    /// adjusted units denser at the top (3x slope), a fixed tau is effectively sharper among
    /// frontier models than among weak ones. <see cref="EffectiveTau"/> compensates by scaling tau
    /// by the local slope at the task difficulty, so "Soft" stays soft across the whole range.
    /// </remarks>
    public static IReadOnlyDictionary<DifficultySensitivityOption, double> TauBySensitivity { get; } = new Dictionary<DifficultySensitivityOption, double>
    {
        [DifficultySensitivityOption.Soft] = 8,
        [DifficultySensitivityOption.Normal] = 5,
        [DifficultySensitivityOption.Sharp] = 3
    };

    /// <summary>
    /// Default profiles used to initialize task-category-specific recommendations.
    /// </summary>
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

    /// <summary>
    /// Analyzes the supplied inputs and returns the full recommendation summary.
    /// </summary>
    /// <param name="inputs">The use case configuration to score.</param>
    /// <returns>A summary containing difficulty factors, guardrails, and ranked model recommendations.</returns>
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

        // CHANGED: silent-failure risk no longer touches difficulty. Its entire effect is on the
        // critical-failure share inside AnalyzeModel. We surface a guardrail note for transparency.
        if (inputs.HasSilentFailureRisk)
        {
            guardrailFactors.Add("Silent-failure risk raises modeled critical-failure exposure (detection problem), not task difficulty.");
        }

        // CHANGED: deterministic validation no longer touches difficulty. Its entire effect is on the
        // critical-failure rate inside AnalyzeModel.
        if (inputs.HasDeterministicValidation)
        {
            guardrailFactors.Add("Deterministic validation reduces modeled critical-failure exposure (catches schema/syntax failures, not semantic ones).");
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
            guardrailFactors.Add("No representative eval set selected. Treat the recommendation as a planning prior, not a production decision. The intelligence-vs-difficulty curve is uncalibrated without one.");
        }

        ApplyCategoryAdjustments(inputs, categoryProfile, difficultyFactors, guardrailFactors, normalizedBaseDifficulty, ref difficulty, ref criticalFailureExposureMultiplier);

        difficulty = Math.Clamp(difficulty, 0, 75);
        var tau = TauBySensitivity.TryGetValue(inputs.DifficultySensitivity, out var configuredTau) ? configuredTau : 5;
        var effectiveTau = EffectiveTau(tau, difficulty);
        var attempts = inputs.RetriesAllowed ? Math.Clamp(inputs.MaxAttempts, 1, 5) : 1;
        var targetSuccess = inputs.RequiredSuccessRate / 100d;
        var allowedCriticalFailure = inputs.AllowedCriticalFailureRate / 100d;

        var results = ModelCatalog.Models
            .Select(model => AnalyzeModel(model, inputs, difficulty, effectiveTau, attempts, targetSuccess, allowedCriticalFailure, criticalFailureExposureMultiplier))
            .OrderByDescending(x => x.IsEligible)
            .ThenByDescending(x => x.ExpectedValuePerTaskUsd)
            .ThenByDescending(x => x.EffectiveSuccessRate)
            .ThenBy(x => x.ExpectedTotalDirectCostUsd)
            .ToList();

        var eligible = results.Where(x => x.IsEligible).ToList();

        return new AnalysisSummary
        {
            EffectiveDifficulty = difficulty,
            Tau = effectiveTau,
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

        // CHANGED: the convex transform is sourced from the engine, not the model property, so the
        // curve has a single configurable home. model.IntelligenceIndex is the raw AA index.
        var adjustedIntelligence = AdjustedIntelligence(model.IntelligenceIndex);

        var singleAttemptSuccess = Sigmoid((adjustedIntelligence - difficulty) / tau);
        var effectiveSuccess = 1 - Math.Pow(1 - singleAttemptSuccess, EffectiveIndependentAttempts(attempts));
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
            criticalFailureShare = Math.Min(1, criticalFailureShare * SilentFailureCriticalShareMultiplier);
        }

        var criticalFailureRate = (1 - effectiveSuccess) * criticalFailureShare * criticalFailureExposureMultiplier;

        if (inputs.HasDeterministicValidation)
        {
            criticalFailureRate *= DeterministicValidationCriticalMultiplier;
        }

        if (inputs.HumanApprovalForHighRiskActions)
        {
            criticalFailureRate *= HumanApprovalCriticalMultiplier;
        }

        var costPerSuccessfulTask = model.HasCostData ? expectedTotalDirectCost / effectiveSuccess : double.NaN;
        var successPerDollar = model.HasCostData ? effectiveSuccess / Math.Max(expectedTotalDirectCost, 0.000001) * batchSize : 0;

        // Expected value retains the asymmetric framing: business value of a success minus direct
        // cost minus the cost of a failure, where FailureCostUsd is the *expected* failure cost. The
        // tail of that distribution is reported separately via WorstCaseFailureCostUsd so two models
        // with equal EV but different downside are distinguishable.
        var expectedValue = model.HasCostData
            ? inputs.BusinessValuePerSuccessUsd * effectiveSuccess * batchSize
              - expectedTotalDirectCost
              - inputs.FailureCostUsd * (1 - effectiveSuccess) * batchSize
            : double.NaN;
        var monthlyExpectedValue = model.HasCostData ? expectedValue * Math.Max(0, inputs.MonthlyVolume) : double.NaN;

        // NEW: downside exposure. The expected critical-failure cost over the batch, i.e. the part
        // of failure that is genuinely harmful rather than merely a retry. Lets the asymmetric-cost
        // philosophy show up as a number a reviewer can threshold on, not just an EV term.
        var worstCaseFailureCost = model.HasCostData
            ? inputs.FailureCostUsd * criticalFailureRate * batchSize
            : double.NaN;

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
            //AdjustedIntelligence = adjustedIntelligence,
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
            //WorstCaseFailureCostUsd = worstCaseFailureCost,
            SuccessPerDollar = successPerDollar,
            IsEligible = isEligible,
            ExclusionReasons = reasons,
            RecommendationReason = recommendationReason
        };
    }

    /// <summary>
    /// Scales the configured tau by the local slope of the intelligence curve at the task
    /// difficulty, so a sensitivity setting means the same thing in raw-index terms across the whole
    /// range. Without this, the 3x top-end slope makes every curve three times sharper for frontier
    /// models than for weak ones, and the Soft/Normal/Sharp settings stop being comparable.
    /// </summary>
    private static double EffectiveTau(double configuredTau, double difficulty)
    {
        var slope = LocalCurveSlope(difficulty);
        return configuredTau * Math.Max(slope, 0.0001);
    }

    /// <summary>
    /// Returns the slope of the configured intelligence curve at a given point on the (already
    /// adjusted) difficulty axis. Difficulty lives on the adjusted scale, so we find the segment
    /// whose adjusted span contains it and return that segment's slope.
    /// </summary>
    private static double LocalCurveSlope(double adjustedPoint)
    {
        var adjustedLower = 0d;
        var rawLower = 0d;

        foreach (var segment in IntelligenceCurve.Segments)
        {
            var rawSpan = segment.UpperBoundInclusive - rawLower;
            var adjustedUpper = double.IsPositiveInfinity(rawSpan) ? double.PositiveInfinity : adjustedLower + rawSpan * segment.Slope;

            if (adjustedPoint <= adjustedUpper || double.IsPositiveInfinity(adjustedUpper))
            {
                return segment.Slope;
            }

            adjustedLower = adjustedUpper;
            rawLower = segment.UpperBoundInclusive;
        }

        return IntelligenceCurve.Segments[^1].Slope;
    }

    /// <summary>
    /// Discounts retry attempts beyond the first to reflect failure correlation: a model that fails
    /// a given hard task tends to fail the retry on the *same* task. The naive
    /// 1 - failure^attempts formula treats attempts as independent, which is optimistic exactly where
    /// retries help least. We convert N nominal attempts into a smaller number of effective
    /// independent attempts.
    /// </summary>
    private static double EffectiveIndependentAttempts(int attempts)
    {
        if (attempts <= 1)
        {
            return 1;
        }

        // Each additional attempt contributes with diminishing independence. correlationDecay = 0.6
        // means the 2nd attempt is worth 0.6 of an independent try, the 3rd 0.36, etc.
        const double correlationDecay = 0.6;
        var effective = 1d;
        var weight = 1d;
        for (var i = 1; i < attempts; i++)
        {
            weight *= correlationDecay;
            effective += weight;
        }

        return effective;
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

    /// <summary>
    /// Attempts to resolve a category profile for the specified task category.
    /// </summary>
    public static bool TryGetTaskCategoryProfile(TaskCategoryOption category, out TaskCategoryProfile profile)
    {
        if (TaskCategoryProfiles.TryGetValue(category, out profile!))
        {
            return true;
        }

        profile = TaskCategoryProfiles[TaskCategoryOption.Other];
        return false;
    }

    /// <summary>
    /// Resolves a task category profile, falling back to the <see cref="TaskCategoryOption.Other"/> profile when needed.
    /// </summary>
    public static TaskCategoryProfile ResolveTaskCategoryProfile(TaskCategoryOption category)
    {
        TryGetTaskCategoryProfile(category, out var profile);
        return profile;
    }

    private static double GetCategoryResidualPercent(UseCaseInputs inputs, TaskCategoryProfile profile)
    {
        // CHANGED: the code-gen residual no longer special-cases deterministic validation. That
        // credit now lives solely on the critical-failure channel (see DeterministicValidation
        // constants), so it is not double-counted here. The category's intrinsic residual stands.
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
        var normalizedBaseDifficulty = categoryBaseline + (overrideDelta * BaseDifficultyOverrideWeight);

        if (Math.Abs(overrideDelta) < 0.05)
        {
            factor = $"Base difficulty: {categoryBaseline:0.0} category baseline";
            return normalizedBaseDifficulty;
        }

        // CHANGED: disclose the damping weight so the user can see why their override moved less than
        // they set it.
        factor = $"Base difficulty: {normalizedBaseDifficulty:0.0} ({selectedBaseDifficulty:0.0} selected, {categoryBaseline:0.0} category baseline, override honored at {BaseDifficultyOverrideWeight:P0})";
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
                    // Retained: conditioned on strict schema *and* validation together, this is a
                    // distinct signal from the standalone validation flag, not a duplicate of it.
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

    /// <summary>
    /// Formats a currency value using the current culture and a magnitude-aware precision.
    /// </summary>
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
