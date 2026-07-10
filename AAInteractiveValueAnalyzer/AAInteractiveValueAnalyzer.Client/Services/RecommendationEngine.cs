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
public class RecommendationEngine(ModelCatalog modelCatalog)
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
    /// How strongly a model's headroom above the task tilts the realized good-output share around the
    /// user's base assumption (<see cref="UseCaseInputs.GoodOutcomeShareOfSuccesses"/>).
    /// </summary>
    /// <remarks>
    /// Partial credit splits a *passing* task into a fully-correct ("good") outcome worth the full
    /// business value and a degraded-but-acceptable outcome worth less. The share that lands in the
    /// good tier is not constant across models: a model that clears the difficulty bar with room to
    /// spare produces cleaner output than one that barely clears it, so two models with the *same*
    /// pass rate can still differ in realized value. We model that by tilting the base good-share by
    /// the model's single-attempt headroom -- how far its pre-retry success sits above the 0.5
    /// midpoint, in [-0.5, +0.5] -- scaled by this constant. At 0.30 a maximally-comfortable model
    /// (headroom +0.5) gets +0.15 added to its good-share and a maximally-marginal one (-0.5) loses
    /// 0.15, with the result clamped to [0,1]. Set to 0 to make the good-share a flat constant and
    /// decouple realized quality from model strength. This is what makes partial credit move the
    /// ranking rather than apply a constant haircut that cancels out of every comparison.
    /// </remarks>
    public const double QualityShareDifficultyTilt = 0.30;

    /// <summary>
    /// How strongly a model's headroom tilts the critical share of its *failures*, mirroring
    /// <see cref="QualityShareDifficultyTilt"/> on the downside.
    /// </summary>
    /// <remarks>
    /// NEW. The upside tilt says a comfortable model realizes more good outcomes among its passes.
    /// The mirror claim holds for failures: a marginal model's failures skew toward catastrophic
    /// misreadings of the task, while a comfortable model's rare failures skew toward benign slips
    /// (formatting, minor omissions). Negative headroom therefore raises the critical share of
    /// failures and positive headroom lowers it, scaled by this constant and clamped to [0,1],
    /// before the detection multipliers (silent failure, validation, approval) apply. Set to 0 to
    /// make the critical share a flat user assumption independent of model strength. Kept equal to
    /// the upside tilt by default so the two halves of the asymmetry are symmetric priors; both are
    /// candidates for fitting.
    /// </remarks>
    public const double CriticalShareDifficultyTilt = 0.30;

    /// <summary>
    /// Systematic single-attempt failure floor applied to every model regardless of task
    /// difficulty: refusals, formatting flukes, infrastructure errors, truncation.
    /// </summary>
    /// <remarks>
    /// NEW. Without a floor the sigmoid promises arbitrarily high success (a frontier model on an
    /// easy task approaches 99.9999% and sails through a 99.5% required-success gate), which is
    /// exactly where the tool's answer matters most. Success is now the product of two hurdles:
    /// the capability hurdle (the sigmoid) and this systematic hurdle. The floor is modeled as
    /// retry-resistant -- a refusal or infra failure recurs on retry at roughly the same rate --
    /// so retries recover only capability failures and no amount of retrying approaches 100%.
    /// The 1% default is a deliberate prior, not a measurement; it is a natural per-model figure
    /// once calibration data exists.
    /// </remarks>
    public const double BaseErrorFloorRate = 0.01;

    /// <summary>
    /// Independence weight of each additional retry attempt. The 2nd attempt counts as this
    /// fraction of a fresh independent try, the 3rd as the square, and so on. Shared by the
    /// success side (<see cref="EffectiveIndependentAttempts"/>) and the cost side
    /// (<see cref="ExpectedAttempts"/>) so both halves of the ledger use one retry model.
    /// </summary>
    public const double RetryCorrelationDecay = 0.6;

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
                DefaultBaseDifficulty: 4,
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
            [TaskCategoryOption.SimpleRag] = new(
                Category: TaskCategoryOption.SimpleRag,
                BaseDifficultyPercentResidual: -4,
                DefaultBaseDifficulty: 6,
                DefaultContextRequirement: ContextRequirementOption.ShortClean,
                DefaultReasoningDepth: ReasoningDepthOption.Light,
                DefaultToolUse: ToolUseOption.None,
                DefaultVerifiability: VerifiabilityOption.DeterministicallyTestable,
                DefaultOutputConstraint: OutputConstraintOption.FreeText,
                DefaultHasRepresentativeEvalSet: true,
                DefaultHasDeterministicValidation: false,
                DefaultRequiresStrictStructuredOutput: false,
                DefaultHasSilentFailureRisk: false),
            [TaskCategoryOption.ClassificationRouting] = new(TaskCategoryOption.ClassificationRouting,
                BaseDifficultyPercentResidual: 0, DefaultBaseDifficulty: 10,
                DefaultContextRequirement: ContextRequirementOption.LargeClean,
                DefaultReasoningDepth: ReasoningDepthOption.ModerateMultiStep,
                DefaultToolUse: ToolUseOption.None,
                DefaultVerifiability: VerifiabilityOption.MostlyVerifiableByReviewer,
                DefaultOutputConstraint: OutputConstraintOption.FreeText,
                DefaultHasSilentFailureRisk: true),
            [TaskCategoryOption.Summarization] = new(
                Category: TaskCategoryOption.Summarization,
                BaseDifficultyPercentResidual: 0,
                DefaultBaseDifficulty: 10,
                DefaultContextRequirement: ContextRequirementOption.LargeClean,
                DefaultReasoningDepth: ReasoningDepthOption.ModerateMultiStep,
                DefaultToolUse: ToolUseOption.None,
                DefaultVerifiability: VerifiabilityOption.MostlyVerifiableByReviewer,
                DefaultOutputConstraint: OutputConstraintOption.FreeText,
                DefaultHasSilentFailureRisk: true),
            [TaskCategoryOption.CodeGeneration] = new(
                Category: TaskCategoryOption.CodeGeneration,
                BaseDifficultyPercentResidual: 4,
                DefaultBaseDifficulty: 22,
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
    public async Task<AnalysisSummary> Analyze(UseCaseInputs inputs)
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

        var results = (await modelCatalog.GetLatestModelData())
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

        // CHANGED: success is now the product of two independent hurdles. The sigmoid models the
        // capability hurdle (can the model do the task at all); BaseErrorFloorRate models the
        // systematic hurdle (refusals, formatting flukes, infrastructure errors) that is
        // independent of difficulty and does not shrink with retries. Previously the sigmoid alone
        // could promise 99.9999% success on easy tasks and sail through a 99.5% required-success
        // gate that no real deployment clears.
        var capabilitySuccess = Math.Clamp(Sigmoid((adjustedIntelligence - difficulty) / tau), 0.000001, 0.999999);
        var singleAttemptSuccess = (1 - BaseErrorFloorRate) * capabilitySuccess;

        // Retries recover only correlated capability failures; the systematic floor survives them.
        var effectiveSuccess = (1 - BaseErrorFloorRate) * (1 - Math.Pow(1 - capabilitySuccess, EffectiveIndependentAttempts(attempts)));
        effectiveSuccess = Math.Clamp(effectiveSuccess, 0.000001, 0.999999);

        // Headroom above the difficulty bar, in [-0.5, +0.5). Drives the quality tilt on the
        // upside (realized good-share) and, mirrored, the critical-share tilt on the downside.
        var qualityHeadroom = singleAttemptSuccess - 0.5;

        // CHANGED: expected attempts now come from the same correlated-retry model as
        // effectiveSuccess. Previously this used the independent geometric formula, which
        // understates attempts for weak models (a model that failed a hard task tends to fail the
        // retry on the same task), so the ledger charged optimistic cost against
        // correlation-discounted success -- two different retry models on the two sides.
        var expectedAttempts = ExpectedAttempts(capabilitySuccess, attempts);
        var baseModelCost = model.CostPerAaTaskUsd.GetValueOrDefault() * inputs.CostMultiplier;
        var expectedModelCost = model.HasCostData ? baseModelCost * expectedAttempts * batchSize : double.NaN;
        var expectedReviewCost = Math.Max(0, inputs.HumanReviewCostUsd) * batchSize;
        var expectedRetryOverhead = Math.Max(0, expectedAttempts - 1) * Math.Max(0, inputs.OperationalRetryCostUsd) * batchSize;
        var expectedTotalDirectCost = model.HasCostData
            ? expectedModelCost + expectedReviewCost + expectedRetryOverhead
            : double.NaN;

        var criticalFailureShare = Math.Clamp(inputs.CriticalFailureShareOfFailures / 100d, 0, 1);

        // NEW: downside mirror of the quality tilt. Negative headroom (marginal model) raises the
        // critical share of failures, positive headroom lowers it; see CriticalShareDifficultyTilt.
        // Applied to the user's base assumption before the detection multipliers.
        criticalFailureShare = Math.Clamp(criticalFailureShare - qualityHeadroom * CriticalShareDifficultyTilt, 0, 1);

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

        // CHANGED: cap the critical rate at the total failure mass. With the share saturated at 1
        // (silent-failure multiplier) and an exposure multiplier above 1 (e.g. the 1.35 agentic
        // term), the product could previously exceed (1 - effectiveSuccess), charging a model 135%
        // of its failures as critical -- inflating EV penalties, the worst-case metric, and the
        // eligibility check beyond probability semantics. A model cannot fail critically more
        // often than it fails. Applied after every multiplier.
        criticalFailureRate = Math.Min(criticalFailureRate, 1 - effectiveSuccess);

        var costPerSuccessfulTask = model.HasCostData ? expectedTotalDirectCost / effectiveSuccess : double.NaN;
        var successPerDollar = model.HasCostData ? effectiveSuccess / Math.Max(expectedTotalDirectCost, 0.000001) * batchSize : 0;

        // NEW: latency. End-to-end seconds are the only cross-comparable latency figure (TTFT/TTFA
        // diverge only by whether reasoning tokens are streamed, not by real work timing). Expected
        // latency scales with expected attempts: a model that needs two tries waits twice. Latency is
        // a cost, not a difficulty term -- it does not change whether the model *can* do the task --
        // so it lives here in the value calculation, parallel to human-review cost.
        // CHANGED: missing latency data now reports NaN, matching the missing-cost sentinel,
        // instead of 0 -- a rendered "0.0s" reads as "instant and free". The EV sum still charges
        // 0 in that case (latencyCostCharged): when latency is priced or capped the model is
        // excluded below anyway, and when it is not, zero is the honest charge.
        var expectedLatencySeconds = model.HasLatencyData
            ? model.EndToEndResponseSeconds!.Value * expectedAttempts
            : double.NaN;
        var expectedLatencyCost = model.HasLatencyData
            ? expectedLatencySeconds * Math.Max(0, inputs.LatencyCostPerSecondUsd) * batchSize
            : double.NaN;
        var latencyCostCharged = model.HasLatencyData ? expectedLatencyCost : 0d;

        // Partial credit on the upside, mirroring the failure split below. A pass is subdivided into
        // a fully-correct "good" outcome (full value) and a degraded-but-acceptable one (reduced
        // value). The good-share is the user's base assumption tilted by this model's headroom above
        // the bar: singleAttemptSuccess - 0.5 lands in [-0.5, +0.5], so a comfortable model realizes
        // more good outcomes than a marginal one with the same pass rate. This is what makes the
        // feature move rankings rather than scale every model identically. Acceptable value is floored
        // at 0 and not allowed to exceed the good value (acceptable is by definition no better than
        // good); the resulting blended value is what each success is actually worth.
        var baseGoodShare = Math.Clamp(inputs.GoodOutcomeShareOfSuccesses / 100d, 0, 1);
        var realizedGoodShare = Math.Clamp(baseGoodShare + qualityHeadroom * QualityShareDifficultyTilt, 0, 1);
        var goodValue = inputs.BusinessValuePerSuccessUsd;
        var acceptableValue = Math.Clamp(inputs.AcceptableValuePerSuccessUsd, 0, goodValue);
        var blendedValuePerSuccess = goodValue * realizedGoodShare + acceptableValue * (1 - realizedGoodShare);

        // Expected value retains the asymmetric framing: business value of a success minus direct
        // cost minus latency cost minus the cost of failure. Failure cost is now split: the critical
        // share is charged at FailureCostUsd (the expensive tail), the remaining benign share at
        // BenignFailureCostUsd (caught/retried, usually cheap). criticalFailureRate already carries
        // every guardrail multiplier (silent-failure, deterministic validation, human approval, the
        // category exposure terms), so those controls now move EV directly, not just the advisory
        // downside metric. The benign rate is whatever failure mass is left after the critical part;
        // the cap on criticalFailureRate above guarantees this is non-negative, so the Max(0, ...) is
        // belt-and-suspenders.
        var benignFailureRate = Math.Max(0, (1 - effectiveSuccess) - criticalFailureRate);
        var expectedCriticalFailureCost = model.HasCostData
            ? inputs.FailureCostUsd * criticalFailureRate * batchSize
            : double.NaN;
        var expectedBenignFailureCost = model.HasCostData
            ? Math.Max(0, inputs.BenignFailureCostUsd) * benignFailureRate * batchSize
            : double.NaN;

        var expectedValue = model.HasCostData
            ? blendedValuePerSuccess * effectiveSuccess * batchSize
              - expectedTotalDirectCost
              - latencyCostCharged
              - expectedCriticalFailureCost
              - expectedBenignFailureCost
            : double.NaN;
        var monthlyExpectedValue = model.HasCostData ? expectedValue * Math.Max(0, inputs.MonthlyVolume) : double.NaN;

        // NEW: downside exposure. The expected critical-failure cost over the batch, i.e. the part
        // of failure that is genuinely harmful rather than merely a retry. This is identical to
        // expectedCriticalFailureCost above; kept as a distinctly named output so the asymmetric-cost
        // philosophy remains a number a reviewer can threshold on independent of the EV breakdown.
        var worstCaseFailureCost = expectedCriticalFailureCost;

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

        // NEW: latency handling. Latency only matters to eligibility when the user has actually
        // priced it (cost per second) or capped it (finite ceiling). When neither is set, latency is
        // ignored entirely and missing data is harmless.
        var latencyIsActive = inputs.LatencyCostPerSecondUsd > 0
            || !double.IsPositiveInfinity(inputs.MaxAcceptableLatencySeconds);

        if (latencyIsActive && !model.HasLatencyData)
        {
            // A data gap must not become a competitive advantage. If latency is priced or gated and
            // we cannot measure this model's latency, exclude it the same way a model with no cost
            // data is excluded -- rather than scoring it as instantaneous and free.
            reasons.Add("No latency data is available for this model, so it cannot be evaluated against the latency cost or limit.");
        }
        else if (model.HasLatencyData
            && !double.IsPositiveInfinity(inputs.MaxAcceptableLatencySeconds)
            && expectedLatencySeconds > inputs.MaxAcceptableLatencySeconds)
        {
            reasons.Add($"Estimated latency {expectedLatencySeconds:n1}s exceeds the maximum acceptable {inputs.MaxAcceptableLatencySeconds:n1}s.");
        }

        var isEligible = reasons.Count == 0;
        var recommendationReason = BuildRecommendationReason(model, expectedValue, effectiveSuccess, expectedTotalDirectCost, costPerSuccessfulTask, criticalFailureRate, isEligible, reasons);

        return new RecommendationResult
        {
            Model = model,
            EffectiveDifficulty = difficulty,
            Tau = tau,
            AdjustedIntelligence = adjustedIntelligence,
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
            RealizedGoodOutcomeShare = realizedGoodShare,
            BlendedValuePerSuccessUsd = blendedValuePerSuccess,
            ExpectedValuePerTaskUsd = expectedValue,
            MonthlyExpectedValueUsd = monthlyExpectedValue,
            ExpectedCriticalFailureCostUsd = expectedCriticalFailureCost,
            ExpectedBenignFailureCostUsd = expectedBenignFailureCost,
            WorstCaseFailureCostUsd = worstCaseFailureCost,
            ExpectedLatencySeconds = expectedLatencySeconds,
            ExpectedLatencyCostUsd = expectedLatencyCost,
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

        // Each additional attempt contributes with diminishing independence. RetryCorrelationDecay
        // = 0.6 means the 2nd attempt is worth 0.6 of an independent try, the 3rd 0.36, etc. The
        // same constant drives ExpectedAttempts so success and cost share one retry model.
        var effective = 1d;
        var weight = 1d;
        for (var i = 1; i < attempts; i++)
        {
            weight *= RetryCorrelationDecay;
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

    /// <summary>
    /// Expected number of attempts per task under the correlated-retry model, including the
    /// systematic error floor.
    /// </summary>
    /// <remarks>
    /// CHANGED. Previously the independent geometric formula (1 - failure^N) / p, which treated
    /// every retry as a fresh coin flip while <see cref="EffectiveIndependentAttempts"/> was
    /// already discounting the *success* of those same retries for correlation -- optimistic cost
    /// charged against pessimistic success, from two different retry models. Both sides of the
    /// ledger now share one model: the probability a task is still unresolved after k attempts is
    /// floor + (1 - floor) * capabilityFailure^E_k, where E_k is the effective number of
    /// independent attempts among the first k nominal ones (E_1 = 1, E_2 = 1 + decay, ...).
    /// Expected attempts is the sum of those survival probabilities over the allowed attempts,
    /// and 1 minus the final survival term is exactly the effectiveSuccess computed in
    /// AnalyzeModel. Correlated retries burn more attempts than independent ones, so this raises
    /// expected cost and latency for marginal models, consistent with their discounted success.
    /// </remarks>
    private static double ExpectedAttempts(double capabilitySuccess, int maxAttempts)
    {
        capabilitySuccess = Math.Clamp(capabilitySuccess, 0.000001, 0.999999);
        var capabilityFailure = 1 - capabilitySuccess;

        var expected = 0d;
        var effectiveAttemptsSoFar = 0d;
        var nextAttemptWeight = 1d;

        for (var attempt = 0; attempt < maxAttempts; attempt++)
        {
            // Survival probability entering this attempt; the first term is always 1.
            expected += BaseErrorFloorRate + (1 - BaseErrorFloorRate) * Math.Pow(capabilityFailure, effectiveAttemptsSoFar);
            effectiveAttemptsSoFar += nextAttemptWeight;
            nextAttemptWeight *= RetryCorrelationDecay;
        }

        return expected;
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
