using AAInteractiveValueAnalyzer.Client.Models;

namespace AAInteractiveValueAnalyzer.Client.Services;

/// <summary>
/// Estimates the relative model cost of a workload from the three workload dimensions that have
/// benchmark-cost evidence: tool use, reasoning depth, and context size.
/// </summary>
/// <remarks>
/// The source data records each benchmark's median share of the Artificial Analysis Intelligence
/// Index's weighted average cost per task. Dividing that share by the benchmark's Index weight
/// yields a relative per-task multiplier. The coefficients below are an ordinary least-squares fit
/// over the logarithm of those nine median multipliers. Keep the source CSV beside the calibration
/// data when refreshing this fit.
/// </remarks>
public static class WorkloadCostEstimator
{
    private const double Intercept = -3.353501142634787;
    private const double ToolCoefficient = 1.486516783720942;
    private const double ReasoningCoefficient = 2.553114585277491;
    private const double ContextCoefficient = 0.115368103836260;

    /// <summary>
    /// Median benchmark-cost contributions used to fit the workload cost model. The normalized
    /// multiplier is <c>MedianContributionPercent / IntelligenceIndexWeightPercent</c>.
    /// </summary>
    public static IReadOnlyList<BenchmarkCostReference> BenchmarkReferences { get; } =
    [
        new("GDPval-AA v2", 40.45454545454545, 20),
        new("Terminal-Bench v2.1", 27.67195767195767, 16),
        new("Tau3-Banking", 11.805555555555557, 14),
        new("CritPt", 6.08294930875576, 6),
        new("Humanity's Last Exam", 4.782991202346041, 12),
        new("AA-LCR", 2.0833333333333335, 6),
        new("SciCode", 1.3611111111111112, 8),
        new("GPQA Diamond", 0.8462301587301586, 6),
        new("AA-Omniscience", 0.3855218855218855, 12)
    ];

    /// <summary>Lowest normalized median multiplier observed in the supplied benchmark data.</summary>
    public const double MinimumObservedMultiplier = 0.032126823793490;

    /// <summary>Highest normalized median multiplier observed in the supplied benchmark data.</summary>
    public const double MaximumObservedMultiplier = 2.022727272727272;

    /// <summary>
    /// Estimates the automatic cost factor for a use case. Domain, verifiability, and output are
    /// intentionally excluded because the supplied cost data does not classify those dimensions.
    /// </summary>
    public static double Estimate(UseCaseInputs inputs) =>
        Estimate(inputs.ContextRequirement, inputs.ReasoningDepth, inputs.ToolUse);

    /// <summary>Estimates the automatic cost factor from the three benchmark-classified inputs.</summary>
    public static double Estimate(
        ContextRequirementOption context,
        ReasoningDepthOption reasoning,
        ToolUseOption toolUse)
    {
        var exponent = Intercept
            + ToolCoefficient * ToolIntensity(toolUse)
            + ReasoningCoefficient * ReasoningIntensity(reasoning)
            + ContextCoefficient * ContextIntensity(context);
        var factor = Math.Exp(exponent);

        return double.IsFinite(factor)
            ? Math.Clamp(factor, MinimumObservedMultiplier, MaximumObservedMultiplier)
            : MaximumObservedMultiplier;
    }

    private static double ToolIntensity(ToolUseOption option) => option switch
    {
        ToolUseOption.None => 0,
        ToolUseOption.OneOrTwoDeterministicTools => 1d / 3d,
        ToolUseOption.MultipleToolsWithValidation => 2d / 3d,
        ToolUseOption.AutonomousToolSequence => 1,
        ToolUseOption.AgenticWorkflowWithIrreversibleActions => 1,
        _ => 0
    };

    private static double ReasoningIntensity(ReasoningDepthOption option) => option switch
    {
        ReasoningDepthOption.SingleStepTransformation => 0,
        ReasoningDepthOption.Light => 0.25,
        ReasoningDepthOption.ModerateMultiStep => 0.5,
        ReasoningDepthOption.DeepConditional => 0.75,
        ReasoningDepthOption.ResearchGradeSynthesisPlanning => 1,
        _ => 0
    };

    private static double ContextIntensity(ContextRequirementOption option) => option switch
    {
        ContextRequirementOption.None => 0,
        ContextRequirementOption.ShortClean => 0.2,
        ContextRequirementOption.MediumMostlyRelevant => 0.4,
        ContextRequirementOption.LargeClean => 0.6,
        ContextRequirementOption.LargeNoisy => 0.8,
        ContextRequirementOption.VeryLargeNoisyCrossDocument => 1,
        _ => 0
    };
}

/// <summary>One source benchmark used to calibrate <see cref="WorkloadCostEstimator"/>.</summary>
public sealed record BenchmarkCostReference(
    string Benchmark,
    double MedianContributionPercent,
    double IntelligenceIndexWeightPercent)
{
    public double NormalizedMedianMultiplier => MedianContributionPercent / IntelligenceIndexWeightPercent;
}
