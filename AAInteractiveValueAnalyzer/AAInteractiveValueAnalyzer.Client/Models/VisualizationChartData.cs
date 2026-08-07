namespace AAInteractiveValueAnalyzer.Client.Models;

internal sealed record VisualizationCostBreakdown(
    double ModelCostUsd,
    double ReviewCostUsd,
    double RetryCostUsd,
    double? LatencyCostUsd,
    double CriticalFailureCostUsd,
    double BenignFailureCostUsd)
{
    public double TotalEconomicCostUsd =>
        ModelCostUsd
        + ReviewCostUsd
        + RetryCostUsd
        + LatencyCostUsd.GetValueOrDefault()
        + CriticalFailureCostUsd
        + BenignFailureCostUsd;
}

internal sealed record VisualizationPoint(
    string Key,
    RecommendationResult Result,
    VisualizationCostBreakdown Costs)
{
    public double AdjustedIntelligence => Result.AdjustedIntelligence;
    public double ExpectedValueUsd => Result.ExpectedValuePerTaskUsd;
    public double TotalEconomicCostUsd => Costs.TotalEconomicCostUsd;
}

internal static class VisualizationChartData
{
    private const double EqualityTolerance = 1e-9;

    public static IReadOnlyList<VisualizationPoint> CreatePoints(AnalysisSummary summary) =>
        summary.Results
            .Select(result => new VisualizationPoint(CreateKey(result.Model), result, CreateCostBreakdown(result)))
            .Where(point => IsFinite(point.AdjustedIntelligence)
                && IsFinite(point.ExpectedValueUsd)
                && IsFinite(point.TotalEconomicCostUsd))
            .OrderBy(point => point.Key, StringComparer.Ordinal)
            .ToArray();

    public static VisualizationCostBreakdown CreateCostBreakdown(RecommendationResult result) => new(
        NonNegativeOrZero(result.ExpectedModelCostUsd),
        NonNegativeOrZero(result.ExpectedReviewCostUsd),
        NonNegativeOrZero(result.ExpectedRetryOverheadUsd),
        IsFinite(result.ExpectedLatencyCostUsd) ? Math.Max(0, result.ExpectedLatencyCostUsd) : null,
        NonNegativeOrZero(result.ExpectedCriticalFailureCostUsd),
        NonNegativeOrZero(result.ExpectedBenignFailureCostUsd));

    public static string CreateKey(ModelProfile model) => $"{model.Provider}\u001f{model.Name}";

    public static IReadOnlyList<VisualizationPoint> FindValueFrontier(IEnumerable<VisualizationPoint> source)
    {
        var points = source.Where(IsPlottable).ToArray();
        return points
            .Where(point => !points.Any(other => other.Key != point.Key && DominatesValue(other, point)))
            .OrderBy(point => point.AdjustedIntelligence)
            .ThenByDescending(point => point.ExpectedValueUsd)
            .ThenBy(point => point.Key, StringComparer.Ordinal)
            .ToArray();
    }

    public static IReadOnlyList<VisualizationPoint> FindCostValueFrontier(IEnumerable<VisualizationPoint> source)
    {
        var points = source.Where(point => IsPlottable(point) && point.TotalEconomicCostUsd > 0).ToArray();
        return points
            .Where(point => !points.Any(other => other.Key != point.Key && DominatesCostValue(other, point)))
            .OrderBy(point => point.TotalEconomicCostUsd)
            .ThenByDescending(point => point.ExpectedValueUsd)
            .ThenBy(point => point.Key, StringComparer.Ordinal)
            .ToArray();
    }

    public static string? ResolveSelectionKey(
        IReadOnlyList<VisualizationPoint> points,
        string? currentKey,
        RecommendationResult? preferredResult)
    {
        if (currentKey is not null && points.Any(point => point.Key == currentKey))
        {
            return currentKey;
        }

        if (preferredResult is not null)
        {
            var preferredKey = CreateKey(preferredResult.Model);
            if (points.Any(point => point.Key == preferredKey))
            {
                return preferredKey;
            }
        }

        return points
            .OrderByDescending(point => point.ExpectedValueUsd)
            .ThenBy(point => point.Key, StringComparer.Ordinal)
            .Select(point => point.Key)
            .FirstOrDefault();
    }

    public static (double Minimum, double Maximum) FiniteExtent(
        IEnumerable<double> source,
        bool includeZero = false,
        double paddingFraction = 0.06)
    {
        var values = source.Where(IsFinite).ToList();
        if (includeZero)
        {
            values.Add(0);
        }

        if (values.Count == 0)
        {
            return (0, 1);
        }

        var minimum = values.Min();
        var maximum = values.Max();
        if (NearlyEqual(minimum, maximum))
        {
            var radius = Math.Max(Math.Abs(minimum) * 0.1, 1);
            return (minimum - radius, maximum + radius);
        }

        var padding = (maximum - minimum) * Math.Max(0, paddingFraction);
        return (minimum - padding, maximum + padding);
    }

    public static double Normalize(double value, double minimum, double maximum)
    {
        if (!IsFinite(value) || !IsFinite(minimum) || !IsFinite(maximum))
        {
            return 0;
        }

        if (NearlyEqual(minimum, maximum))
        {
            return 0.5;
        }

        return Math.Clamp((value - minimum) / (maximum - minimum), 0, 1);
    }

    public static double NormalizeLog(double value, double minimumPositive, double maximumPositive)
    {
        if (!IsFinite(value) || !IsFinite(minimumPositive) || !IsFinite(maximumPositive)
            || value <= 0 || minimumPositive <= 0 || maximumPositive <= 0)
        {
            return 0;
        }

        return Normalize(Math.Log10(value), Math.Log10(minimumPositive), Math.Log10(maximumPositive));
    }

    public static bool IsFinite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);

    private static bool IsPlottable(VisualizationPoint point) =>
        IsFinite(point.AdjustedIntelligence)
        && IsFinite(point.ExpectedValueUsd)
        && IsFinite(point.TotalEconomicCostUsd);

    private static bool DominatesValue(VisualizationPoint candidate, VisualizationPoint point)
    {
        var atLeastAsIntelligent = candidate.AdjustedIntelligence >= point.AdjustedIntelligence - EqualityTolerance;
        var atLeastAsValuable = candidate.ExpectedValueUsd >= point.ExpectedValueUsd - EqualityTolerance;
        var strictlyBetter = candidate.AdjustedIntelligence > point.AdjustedIntelligence + EqualityTolerance
            || candidate.ExpectedValueUsd > point.ExpectedValueUsd + EqualityTolerance;
        return atLeastAsIntelligent && atLeastAsValuable && strictlyBetter;
    }

    private static bool DominatesCostValue(VisualizationPoint candidate, VisualizationPoint point)
    {
        var noMoreExpensive = candidate.TotalEconomicCostUsd <= point.TotalEconomicCostUsd + EqualityTolerance;
        var atLeastAsValuable = candidate.ExpectedValueUsd >= point.ExpectedValueUsd - EqualityTolerance;
        var strictlyBetter = candidate.TotalEconomicCostUsd < point.TotalEconomicCostUsd - EqualityTolerance
            || candidate.ExpectedValueUsd > point.ExpectedValueUsd + EqualityTolerance;
        return noMoreExpensive && atLeastAsValuable && strictlyBetter;
    }

    private static double NonNegativeOrZero(double value) => IsFinite(value) ? Math.Max(0, value) : 0;

    private static bool NearlyEqual(double left, double right) => Math.Abs(left - right) <= EqualityTolerance;
}
