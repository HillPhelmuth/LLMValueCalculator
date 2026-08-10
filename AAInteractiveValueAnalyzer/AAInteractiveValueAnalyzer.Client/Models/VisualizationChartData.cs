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
    VisualizationCostBreakdown Costs,
    int LabelOrdinal = 0)
{
    private double LabelOffset => LabelOrdinal * 0.0000001;

    public double AdjustedIntelligence => Result.AdjustedIntelligence;
    public double AdjustedIntelligenceLabelCoordinate => AdjustedIntelligence + LabelOffset;
    public double ExpectedValueUsd => Result.ExpectedValuePerTaskUsd;
    public double ExpectedValueLabelCoordinate => ExpectedValueUsd + LabelOffset;
    public double TotalEconomicCostUsd => Costs.TotalEconomicCostUsd;
    public double LogEconomicCost => TotalEconomicCostUsd > 0 ? Math.Log10(TotalEconomicCostUsd) : double.NaN;
    public double EffectiveSuccessRate => Result.EffectiveSuccessRate;
    public double SuccessPercent => Result.EffectiveSuccessRate * 100;
    public double SuccessPerDollar => Result.SuccessPerDollar;
    public double CriticalFailurePercent => Result.CriticalFailureRate * 100;
    public double ExpectedLatencySeconds => Result.ExpectedLatencySeconds;
    public double ModelCostUsd => Costs.ModelCostUsd;
    public double ReviewCostUsd => Costs.ReviewCostUsd;
    public double RetryCostUsd => Costs.RetryCostUsd;
    public double LatencyCostUsd => Costs.LatencyCostUsd.GetValueOrDefault();
    public double CriticalFailureCostUsd => Costs.CriticalFailureCostUsd;
    public double BenignFailureCostUsd => Costs.BenignFailureCostUsd;
    public string ModelLabel => Result.Model.DisplayName;
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
            .Select((point, index) => point with { LabelOrdinal = index + 1 })
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

    public static IReadOnlyList<VisualizationPoint> FindCostIntelligenceFrontier(IEnumerable<VisualizationPoint> source)
    {
        var points = source.Where(point => IsPlottable(point) && point.TotalEconomicCostUsd > 0).ToArray();
        return points
            .Where(point => !points.Any(other => other.Key != point.Key && DominatesCostIntelligence(other, point)))
            .OrderBy(point => point.TotalEconomicCostUsd)
            .ThenBy(point => point.AdjustedIntelligence)
            .ThenBy(point => point.Key, StringComparer.Ordinal)
            .ToArray();
    }

    public static IReadOnlyList<VisualizationPoint> FindCostBandEnvelope(
        IEnumerable<VisualizationPoint> source,
        int maximumBands = 14)
    {
        var points = source
            .Where(point => IsPlottable(point) && point.TotalEconomicCostUsd > 0)
            .OrderBy(point => point.TotalEconomicCostUsd)
            .ThenBy(point => point.Key, StringComparer.Ordinal)
            .ToArray();

        if (points.Length <= 2 || maximumBands <= 1)
        {
            return points;
        }

        var minimumLogCost = Math.Log10(points[0].TotalEconomicCostUsd);
        var maximumLogCost = Math.Log10(points[^1].TotalEconomicCostUsd);
        if (NearlyEqual(minimumLogCost, maximumLogCost))
        {
            return points
                .OrderByDescending(point => point.ExpectedValueUsd)
                .ThenBy(point => point.TotalEconomicCostUsd)
                .ThenBy(point => point.Key, StringComparer.Ordinal)
                .Take(1)
                .ToArray();
        }

        var bandCount = Math.Clamp(maximumBands, 2, points.Length);
        var bandWidth = (maximumLogCost - minimumLogCost) / bandCount;

        return points
            .GroupBy(point => Math.Min(
                bandCount - 1,
                (int)Math.Floor((Math.Log10(point.TotalEconomicCostUsd) - minimumLogCost) / bandWidth)))
            .Select(group => group
                .OrderByDescending(point => point.ExpectedValueUsd)
                .ThenBy(point => point.TotalEconomicCostUsd)
                .ThenBy(point => point.Key, StringComparer.Ordinal)
                .First())
            .OrderBy(point => point.TotalEconomicCostUsd)
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

    private static bool DominatesCostIntelligence(VisualizationPoint candidate, VisualizationPoint point)
    {
        var noMoreExpensive = candidate.TotalEconomicCostUsd <= point.TotalEconomicCostUsd + EqualityTolerance;
        var atLeastAsIntelligent = candidate.AdjustedIntelligence >= point.AdjustedIntelligence - EqualityTolerance;
        var strictlyBetter = candidate.TotalEconomicCostUsd < point.TotalEconomicCostUsd - EqualityTolerance
            || candidate.AdjustedIntelligence > point.AdjustedIntelligence + EqualityTolerance;
        return noMoreExpensive && atLeastAsIntelligent && strictlyBetter;
    }

    private static double NonNegativeOrZero(double value) => IsFinite(value) ? Math.Max(0, value) : 0;

    private static bool NearlyEqual(double left, double right) => Math.Abs(left - right) <= EqualityTolerance;
}
