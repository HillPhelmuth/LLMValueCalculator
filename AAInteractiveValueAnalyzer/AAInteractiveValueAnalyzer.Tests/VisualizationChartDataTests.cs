using AAInteractiveValueAnalyzer.Client.Models;
using Xunit;

namespace AAInteractiveValueAnalyzer.Tests;

public sealed class VisualizationChartDataTests
{
    [Fact]
    public void CreatePoints_AssignsUniqueNativeLabelCoordinatesToTiedValues()
    {
        var summary = new AnalysisSummary
        {
            Results =
            [
                Result("zeta", 50, 100, 20, modelCost: 20),
                Result("alpha", 50, 100, 20, modelCost: 20)
            ]
        };

        var points = VisualizationChartData.CreatePoints(summary);

        Assert.Equal(["alpha", "zeta"], points.Select(point => point.Result.Model.Name));
        Assert.Equal(2, points.Select(point => point.ExpectedValueLabelCoordinate).Distinct().Count());
        Assert.Equal(2, points.Select(point => point.AdjustedIntelligenceLabelCoordinate).Distinct().Count());
    }

    [Fact]
    public void CreateCostBreakdown_AggregatesEveryEconomicCostComponent()
    {
        var breakdown = VisualizationChartData.CreateCostBreakdown(Result(
            "model",
            intelligence: 50,
            expectedValue: 100,
            economicCost: 0,
            modelCost: 10,
            reviewCost: 20,
            retryCost: 30,
            latencyCost: 40,
            criticalCost: 50,
            benignCost: 60));

        Assert.Equal(210, breakdown.TotalEconomicCostUsd);
        Assert.Equal(40, breakdown.LatencyCostUsd);
    }

    [Fact]
    public void CreateCostBreakdown_OmitsUnavailableLatencyAndInvalidCosts()
    {
        var breakdown = VisualizationChartData.CreateCostBreakdown(Result(
            "model",
            intelligence: 50,
            expectedValue: 100,
            economicCost: 0,
            modelCost: double.NaN,
            latencyCost: double.NaN,
            criticalCost: -10));

        Assert.Null(breakdown.LatencyCostUsd);
        Assert.Equal(0, breakdown.ModelCostUsd);
        Assert.Equal(0, breakdown.CriticalFailureCostUsd);
        Assert.Equal(0, breakdown.TotalEconomicCostUsd);
    }

    [Fact]
    public void FindValueFrontier_RemovesPointsDominatedOnIntelligenceAndValue()
    {
        var points = new[]
        {
            Point("balanced", 60, 120, 60),
            Point("value", 50, 150, 50),
            Point("dominated", 45, 100, 40),
            Point("intelligence", 70, 90, 70)
        };

        var frontier = VisualizationChartData.FindValueFrontier(points);

        Assert.Equal(["value", "balanced", "intelligence"], frontier.Select(point => point.Result.Model.Name));
    }

    [Fact]
    public void FindCostValueFrontier_UsesLowerCostAndHigherValueDominance()
    {
        var points = new[]
        {
            Point("cheap", 40, 80, 10),
            Point("balanced", 50, 120, 20),
            Point("dominated", 55, 100, 25),
            Point("premium", 60, 150, 40)
        };

        var frontier = VisualizationChartData.FindCostValueFrontier(points);

        Assert.Equal(["cheap", "balanced", "premium"], frontier.Select(point => point.Result.Model.Name));
    }

    [Fact]
    public void FindCostIntelligenceFrontier_UsesLowerCostAndHigherCapabilityDominance()
    {
        var points = new[]
        {
            Point("cheap", 40, 80, 10),
            Point("balanced", 55, 100, 20),
            Point("dominated", 50, 150, 25),
            Point("capable", 70, 90, 40)
        };

        var frontier = VisualizationChartData.FindCostIntelligenceFrontier(points);

        Assert.Equal(["cheap", "balanced", "capable"], frontier.Select(point => point.Result.Model.Name));
    }

    [Fact]
    public void FindCostBandEnvelope_RemainsVisibleWhenStrictFrontierHasOnePoint()
    {
        var points = new[]
        {
            Point("best-and-cheapest", 60, 200, 1),
            Point("low-band", 55, 160, 10),
            Point("middle-band", 50, 140, 100),
            Point("high-band", 45, 100, 1000)
        };

        Assert.Single(VisualizationChartData.FindCostValueFrontier(points));

        var envelope = VisualizationChartData.FindCostBandEnvelope(points, maximumBands: 4);

        Assert.Equal(4, envelope.Count);
        Assert.Equal(
            ["best-and-cheapest", "low-band", "middle-band", "high-band"],
            envelope.Select(point => point.Result.Model.Name));
    }

    [Fact]
    public void FindCostBandEnvelope_SelectsTheHighestValueInEachLogCostBand()
    {
        var points = new[]
        {
            Point("low-value", 50, 100, 1),
            Point("low-winner", 50, 150, 2),
            Point("middle", 50, 140, 12),
            Point("high", 50, 130, 100)
        };

        var envelope = VisualizationChartData.FindCostBandEnvelope(points, maximumBands: 3);

        Assert.Equal(["low-winner", "middle", "high"], envelope.Select(point => point.Result.Model.Name));
    }

    [Fact]
    public void FrontierCalculations_KeepExactTiesAndSortDeterministically()
    {
        var points = new[]
        {
            Point("zeta", 50, 100, 20),
            Point("alpha", 50, 100, 20)
        };

        Assert.Equal(["alpha", "zeta"], VisualizationChartData.FindValueFrontier(points).Select(point => point.Result.Model.Name));
        Assert.Equal(["alpha", "zeta"], VisualizationChartData.FindCostValueFrontier(points).Select(point => point.Result.Model.Name));
    }

    [Fact]
    public void ExtentAndNormalization_HandleEqualNegativeAndInvalidValues()
    {
        var extent = VisualizationChartData.FiniteExtent([-12, -12], includeZero: false);

        Assert.True(extent.Minimum < -12);
        Assert.True(extent.Maximum > -12);
        Assert.Equal(0.5, VisualizationChartData.Normalize(5, 5, 5));
        Assert.Equal(0, VisualizationChartData.NormalizeLog(0, 1, 100));
        Assert.Equal(0.5, VisualizationChartData.NormalizeLog(10, 1, 100), 10);
    }

    [Fact]
    public void ResolveSelectionKey_PreservesCurrentThenUsesPreferredAndValueFallback()
    {
        var points = new[]
        {
            Point("first", 40, 80, 10),
            Point("best", 50, 120, 20)
        };

        Assert.Equal(points[0].Key, VisualizationChartData.ResolveSelectionKey(points, points[0].Key, points[1].Result));
        Assert.Equal(points[1].Key, VisualizationChartData.ResolveSelectionKey(points, "missing", points[1].Result));
        Assert.Equal(points[1].Key, VisualizationChartData.ResolveSelectionKey(points, "missing", null));
    }

    private static VisualizationPoint Point(string name, double intelligence, double expectedValue, double economicCost)
    {
        var result = Result(name, intelligence, expectedValue, economicCost, modelCost: economicCost);
        return new VisualizationPoint(
            VisualizationChartData.CreateKey(result.Model),
            result,
            VisualizationChartData.CreateCostBreakdown(result));
    }

    private static RecommendationResult Result(
        string name,
        double intelligence,
        double expectedValue,
        double economicCost,
        double modelCost = 0,
        double reviewCost = 0,
        double retryCost = 0,
        double latencyCost = 0,
        double criticalCost = 0,
        double benignCost = 0) => new()
    {
        Model = new ModelProfile("Provider", name, intelligence, 1),
        CapabilityIndexName = "Index",
        AdjustedIntelligence = intelligence,
        ExpectedValuePerTaskUsd = expectedValue,
        ExpectedModelCostUsd = modelCost,
        ExpectedReviewCostUsd = reviewCost,
        ExpectedRetryOverheadUsd = retryCost,
        ExpectedLatencyCostUsd = latencyCost,
        ExpectedCriticalFailureCostUsd = criticalCost,
        ExpectedBenignFailureCostUsd = benignCost,
        ExpectedTotalDirectCostUsd = economicCost
    };
}
