using System.Globalization;
using AAInteractiveValueAnalyzer.Client.Models;
using Microsoft.AspNetCore.Components;
using Radzen;
using Radzen.Blazor;

namespace AAInteractiveValueAnalyzer.Client.Components;

public partial class AnalysisVisualizationDashboard
{
    private const int DenseChartLimit = 36;
    private const int CostAnatomyLimit = 12;

    private static readonly string[] ProviderPalette =
    [
        "#e2762d", "#4d7fe8", "#61a46a", "#b97b61", "#d24f70", "#8f79d6",
        "#f1c453", "#73a942", "#4eb5a8", "#e8584f", "#64a9e8", "#a16dd3",
        "#d98b45", "#5bca88", "#ce6fa8", "#8ba7ca", "#dfaf5f", "#6678dd",
        "#cf875f", "#8fba51", "#5f9fcb", "#d7673c", "#9c82c7", "#78b7a4"
    ];

    private static readonly IReadOnlyList<ChartOption> ChartOptions =
    [
        new(ChartKind.ValueFrontier, "Decision · Value frontier", "Capability, expected value, and success efficiency in one bubble plot."),
        new(ChartKind.CostValue, "Decision · Cost/value frontier", "Find the models that create the most value for the least economic cost."),
        new(ChartKind.CostAnatomy, "Cost · Stacked anatomy", "Compare the top-value contenders' direct, retry, latency, and failure-cost composition."),
        new(ChartKind.CostPareto, "Decision · Cost/intelligence Pareto", "Find low-cost, high-capability models along the Pareto-optimal frontier."),
        new(ChartKind.ProviderDrilldown, "Interactive · Provider drilldown", "Click a provider, then click a model to inspect its result."),
        new(ChartKind.ValueCandlestick, "Financial · Value candlesticks", "Read downside, direct spend, net EV, and gross benefit as OHLC-style envelopes."),
        new(ChartKind.SelectedWaterfall, "Financial · Selected-model waterfall", "Reconcile gross expected benefit through every cost component to net EV."),
        new(ChartKind.ProviderBoxPlot, "Statistical · Provider box plots", "Compare each provider's EV distribution, quartiles, whiskers, and mean."),
        new(ChartKind.ModelRadar, "Profile · Model radar", "Compare the selected model with the cohort median across six normalized dimensions."),
        new(ChartKind.ReliabilityRange, "Quality · Reliability uplift", "Show the lift from one attempt to the configured retry policy."),
        new(ChartKind.SuccessBullet, "Quality · Success targets", "Rank model success against a 95% reference target with bullet charts."),
        new(ChartKind.EligibilityFunnel, "Quality · Eligibility funnel", "Follow the cohort through value, success, safety, and eligibility gates."),
        new(ChartKind.ProviderOpportunity, "Portfolio · Provider opportunity", "Show each provider's share of positive expected value."),
        new(ChartKind.ValueHeatmap, "Statistical · Model heatmap", "Compare the strongest models across value, quality, cost, speed, and risk.")
    ];

    private IReadOnlyList<VisualizationPoint> _allPoints = [];
    private IReadOnlyList<VisualizationPoint> _points = [];
    private IReadOnlyList<VisualizationPoint> _valueFrontier = [];
    private IReadOnlyList<VisualizationPoint> _costFrontier = [];
    private IReadOnlyList<VisualizationPoint> _costBandEnvelope = [];
    private IReadOnlyList<VisualizationPoint> _rankedByValue = [];
    private IReadOnlyList<VisualizationPoint> _rankedByCost = [];
    private IReadOnlyList<ProviderOption> _providers = [];
    private IReadOnlyList<VisualizationPoint> _paretoFrontier = [];
    private IReadOnlyList<ProviderPointSeries> _paretoProviders = [];
    private IReadOnlyList<AttractiveRegionDatum> _attractiveRegion = [];
    private IReadOnlyDictionary<double, string> _expectedValuePointLabels = new Dictionary<double, string>();
    private IReadOnlyDictionary<double, string> _intelligencePointLabels = new Dictionary<double, string>();
    private IReadOnlyList<ProviderDatum> _providerData = [];
    private IReadOnlyList<DrilldownDatum> _drilldownData = [];
    private IReadOnlyList<CandleDatum> _candles = [];
    private IReadOnlyList<WaterfallDatum> _waterfall = [];
    private double _waterfallAxisMinimum;
    private double _waterfallAxisMaximum = 1;
    private IReadOnlyList<BoxPlotDatum> _boxPlots = [];
    private IReadOnlyList<RadarDatum> _selectedRadar = [];
    private IReadOnlyList<RadarDatum> _medianRadar = [];
    private IReadOnlyList<RangeDatum> _reliabilityRanges = [];
    private IReadOnlyList<BulletDatum> _bulletData = [];
    private IReadOnlyList<FunnelDatum> _funnelData = [];
    private IReadOnlyList<ProviderShareDatum> _providerShares = [];
    private IReadOnlyList<ModelHeatmapRow> _heatmapRows = [];
    private string? _selectedKey;
    private string _providerFilter = string.Empty;
    private string? _drilldownProvider;
    private bool _hideExcludedModels;
    private bool _showPointLabels;
    private ChartKind _selectedChart = ChartKind.ValueFrontier;

    [Parameter, EditorRequired]
    public AnalysisSummary Summary { get; set; } = new();

    private VisualizationPoint? SelectedPoint =>
        _points.FirstOrDefault(point => point.Key == _selectedKey)
        ?? _allPoints.FirstOrDefault(point => point.Key == _selectedKey);

    private ChartOption ActiveChart => ChartOptions.First(option => option.Kind == _selectedChart);
    private bool SupportsPointLabels => _selectedChart is ChartKind.ValueFrontier or ChartKind.CostValue or ChartKind.CostPareto;
    private double CostChartHeight => Math.Max(560, 90 + _rankedByCost.Count * 27);
    private double RangeChartHeight => Math.Max(560, 90 + _reliabilityRanges.Count * 27);
    private double BulletChartHeight => Math.Max(560, 90 + _bulletData.Count * 27);
    private double DrilldownChartHeight => Math.Max(560, 120 + _drilldownData.Count * 30);
    private int FilteredUniverseCount => _hideExcludedModels
        ? _allPoints.Count(point => point.Result.IsEligible)
        : _allPoints.Count;

    private string ProviderFilter
    {
        get => _providerFilter;
        set
        {
            var next = value ?? string.Empty;
            if (string.Equals(_providerFilter, next, StringComparison.Ordinal))
            {
                return;
            }

            _providerFilter = next;
            _drilldownProvider = null;
            RebuildChartState();
        }
    }

    private bool HideExcludedModels
    {
        get => _hideExcludedModels;
        set
        {
            if (_hideExcludedModels == value)
            {
                return;
            }

            _hideExcludedModels = value;
            _drilldownProvider = null;
            RebuildChartState();
        }
    }

    protected override void OnParametersSet()
    {
        _allPoints = VisualizationChartData.CreatePoints(Summary);
        RebuildChartState();
    }

    private void RebuildChartState()
    {
        var filterablePoints = _hideExcludedModels
            ? _allPoints.Where(point => point.Result.IsEligible).ToArray()
            : _allPoints;

        _providers = filterablePoints
            .GroupBy(point => point.Result.Model.Provider, StringComparer.CurrentCultureIgnoreCase)
            .Select(group => new ProviderOption(group.Key, group.Count()))
            .OrderBy(provider => provider.Name, StringComparer.CurrentCultureIgnoreCase)
            .ToArray();

        if (!string.IsNullOrEmpty(_providerFilter)
            && _providers.All(provider => !string.Equals(provider.Name, _providerFilter, StringComparison.CurrentCultureIgnoreCase)))
        {
            _providerFilter = string.Empty;
        }

        _points = string.IsNullOrEmpty(_providerFilter)
            ? filterablePoints
            : filterablePoints.Where(point => string.Equals(
                point.Result.Model.Provider,
                _providerFilter,
                StringComparison.CurrentCultureIgnoreCase)).ToArray();

        _selectedKey = VisualizationChartData.ResolveSelectionKey(_points, _selectedKey, Summary.BestExpectedValue);
        _valueFrontier = VisualizationChartData.FindValueFrontier(_points);
        _costFrontier = VisualizationChartData.FindCostValueFrontier(_points);
        _costBandEnvelope = VisualizationChartData.FindCostBandEnvelope(_points);
        _rankedByValue = _points
            .OrderByDescending(point => point.ExpectedValueUsd)
            .ThenByDescending(point => point.Result.EffectiveSuccessRate)
            .ThenBy(point => point.Key, StringComparer.Ordinal)
            .Take(DenseChartLimit)
            .ToArray();
        _rankedByCost = _points
            .OrderByDescending(point => point.ExpectedValueUsd)
            .Take(CostAnatomyLimit)
            .OrderBy(point => point.TotalEconomicCostUsd)
            .ThenByDescending(point => point.ExpectedValueUsd)
            .ThenBy(point => point.Key, StringComparer.Ordinal)
            .ToArray();

        BuildProviderData();
        BuildParetoFrontierData();
        BuildPointLabelLookups();
        BuildCandles();
        BuildDistributionData();
        BuildQualityData();
        BuildHeatmapData();
        BuildDrilldownData();
        BuildSelectedModelViews();
    }

    private void BuildProviderData()
    {
        _providerData = _points
            .GroupBy(point => point.Result.Model.Provider, StringComparer.CurrentCultureIgnoreCase)
            .Select(group => new ProviderDatum(
                group.Key,
                group.Count(),
                group.Average(point => point.ExpectedValueUsd),
                group.Sum(point => Math.Max(0, point.ExpectedValueUsd)),
                group.Average(point => point.Result.EffectiveSuccessRate) * 100))
            .OrderByDescending(provider => provider.AverageValue)
            .ThenBy(provider => provider.Provider, StringComparer.CurrentCultureIgnoreCase)
            .ToArray();

        var shares = _providerData
            .Where(provider => provider.PositiveValue > 0)
            .OrderByDescending(provider => provider.PositiveValue)
            .ToArray();
        if (shares.Length == 0)
        {
            _providerShares = _providerData
                .Select(provider => new ProviderShareDatum(provider.Provider, provider.ModelCount))
                .ToArray();
            return;
        }

        var primary = shares.Take(9)
            .Select(provider => new ProviderShareDatum(provider.Provider, provider.PositiveValue))
            .ToList();
        var remainder = shares.Skip(9).Sum(provider => provider.PositiveValue);
        if (remainder > 0)
        {
            primary.Add(new ProviderShareDatum("Other providers", remainder));
        }
        _providerShares = primary;
    }

    private void BuildParetoFrontierData()
    {
        var plottable = _points
            .Where(point => point.TotalEconomicCostUsd > 0)
            .ToArray();
        if (plottable.Length == 0)
        {
            _paretoFrontier = [];
            _paretoProviders = [];
            _attractiveRegion = [];
            return;
        }

        _paretoFrontier = VisualizationChartData.FindCostIntelligenceFrontier(plottable);
        _paretoProviders = plottable
            .GroupBy(point => point.Result.Model.Provider, StringComparer.CurrentCultureIgnoreCase)
            .OrderBy(group => group.Key, StringComparer.CurrentCultureIgnoreCase)
            .Select(group => new ProviderPointSeries(
                group.Key,
                ProviderColor(group.Key),
                group.OrderBy(point => point.TotalEconomicCostUsd).ThenBy(point => point.Key, StringComparer.Ordinal).ToArray()))
            .ToArray();

        var logCosts = plottable.Select(point => point.LogEconomicCost).Order().ToArray();
        var intelligence = plottable.Select(point => point.AdjustedIntelligence).Order().ToArray();
        var intelligenceThreshold = Percentile(intelligence, 0.5);
        var intelligencePadding = Math.Max(1, (intelligence[^1] - intelligence[0]) * 0.06);
        var regionMaximum = intelligence[^1] + intelligencePadding;
        _attractiveRegion =
        [
            new(logCosts[0], intelligenceThreshold, regionMaximum),
            new(Percentile(logCosts, 0.55), intelligenceThreshold, regionMaximum)
        ];
    }

    private string ProviderColor(string provider)
    {
        var index = _providers
            .Select((item, position) => (item, position))
            .FirstOrDefault(candidate => string.Equals(candidate.item.Name, provider, StringComparison.CurrentCultureIgnoreCase))
            .position;
        return ProviderPalette[Math.Abs(index) % ProviderPalette.Length];
    }

    private void BuildPointLabelLookups()
    {
        _expectedValuePointLabels = _points
            .GroupBy(point => point.ExpectedValueLabelCoordinate)
            .ToDictionary(group => group.Key, group => ShortName(group.First().ModelLabel, 24));
        _intelligencePointLabels = _points
            .GroupBy(point => point.AdjustedIntelligenceLabelCoordinate)
            .ToDictionary(group => group.Key, group => ShortName(group.First().ModelLabel, 26));
    }

    private string ExpectedValuePointLabel(object value) => PointLabel(value, _expectedValuePointLabels);

    private string IntelligencePointLabel(object value) => PointLabel(value, _intelligencePointLabels);

    private static string PointLabel(object value, IReadOnlyDictionary<double, string> labels) =>
        TryDouble(value, out var coordinate) && labels.TryGetValue(coordinate, out var label) ? label : string.Empty;

    private void BuildCandles()
    {
        _candles = _rankedByValue
            .Take(28)
            .Reverse()
            .Select((point, index) =>
            {
                var directSpend = -Math.Max(0, point.Result.ExpectedTotalDirectCostUsd);
                var totalDownside = -point.TotalEconomicCostUsd;
                var netValue = point.ExpectedValueUsd;
                var grossBenefit = point.ExpectedValueUsd + point.TotalEconomicCostUsd;
                return new CandleDatum(
                    point.Key,
                    index,
                    ShortName(point.Result.Model.DisplayName, 20),
                    Math.Min(Math.Min(totalDownside, directSpend), netValue),
                    directSpend,
                    Math.Max(Math.Max(grossBenefit, directSpend), netValue),
                    netValue);
            })
            .ToArray();
    }

    private void BuildDistributionData()
    {
        _boxPlots = _points
            .GroupBy(point => point.Result.Model.Provider, StringComparer.CurrentCultureIgnoreCase)
            .Where(group => group.Any())
            .Select(group =>
            {
                var values = group.Select(point => point.ExpectedValueUsd).Order().ToArray();
                var q1 = Percentile(values, 0.25);
                var median = Percentile(values, 0.5);
                var q3 = Percentile(values, 0.75);
                var iqr = q3 - q1;
                var lowerFence = q1 - 1.5 * iqr;
                var upperFence = q3 + 1.5 * iqr;
                return new BoxPlotDatum(
                    group.Key,
                    values.Where(value => value >= lowerFence).DefaultIfEmpty(values[0]).Min(),
                    q1,
                    median,
                    q3,
                    values.Where(value => value <= upperFence).DefaultIfEmpty(values[^1]).Max(),
                    values.Average(),
                    values.Length);
            })
            .OrderByDescending(item => item.Median)
            .ToArray();
    }

    private void BuildQualityData()
    {
        _reliabilityRanges = _points
            .OrderByDescending(point => point.Result.EffectiveSuccessRate - point.Result.SingleAttemptSuccessRate)
            .ThenByDescending(point => point.Result.EffectiveSuccessRate)
            .Take(DenseChartLimit)
            .Select(point => new RangeDatum(
                point.Key,
                ShortName(point.Result.Model.DisplayName, 28),
                point.Result.SingleAttemptSuccessRate * 100,
                point.Result.EffectiveSuccessRate * 100,
                point.Result.ExpectedAttempts))
            .Reverse()
            .ToArray();

        _bulletData = _rankedByValue
            .Select(point => new BulletDatum(
                point.Key,
                ShortName(point.Result.Model.DisplayName, 28),
                point.Result.EffectiveSuccessRate * 100,
                95,
                100))
            .Reverse()
            .ToArray();

        IEnumerable<VisualizationPoint> gate = _points;
        var funnel = new List<FunnelDatum> { new("Analyzed", _points.Count) };
        gate = gate.Where(point => point.ExpectedValueUsd > 0);
        funnel.Add(new("Positive EV", gate.Count()));
        gate = gate.Where(point => point.Result.EffectiveSuccessRate >= 0.8);
        funnel.Add(new("≥80% success", gate.Count()));
        gate = gate.Where(point => point.Result.CriticalFailureRate <= 0.02);
        funnel.Add(new("≤2% critical risk", gate.Count()));
        gate = gate.Where(point => point.Result.IsEligible);
        funnel.Add(new("Eligible", gate.Count()));
        _funnelData = funnel;
    }

    private void BuildHeatmapData()
    {
        if (_points.Count == 0)
        {
            _heatmapRows = [];
            return;
        }

        const int rowLimit = 30;
        var capability = _points.Select(point => point.AdjustedIntelligence).Order().ToArray();
        var success = _points.Select(point => point.EffectiveSuccessRate).Order().ToArray();
        var expectedValue = _points.Select(point => point.ExpectedValueUsd).Order().ToArray();
        var directCost = _points.Select(point => point.Result.ExpectedTotalDirectCostUsd).Order().ToArray();
        var latency = _points
            .Where(point => point.Result.Model.HasLatencyData)
            .Select(point => point.ExpectedLatencySeconds)
            .Order()
            .ToArray();
        var criticalFailure = _points.Select(point => point.Result.CriticalFailureRate).Order().ToArray();
        var successPerDollar = _points.Select(point => point.SuccessPerDollar).Order().ToArray();

        _heatmapRows = _points
            .OrderByDescending(point => point.ExpectedValueUsd)
            .ThenByDescending(point => point.EffectiveSuccessRate)
            .ThenBy(point => point.Result.ExpectedTotalDirectCostUsd)
            .ThenBy(point => point.Key, StringComparer.Ordinal)
            .Take(rowLimit)
            .Select((point, index) => new ModelHeatmapRow(
                index + 1,
                point,
                DesirabilityScore(capability, point.AdjustedIntelligence),
                DesirabilityScore(success, point.EffectiveSuccessRate),
                DesirabilityScore(expectedValue, point.ExpectedValueUsd),
                DesirabilityScore(directCost, point.Result.ExpectedTotalDirectCostUsd, lowerIsBetter: true),
                point.Result.Model.HasLatencyData
                    ? DesirabilityScore(latency, point.ExpectedLatencySeconds, lowerIsBetter: true)
                    : null,
                DesirabilityScore(criticalFailure, point.Result.CriticalFailureRate, lowerIsBetter: true),
                DesirabilityScore(successPerDollar, point.SuccessPerDollar)))
            .ToArray();
    }

    private void BuildDrilldownData()
    {
        if (string.IsNullOrEmpty(_drilldownProvider))
        {
            _drilldownData = _providerData
                .Select(provider => new DrilldownDatum(
                    provider.Provider,
                    provider.Provider,
                    null,
                    provider.AverageValue,
                    provider.ModelCount))
                .OrderBy(item => item.Value)
                .ToArray();
            return;
        }

        _drilldownData = _points
            .Where(point => string.Equals(point.Result.Model.Provider, _drilldownProvider, StringComparison.CurrentCultureIgnoreCase))
            .OrderBy(point => point.ExpectedValueUsd)
            .Select(point => new DrilldownDatum(
                ShortName(point.Result.Model.DisplayName, 32),
                point.Result.Model.Provider,
                point.Key,
                point.ExpectedValueUsd,
                1))
            .ToArray();
    }

    private void BuildSelectedModelViews()
    {
        var selected = SelectedPoint;
        if (selected is null)
        {
            _waterfall = [];
            _waterfallAxisMinimum = 0;
            _waterfallAxisMaximum = 1;
            _selectedRadar = [];
            _medianRadar = [];
            return;
        }

        var grossBenefit = selected.ExpectedValueUsd + selected.TotalEconomicCostUsd;
        _waterfall =
        [
            new("Gross benefit", grossBenefit, false, grossBenefit),
            new("Model", -selected.Costs.ModelCostUsd, false, -selected.Costs.ModelCostUsd),
            new("Review", -selected.Costs.ReviewCostUsd, false, -selected.Costs.ReviewCostUsd),
            new("Retry", -selected.Costs.RetryCostUsd, false, -selected.Costs.RetryCostUsd),
            new("Latency", -selected.Costs.LatencyCostUsd.GetValueOrDefault(), false, -selected.Costs.LatencyCostUsd.GetValueOrDefault()),
            new("Critical failure", -selected.Costs.CriticalFailureCostUsd, false, -selected.Costs.CriticalFailureCostUsd),
            new("Benign failure", -selected.Costs.BenignFailureCostUsd, false, -selected.Costs.BenignFailureCostUsd),
            new("Net EV", 0, true, selected.ExpectedValueUsd)
        ];

        var runningValue = grossBenefit;
        var cumulativeValues = new List<double> { runningValue };
        foreach (var step in _waterfall.Skip(1).Where(step => !step.IsSummary))
        {
            runningValue += step.Amount;
            cumulativeValues.Add(runningValue);
        }
        cumulativeValues.Add(selected.ExpectedValueUsd);
        var cumulativeMinimum = cumulativeValues.Min();
        var cumulativeMaximum = cumulativeValues.Max();
        var cumulativeSpan = Math.Max(cumulativeMaximum - cumulativeMinimum, Math.Max(Math.Abs(cumulativeMaximum) * 0.01, 1));
        var cumulativePadding = cumulativeSpan * 0.2;
        _waterfallAxisMinimum = cumulativeMinimum - cumulativePadding;
        _waterfallAxisMaximum = cumulativeMaximum + cumulativePadding;

        var metrics = new[]
        {
            new RadarMetric("Capability", (Func<VisualizationPoint, double>)(point => point.AdjustedIntelligence), false),
            new RadarMetric("Success", point => point.Result.EffectiveSuccessRate, false),
            new RadarMetric("Safety", point => point.Result.CriticalFailureRate, true),
            new RadarMetric("Cost efficiency", point => point.TotalEconomicCostUsd, true),
            new RadarMetric("Speed", point => point.Result.ExpectedLatencySeconds, true),
            new RadarMetric("Expected value", point => point.ExpectedValueUsd, false)
        };
        var selectedRows = new List<RadarDatum>();
        var medianRows = new List<RadarDatum>();
        foreach (var metric in metrics)
        {
            var values = _points.Select(metric.Selector).Where(VisualizationChartData.IsFinite).Order().ToArray();
            var extent = VisualizationChartData.FiniteExtent(values, paddingFraction: 0);
            var selectedScore = VisualizationChartData.Normalize(metric.Selector(selected), extent.Minimum, extent.Maximum) * 100;
            var medianScore = VisualizationChartData.Normalize(Percentile(values, 0.5), extent.Minimum, extent.Maximum) * 100;
            if (metric.Invert)
            {
                selectedScore = 100 - selectedScore;
                medianScore = 100 - medianScore;
            }
            selectedRows.Add(new RadarDatum(metric.Name, selectedScore));
            medianRows.Add(new RadarDatum(metric.Name, medianScore));
        }
        _selectedRadar = selectedRows;
        _medianRadar = medianRows;
    }

    private void HandleSeriesClick(SeriesClickEventArgs args)
    {
        switch (args.Data)
        {
            case VisualizationPoint point:
                Select(point.Key);
                break;
            case CandleDatum candle:
                Select(candle.Key);
                break;
            case RangeDatum range:
                Select(range.Key);
                break;
            case BulletDatum bullet:
                Select(bullet.Key);
                break;
            case DrilldownDatum drilldown when drilldown.Key is null:
                _drilldownProvider = drilldown.Provider;
                BuildDrilldownData();
                break;
            case DrilldownDatum drilldown:
                Select(drilldown.Key);
                break;
            case ProviderDatum provider:
                ProviderFilter = provider.Provider;
                break;
            case ProviderShareDatum provider when !string.Equals(provider.Provider, "Other providers", StringComparison.Ordinal):
                ProviderFilter = provider.Provider;
                break;
        }
    }

    private void Select(string? key)
    {
        if (key is null || _points.All(point => point.Key != key))
        {
            return;
        }

        _selectedKey = key;
        BuildSelectedModelViews();
    }

    private void ExitDrilldown()
    {
        _drilldownProvider = null;
        BuildDrilldownData();
    }

    private static double Percentile(IReadOnlyList<double> sortedValues, double percentile)
    {
        if (sortedValues.Count == 0)
        {
            return 0;
        }

        var position = Math.Clamp(percentile, 0, 1) * (sortedValues.Count - 1);
        var lower = (int)Math.Floor(position);
        var upper = (int)Math.Ceiling(position);
        var fraction = position - lower;
        return sortedValues[lower] + (sortedValues[upper] - sortedValues[lower]) * fraction;
    }

    private static string ShortName(string value, int maximumLength) =>
        value.Length <= maximumLength ? value : value[..Math.Max(1, maximumLength - 1)] + "…";

    private static string CurrencyAxis(object value) =>
        TryDouble(value, out var number)
            ? CurrencyTick(number)
            : value?.ToString() ?? string.Empty;

    private static string PercentAxis(object value) =>
        TryDouble(value, out var number)
            ? $"{number:0}%"
            : value?.ToString() ?? string.Empty;

    private static string LogCurrencyAxis(object value) =>
        TryDouble(value, out var number)
            ? CurrencyTick(Math.Pow(10, number))
            : value?.ToString() ?? string.Empty;

    private string CandleCategoryAxis(object value)
    {
        if (!TryDouble(value, out var number))
        {
            return string.Empty;
        }

        var index = (int)Math.Round(number);
        return Math.Abs(number - index) < 0.000001 && index >= 0 && index < _candles.Count
            ? _candles[index].Model
            : string.Empty;
    }

    private static double DesirabilityScore(IReadOnlyList<double> sortedValues, double value, bool lowerIsBetter = false)
    {
        if (!VisualizationChartData.IsFinite(value) || sortedValues.Count == 0)
        {
            return 0.5;
        }

        if (sortedValues.Count == 1)
        {
            return 1;
        }

        var firstIndex = 0;
        while (firstIndex < sortedValues.Count && sortedValues[firstIndex] < value)
        {
            firstIndex++;
        }

        var lastIndex = firstIndex;
        while (lastIndex + 1 < sortedValues.Count && sortedValues[lastIndex + 1] <= value)
        {
            lastIndex++;
        }

        var percentile = ((firstIndex + lastIndex) / 2d) / (sortedValues.Count - 1);
        return lowerIsBetter ? 1 - percentile : percentile;
    }

    private static string HeatmapCellStyle(double? score)
    {
        if (score is null || !VisualizationChartData.IsFinite(score.Value))
        {
            return string.Empty;
        }

        var opacity = 0.14 + Math.Clamp(score.Value, 0, 1) * 0.74;
        return FormattableString.Invariant($"background-color: rgba(91, 129, 190, {opacity:0.###});");
    }

    private static string HeatmapCellTitle(double? score) =>
        score is null || !VisualizationChartData.IsFinite(score.Value)
            ? "No comparable data"
            : $"{score.Value:P0} relative desirability within the current filters";

    private static bool TryDouble(object? value, out double number)
    {
        if (value is string text)
        {
            return double.TryParse(text, NumberStyles.Float | NumberStyles.AllowThousands, CultureInfo.InvariantCulture, out number);
        }

        if (value is IConvertible convertible)
        {
            try
            {
                number = convertible.ToDouble(CultureInfo.InvariantCulture);
                return VisualizationChartData.IsFinite(number);
            }
            catch (FormatException)
            {
            }
            catch (InvalidCastException)
            {
            }
        }

        number = 0;
        return false;
    }

    private static string Number(double value, string format) =>
        VisualizationChartData.IsFinite(value) ? value.ToString(format, CultureInfo.CurrentCulture) : "n/a";

    private static string Percent(double value) =>
        VisualizationChartData.IsFinite(value) ? value.ToString("P1", CultureInfo.CurrentCulture) : "n/a";

    private static string PercentPrecise(double value) =>
        VisualizationChartData.IsFinite(value) ? value.ToString("P2", CultureInfo.CurrentCulture) : "n/a";

    private static string Currency(double value) =>
        VisualizationChartData.IsFinite(value) ? value.ToString("C0", CultureInfo.CurrentCulture) : "n/a";

    private static string HeatmapCurrency(double value) =>
        VisualizationChartData.IsFinite(value)
            ? value.ToString(Math.Abs(value) < 100 ? "C2" : "C1", CultureInfo.CurrentCulture)
            : "n/a";

    private static string CurrencyTick(double value)
    {
        if (!VisualizationChartData.IsFinite(value)) return "n/a";
        var sign = value < 0 ? "-" : string.Empty;
        var absolute = Math.Abs(value);
        return absolute >= 1_000_000
            ? $"{sign}${absolute / 1_000_000:0.#}m"
            : absolute >= 1000
                ? $"{sign}${absolute / 1000:0.#}k"
                : $"{sign}${absolute:0.#}";
    }

    private static string SelectionNote(VisualizationPoint point)
    {
        if (point.Result.IsEligible)
        {
            return string.IsNullOrWhiteSpace(point.Result.RecommendationReason)
                ? "This model clears the configured constraints."
                : point.Result.RecommendationReason;
        }

        return point.Result.ExclusionReasons.Count == 0
            ? "This model is excluded by the configured constraints."
            : string.Join(" ", point.Result.ExclusionReasons);
    }

    private enum ChartKind
    {
        ValueFrontier,
        CostValue,
        CostAnatomy,
        CostPareto,
        ProviderDrilldown,
        ValueCandlestick,
        SelectedWaterfall,
        ProviderBoxPlot,
        ModelRadar,
        ReliabilityRange,
        SuccessBullet,
        EligibilityFunnel,
        ProviderOpportunity,
        ValueHeatmap
    }

    private sealed record ChartOption(ChartKind Kind, string Label, string Description);
    private sealed record ProviderOption(string Name, int Count);
    private sealed record ProviderDatum(string Provider, int ModelCount, double AverageValue, double PositiveValue, double AverageSuccess);
    private sealed record ProviderPointSeries(string Provider, string Color, IReadOnlyList<VisualizationPoint> Points);
    private sealed record AttractiveRegionDatum(double LogCost, double MinimumIntelligence, double MaximumIntelligence);
    private sealed record DrilldownDatum(string Label, string Provider, string? Key, double Value, int ModelCount);
    private sealed record CandleDatum(string Key, int Index, string Model, double Low, double Open, double High, double Close);
    private sealed record WaterfallDatum(string Label, double Amount, bool IsSummary, double DisplayAmount);
    private sealed record BoxPlotDatum(string Provider, double Low, double Q1, double Median, double Q3, double High, double Mean, int Count);
    private sealed record RadarDatum(string Metric, double Score);
    private sealed record RadarMetric(string Name, Func<VisualizationPoint, double> Selector, bool Invert);
    private sealed record RangeDatum(string Key, string Model, double FirstAttempt, double Effective, double ExpectedAttempts)
    {
        public double Uplift => Math.Max(0, Effective - FirstAttempt);
    }
    private sealed record BulletDatum(string Key, string Model, double Success, double Target, double Maximum);
    private sealed record FunnelDatum(string Stage, int Count);
    private sealed record ProviderShareDatum(string Provider, double Value);
    private sealed record ModelHeatmapRow(
        int Rank,
        VisualizationPoint Point,
        double CapabilityScore,
        double SuccessScore,
        double ExpectedValueScore,
        double DirectCostScore,
        double? LatencyScore,
        double CriticalFailureScore,
        double SuccessPerDollarScore);
}
