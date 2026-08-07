using System.Globalization;
using System.Text;
using AAInteractiveValueAnalyzer.Client.Models;
using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.JSInterop;

namespace AAInteractiveValueAnalyzer.Client.Components;

public partial class AnalysisVisualizationDashboard
{
    private const double ValueLeft = 70;
    private const double ValueTop = 34;
    private const double ValueWidth = 902;
    private const double ValueHeight = 326;
    private const double OutcomeLeft = 72;
    private const double OutcomeTop = 34;
    private const double OutcomeWidth = 620;
    private const double OutcomeHeight = 416;
    private const double CostBarsLeft = 245;
    private const double CostBarsWidth = 420;

    private IReadOnlyList<VisualizationPoint> _allPoints = [];
    private IReadOnlyList<VisualizationPoint> _points = [];
    private IReadOnlyList<ProviderOption> _providers = [];
    private IReadOnlyList<VisualizationPoint> _keyboardPoints = [];
    private IReadOnlyList<VisualizationPoint> _costPoints = [];
    private IReadOnlyList<VisualizationPoint> _valueFrontier = [];
    private IReadOnlyList<VisualizationPoint> _costFrontier = [];
    private HashSet<string> _valueFrontierKeys = [];
    private HashSet<string> _costFrontierKeys = [];
    private IReadOnlyList<double> _valueXTicks = [];
    private IReadOnlyList<double> _valueYTicks = [];
    private IReadOnlyList<double> _costXTicks = [];
    private (double Minimum, double Maximum) _valueXExtent;
    private (double Minimum, double Maximum) _valueYExtent;
    private (double Minimum, double Maximum) _costXExtent;
    private double _maximumEconomicCost = 1;
    private string? _selectedKey;
    private string? _hoveredKey;
    private string? _rovingKey;
    private string _providerFilter = string.Empty;

    [Parameter, EditorRequired]
    public AnalysisSummary Summary { get; set; } = new();

    [Inject]
    private IJSRuntime JsRuntime { get; set; } = null!;

    private VisualizationPoint? SelectedPoint => _points.FirstOrDefault(point => point.Key == _selectedKey);
    private string? DisplayKey => _hoveredKey ?? _selectedKey;
    private string ProviderFilter
    {
        get => _providerFilter;
        set
        {
            var nextValue = value ?? string.Empty;
            if (string.Equals(_providerFilter, nextValue, StringComparison.Ordinal))
            {
                return;
            }

            _providerFilter = nextValue;
            RebuildChartState();
        }
    }
    private string ValueFrontierPath => BuildLinePath(_valueFrontier, point => ValueX(point.AdjustedIntelligence), point => ValueY(point.ExpectedValueUsd));
    private string CostFrontierPath => BuildStepPath(_costFrontier, point => OutcomeX(point.TotalEconomicCostUsd), point => OutcomeY(point.ExpectedValueUsd));
    private double CostChartHeight => Math.Max(88, 42 + _costPoints.Count * 28);

    protected override void OnParametersSet()
    {
        _allPoints = VisualizationChartData.CreatePoints(Summary);
        _providers = _allPoints
            .GroupBy(point => point.Result.Model.Provider, StringComparer.CurrentCultureIgnoreCase)
            .Select(group => new ProviderOption(group.Key, group.Count()))
            .OrderBy(provider => provider.Name, StringComparer.CurrentCultureIgnoreCase)
            .ToArray();
        if (!string.IsNullOrEmpty(_providerFilter)
            && _providers.All(provider => !string.Equals(provider.Name, _providerFilter, StringComparison.CurrentCultureIgnoreCase)))
        {
            _providerFilter = string.Empty;
        }

        RebuildChartState();
    }

    private void RebuildChartState()
    {
        _points = string.IsNullOrEmpty(_providerFilter)
            ? _allPoints
            : _allPoints
                .Where(point => string.Equals(
                    point.Result.Model.Provider,
                    _providerFilter,
                    StringComparison.CurrentCultureIgnoreCase))
                .ToArray();
        _keyboardPoints = _points
            .OrderBy(point => point.AdjustedIntelligence)
            .ThenBy(point => point.ExpectedValueUsd)
            .ThenBy(point => point.Key, StringComparer.Ordinal)
            .ToArray();
        _costPoints = _points
            .OrderBy(point => point.TotalEconomicCostUsd)
            .ThenByDescending(point => point.ExpectedValueUsd)
            .ThenBy(point => point.Key, StringComparer.Ordinal)
            .ToArray();
        _valueFrontier = VisualizationChartData.FindValueFrontier(_points);
        _costFrontier = VisualizationChartData.FindCostValueFrontier(_points);
        _valueFrontierKeys = _valueFrontier.Select(point => point.Key).ToHashSet(StringComparer.Ordinal);
        _costFrontierKeys = _costFrontier.Select(point => point.Key).ToHashSet(StringComparer.Ordinal);

        _valueXExtent = VisualizationChartData.FiniteExtent(_points.Select(point => point.AdjustedIntelligence), paddingFraction: 0.04);
        _valueYExtent = VisualizationChartData.FiniteExtent(_points.Select(point => point.ExpectedValueUsd), includeZero: true, paddingFraction: 0.08);
        var positiveCosts = _points.Select(point => point.TotalEconomicCostUsd).Where(cost => cost > 0).ToArray();
        _costXExtent = positiveCosts.Length == 0
            ? (1, 10)
            : (positiveCosts.Min() * 0.88, positiveCosts.Max() * 1.12);
        _maximumEconomicCost = Math.Max(1, _points.Select(point => point.TotalEconomicCostUsd).DefaultIfEmpty(1).Max());
        _valueXTicks = LinearTicks(_valueXExtent.Minimum, _valueXExtent.Maximum, 5);
        _valueYTicks = LinearTicks(_valueYExtent.Minimum, _valueYExtent.Maximum, 5);
        _costXTicks = LogTicks(_costXExtent.Minimum, _costXExtent.Maximum, 4);

        var preferred = Summary.BestExpectedValue
            ?? Summary.Results.Where(result => VisualizationChartData.IsFinite(result.ExpectedValuePerTaskUsd))
                .OrderByDescending(result => result.ExpectedValuePerTaskUsd)
                .FirstOrDefault();
        _selectedKey = VisualizationChartData.ResolveSelectionKey(_points, _selectedKey, preferred);
        if (_rovingKey is null || _keyboardPoints.All(point => point.Key != _rovingKey))
        {
            _rovingKey = _selectedKey;
        }
    }

    private double ValueX(double value) => ValueLeft + VisualizationChartData.Normalize(value, _valueXExtent.Minimum, _valueXExtent.Maximum) * ValueWidth;
    private double ValueY(double value) => ValueTop + (1 - VisualizationChartData.Normalize(value, _valueYExtent.Minimum, _valueYExtent.Maximum)) * ValueHeight;
    private double OutcomeX(double value) => OutcomeLeft + VisualizationChartData.NormalizeLog(value, _costXExtent.Minimum, _costXExtent.Maximum) * OutcomeWidth;
    private double OutcomeY(double value) => OutcomeTop + (1 - VisualizationChartData.Normalize(value, _valueYExtent.Minimum, _valueYExtent.Maximum)) * OutcomeHeight;
    private double CostBarX(double cumulativeValue) => CostBarsLeft + cumulativeValue / _maximumEconomicCost * CostBarsWidth;
    private double CostBarWidth(double value) => Math.Max(0, value / _maximumEconomicCost * CostBarsWidth);

    private IEnumerable<CostSegment> CostSegments(VisualizationPoint point)
    {
        yield return new(point.Costs.ModelCostUsd, "model-cost");
        yield return new(point.Costs.ReviewCostUsd, "review-cost");
        yield return new(point.Costs.RetryCostUsd, "retry-cost");
        if (point.Costs.LatencyCostUsd is { } latency)
        {
            yield return new(latency, "latency-cost");
        }
        yield return new(point.Costs.CriticalFailureCostUsd, "critical-cost");
        yield return new(point.Costs.BenignFailureCostUsd, "benign-cost");
    }

    private string PointClass(VisualizationPoint point, IReadOnlySet<string> frontierKeys)
    {
        var classes = new List<string> { "chart-point", point.Result.IsEligible ? "is-eligible" : "is-excluded" };
        if (frontierKeys.Contains(point.Key)) classes.Add("is-frontier");
        if (point.Key == _selectedKey) classes.Add("is-selected");
        if (point.Key == _hoveredKey) classes.Add("is-hovered");
        if (DisplayKey is not null && point.Key != DisplayKey) classes.Add("is-context-dimmed");
        return string.Join(' ', classes);
    }

    private string BarClass(VisualizationPoint point)
    {
        var classes = new List<string> { "cost-bar-row", point.Result.IsEligible ? "is-eligible" : "is-excluded" };
        if (point.Key == _selectedKey) classes.Add("is-selected");
        if (point.Key == _hoveredKey) classes.Add("is-hovered");
        if (DisplayKey is not null && point.Key != DisplayKey) classes.Add("is-context-dimmed");
        return string.Join(' ', classes);
    }

    private void Hover(string key) => _hoveredKey = key;

    private void ClearHover() => _hoveredKey = null;

    private void Select(string key)
    {
        _selectedKey = key;
        _rovingKey = key;
    }

    private void SetRovingKey(string key) => _rovingKey = key;

    private async Task HandlePointKeyDown(KeyboardEventArgs args, string key)
    {
        if (args.Key is "Enter" or " " or "Spacebar")
        {
            Select(key);
            return;
        }

        var currentIndex = _keyboardPoints.ToList().FindIndex(point => point.Key == key);
        if (currentIndex < 0)
        {
            return;
        }

        var targetIndex = args.Key switch
        {
            "ArrowRight" or "ArrowUp" => Math.Min(_keyboardPoints.Count - 1, currentIndex + 1),
            "ArrowLeft" or "ArrowDown" => Math.Max(0, currentIndex - 1),
            "Home" => 0,
            "End" => _keyboardPoints.Count - 1,
            _ => currentIndex
        };

        if (targetIndex == currentIndex)
        {
            return;
        }

        _rovingKey = _keyboardPoints[targetIndex].Key;
        await InvokeAsync(StateHasChanged);
        await JsRuntime.InvokeVoidAsync("aaInteractiveValueAnalyzer.focusElementById", PointElementId(_keyboardPoints[targetIndex]));
    }

    private string PointElementId(VisualizationPoint point)
    {
        var index = _keyboardPoints.ToList().FindIndex(candidate => candidate.Key == point.Key);
        return $"value-frontier-model-{Math.Max(0, index)}";
    }

    private bool ShouldLabel(VisualizationPoint point) =>
        point.Key == DisplayKey
        || (Summary.BestExpectedValue is not null && point.Key == VisualizationChartData.CreateKey(Summary.BestExpectedValue.Model));

    private string SelectionNote(VisualizationPoint point)
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

    private static string LatencyCostLabel(VisualizationPoint point) =>
        point.Costs.LatencyCostUsd is { } cost ? Currency(cost) : "n/a";

    private static string PointAriaLabel(VisualizationPoint point) =>
        $"{point.Result.Model.DisplayName}, {(point.Result.IsEligible ? "eligible" : "excluded")}, adjusted intelligence {Number(point.AdjustedIntelligence, "0.0")}, expected value {Currency(point.ExpectedValueUsd)} per one thousand tasks, economic cost {Currency(point.TotalEconomicCostUsd)}";

    private static string PointTooltip(VisualizationPoint point) =>
        $"{point.Result.Model.DisplayName}\n{(point.Result.IsEligible ? "Eligible" : "Excluded")} · IQ {Number(point.AdjustedIntelligence, "0.0")} · Success {Percent(point.Result.EffectiveSuccessRate)}\nEV {Currency(point.ExpectedValueUsd)} · Economic cost {Currency(point.TotalEconomicCostUsd)}";

    private static string CostTooltip(VisualizationPoint point) =>
        $"{point.Result.Model.DisplayName}\nTotal {Currency(point.TotalEconomicCostUsd)} · Model {Currency(point.Costs.ModelCostUsd)} · Review {Currency(point.Costs.ReviewCostUsd)} · Retry {Currency(point.Costs.RetryCostUsd)} · Latency {(point.Costs.LatencyCostUsd is { } latency ? Currency(latency) : "n/a")} · Critical {Currency(point.Costs.CriticalFailureCostUsd)} · Benign {Currency(point.Costs.BenignFailureCostUsd)}";

    private static IReadOnlyList<double> LinearTicks(double minimum, double maximum, int count) =>
        Enumerable.Range(0, count)
            .Select(index => minimum + (maximum - minimum) * index / Math.Max(1, count - 1))
            .ToArray();

    private static IReadOnlyList<double> LogTicks(double minimum, double maximum, int count)
    {
        var minLog = Math.Log10(Math.Max(minimum, 0.000001));
        var maxLog = Math.Log10(Math.Max(maximum, minimum));
        return Enumerable.Range(0, count)
            .Select(index => Math.Pow(10, minLog + (maxLog - minLog) * index / Math.Max(1, count - 1)))
            .ToArray();
    }

    private static string BuildLinePath(
        IReadOnlyList<VisualizationPoint> points,
        Func<VisualizationPoint, double> x,
        Func<VisualizationPoint, double> y) =>
        points.Count == 0
            ? string.Empty
            : string.Join(' ', points.Select((point, index) => $"{(index == 0 ? "M" : "L")} {F(x(point))} {F(y(point))}"));

    private static string BuildStepPath(
        IReadOnlyList<VisualizationPoint> points,
        Func<VisualizationPoint, double> x,
        Func<VisualizationPoint, double> y)
    {
        if (points.Count == 0)
        {
            return string.Empty;
        }

        var sb = new StringBuilder();
        sb.Append($"M {F(x(points[0]))} {F(y(points[0]))}");
        for (var index = 1; index < points.Count; index++)
        {
            sb.Append($" H {F(x(points[index]))} V {F(y(points[index]))}");
        }
        return sb.ToString();
    }

    private static string ShortModelName(string name, int maximumLength) =>
        name.Length <= maximumLength ? name : name[..Math.Max(1, maximumLength - 1)] + "…";

    private static string Number(double value, string format) =>
        VisualizationChartData.IsFinite(value) ? value.ToString(format, CultureInfo.CurrentCulture) : "n/a";

    private static string Percent(double value) =>
        VisualizationChartData.IsFinite(value) ? value.ToString("P1", CultureInfo.CurrentCulture) : "n/a";

    private static string Currency(double value) =>
        VisualizationChartData.IsFinite(value) ? value.ToString("C0", CultureInfo.CurrentCulture) : "n/a";

    private static string CurrencyTick(double value)
    {
        if (!VisualizationChartData.IsFinite(value)) return "n/a";
        var sign = value < 0 ? "-" : string.Empty;
        var absolute = Math.Abs(value);
        return absolute >= 1000
            ? $"{sign}${absolute / 1000:0.#}k"
            : absolute >= 100
                ? $"{sign}${absolute:0}"
                : $"{sign}${absolute:0.#}";
    }

    private static RenderFragment SvgText(string cssClass, double x, double y, string value) => builder =>
    {
        builder.OpenElement(0, "text");
        builder.AddAttribute(1, "class", cssClass);
        builder.AddAttribute(2, "x", F(x));
        builder.AddAttribute(3, "y", F(y));
        builder.AddAttribute(4, "style", SvgTextStyle(cssClass));
        builder.AddContent(5, value);
        builder.CloseElement();
    };

    private static string SvgTextStyle(string cssClass)
    {
        var fill = cssClass.Contains("break-even-label", StringComparison.Ordinal)
            ? "var(--deck-danger)"
            : cssClass.Contains("point-label", StringComparison.Ordinal)
                || cssClass.Contains("bar-model-label", StringComparison.Ordinal)
                || cssClass.Contains("bar-total-label", StringComparison.Ordinal)
                ? "var(--deck-text)"
                : "var(--deck-muted)";
        var textAnchor = cssClass.Contains("y-label", StringComparison.Ordinal)
            || cssClass.Contains("bar-total-label", StringComparison.Ordinal)
            ? "end"
            : cssClass.Contains("axis-label", StringComparison.Ordinal)
                ? "middle"
                : "start";
        var fontSize = cssClass.Contains("point-label", StringComparison.Ordinal)
            || cssClass.Contains("break-even-label", StringComparison.Ordinal)
            ? 10
            : 11;
        var weight = cssClass.Contains("point-label", StringComparison.Ordinal)
            || cssClass.Contains("bar-model-label", StringComparison.Ordinal)
            ? 650
            : 500;
        var outline = cssClass.Contains("point-label", StringComparison.Ordinal)
            ? "paint-order:stroke;stroke:var(--deck-bg);stroke-width:3px;stroke-linejoin:round;"
            : string.Empty;

        return $"fill:{fill};font-family:'SFMono-Regular',Consolas,'Liberation Mono',monospace;"
            + $"font-size:{fontSize}px;font-weight:{weight};text-anchor:{textAnchor};{outline}";
    }

    private static string F(double value) => value.ToString("0.###", CultureInfo.InvariantCulture);

    private sealed record CostSegment(double Value, string CssClass);
    private sealed record ProviderOption(string Name, int Count);
}
