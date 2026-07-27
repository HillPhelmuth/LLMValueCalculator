using System.Globalization;
using System.Text;
using AAInteractiveValueAnalyzer.Client.Models;
using AAInteractiveValueAnalyzer.Client.Services;
using Microsoft.AspNetCore.Components;
using Microsoft.JSInterop;
using static AAInteractiveValueAnalyzer.Client.Models.AnalyzerHelper;

namespace AAInteractiveValueAnalyzer.Client.Pages;

public partial class Analyzer
{
    private enum ComparisonEligibilityFilter
    {
        All,
        EligibleOnly,
        ExcludedOnly
    }

    private UseCaseInputs Inputs { get; set; } = new();

    private HashSet<string> VisibleRecommendationColumns { get; set; } = CreateDefaultColumns(RecommendationColumns);
    private HashSet<string> VisibleComparisonColumns { get; set; } = CreateDefaultColumns(ComparisonColumns);
    private SortState RecommendationSort { get; set; } = new("ev", true);
    private SortState ComparisonSort { get; set; } = new("ev", true);
    private string RecommendationFilterText { get; set; } = string.Empty;
    private string ComparisonFilterText { get; set; } = string.Empty;
    private ComparisonEligibilityFilter ComparisonFilter { get; set; }
    private RecommendationResult? ActiveModalResult { get; set; }
    private string ActiveModalTitle { get; set; } = string.Empty;
    private string? ActiveHelpKey { get; set; }
    private bool IsAnalyzing { get; set; }
    private string? AnalysisError { get; set; }
    private bool _analysisPending;

    [Inject]
    private IJSRuntime JsRuntime { get; set; } = null!;
    [Inject]
    private RecommendationEngine RecommendationEngine { get; set; } = null!;

    private AnalysisSummary Summary { get; set; } = new();

    private async Task Update()
    {
        _analysisPending = true;

        if (IsAnalyzing)
        {
            return;
        }

        IsAnalyzing = true;
        AnalysisError = null;

        try
        {
            while (_analysisPending)
            {
                _analysisPending = false;
                Summary = await RecommendationEngine.Analyze(Inputs);
            }
        }
        catch (Exception exception)
        {
            AnalysisError = $"The analysis could not be refreshed: {exception.Message}";
        }
        finally
        {
            IsAnalyzing = false;
        }
    }

    protected override async Task OnInitializedAsync()
    {
        await Update();
    }

    
    // The engine stores "no latency ceiling" as PositiveInfinity, which does not round-trip through a
    // number input. This proxy presents an empty box for "no limit" and treats 0 or blank the same
    // way, so the UI never has to render or parse infinity.
    private double? MaxLatencyInput
    {
        get => double.IsPositiveInfinity(Inputs.MaxAcceptableLatencySeconds)
            ? null
            : Inputs.MaxAcceptableLatencySeconds;
        set => Inputs.MaxAcceptableLatencySeconds = value is null or <= 0
            ? double.PositiveInfinity
            : value.Value;
    }

    private TaskCategoryProfile ActiveTaskCategoryProfile => RecommendationEngine.ResolveTaskCategoryProfile(Inputs.TaskCategory);
    private FieldHelp? ActiveFieldHelp =>
        ActiveHelpKey is not null && FieldHelpContent.TryGetValue(ActiveHelpKey, out var help)
            ? help
            : null;
    private IReadOnlyList<KeyValuePair<string, string>> ActiveCategoryPresetDetails => BuildCategoryPresetDetails(ActiveTaskCategoryProfile);
    private bool HasPendingCategoryChange => Inputs.TaskCategory != Inputs.LastAppliedTaskCategory;
    private string ActiveCategoryPresetSummary => BuildCategoryPresetSummary(ActiveTaskCategoryProfile);
    private string CategoryPresetPrompt => ActiveTaskCategoryProfile.HasPresetDefaults
        ? $"Apply recommended defaults? {ActiveCategoryPresetSummary}"
        : "This category does not have automatic defaults. Keep the current workload inputs or adjust them manually.";

    private int MaxRecommendations { get; set; } = 8;
    private async Task ResetDefaults()
    {
        Inputs = new UseCaseInputs();
        ActiveModalResult = null;
        ActiveModalTitle = string.Empty;
        ActiveHelpKey = null;
        VisibleRecommendationColumns = CreateDefaultColumns(RecommendationColumns);
        VisibleComparisonColumns = CreateDefaultColumns(ComparisonColumns);
        RecommendationSort = new("ev", true);
        ComparisonSort = new("ev", true);
        RecommendationFilterText = string.Empty;
        ComparisonFilterText = string.Empty;
        ComparisonFilter = ComparisonEligibilityFilter.All;
        await Update();
    }

    private async Task ApplyCategoryDefaults()
    {
        if (!ActiveTaskCategoryProfile.HasPresetDefaults)
        {
            Inputs.LastAppliedTaskCategory = Inputs.TaskCategory;
            return;
        }

        Inputs.ApplyCategoryDefaults(ActiveTaskCategoryProfile);
        await Update();
    }

    private void KeepCurrentInputs()
    {
        Inputs.LastAppliedTaskCategory = Inputs.TaskCategory;
    }

    

    private void ToggleColumn(HashSet<string> visibleColumns, string key, bool isChecked)
    {
        if (isChecked)
        {
            visibleColumns.Add(key);
            return;
        }

        if (visibleColumns.Count > 1)
        {
            visibleColumns.Remove(key);
        }
    }

    private void ToggleSort(bool isRecommendationTable, string columnKey)
    {
        var current = isRecommendationTable ? RecommendationSort : ComparisonSort;
        var next = current.ColumnKey == columnKey
            ? current with { Descending = !current.Descending }
            : new SortState(columnKey, GetDefaultSortDescending(columnKey));

        if (isRecommendationTable)
        {
            RecommendationSort = next;
        }
        else
        {
            ComparisonSort = next;
        }
    }

    private static bool GetDefaultSortDescending(string columnKey) => columnKey != "model";

    private static string GetSortIndicator(SortState sort, string columnKey)
    {
        if (sort.ColumnKey != columnKey)
        {
            return "";
        }

        return sort.Descending ? " ↓" : " ↑";
    }

    private string RecommendationSummaryText(AnalysisSummary summary)
    {
        var filteredCount = GetFilteredRecommendationRows(summary).Count();
        var baseText = $"{summary.EligibleResults.Count} eligible models at difficulty {summary.EffectiveDifficulty:0.0} using a {Inputs.DifficultySensitivity.DisplayName().ToLowerInvariant()} curve.";

        return string.IsNullOrWhiteSpace(RecommendationFilterText)
            ? baseText
            : $"{baseText} Showing {Math.Min(filteredCount, 8)} filtered results.";
    }

    private string ComparisonSummaryText(AnalysisSummary summary)
    {
        var filteredCount = GetFilteredComparisonRows(summary).Count();
        return $"Showing {filteredCount} of {summary.Results.Count} models with the current sort and filters.";
    }

    private IEnumerable<RecommendationResult> GetRecommendationRows(AnalysisSummary summary, int take = 8)
    {
        return SortRecommendationRows(GetFilteredRecommendationRows(summary)).Take(take);
    }

    private IEnumerable<RecommendationResult> GetFilteredRecommendationRows(AnalysisSummary summary)
    {
        var filter = RecommendationFilterText.Trim();
        if (string.IsNullOrWhiteSpace(filter))
        {
            return summary.EligibleResults;
        }

        return summary.EligibleResults.Where(item => MatchesFilter(item, filter));
    }

    private IEnumerable<RecommendationResult> GetComparisonRows(AnalysisSummary summary)
    {
        return SortComparisonRows(GetFilteredComparisonRows(summary));
    }

    private IEnumerable<RecommendationResult> GetFilteredComparisonRows(AnalysisSummary summary)
    {
        var items = summary.Results.AsEnumerable();

        items = ComparisonFilter switch
        {
            ComparisonEligibilityFilter.EligibleOnly => items.Where(item => item.IsEligible),
            ComparisonEligibilityFilter.ExcludedOnly => items.Where(item => !item.IsEligible),
            _ => items
        };

        var filter = ComparisonFilterText.Trim();
        if (string.IsNullOrWhiteSpace(filter))
        {
            return items;
        }

        return items.Where(item => MatchesFilter(item, filter));
    }

    private IEnumerable<RecommendationResult> SortRecommendationRows(IEnumerable<RecommendationResult> items)
    {
        return ApplySort(items, RecommendationSort, useEligibilityTieBreak: false);
    }

    private IEnumerable<RecommendationResult> SortComparisonRows(IEnumerable<RecommendationResult> items)
    {
        return ApplySort(items, ComparisonSort, useEligibilityTieBreak: true);
    }

    private static IEnumerable<RecommendationResult> ApplySort(IEnumerable<RecommendationResult> items, SortState sort, bool useEligibilityTieBreak)
    {
        IOrderedEnumerable<RecommendationResult> ordered = sort.ColumnKey switch
        {
            "model" => sort.Descending
                ? items.OrderByDescending(item => item.Model.DisplayName, StringComparer.CurrentCultureIgnoreCase)
                : items.OrderBy(item => item.Model.DisplayName, StringComparer.CurrentCultureIgnoreCase),
            "intel" => OrderBy(items, item => item.AdjustedIntelligence, sort.Descending),
            "aacost" => OrderBy(items, item => item.Model.CostPerAaTaskUsd ?? (sort.Descending ? double.NegativeInfinity : double.PositiveInfinity), sort.Descending),
            "success" => OrderBy(items, item => item.EffectiveSuccessRate, sort.Descending),
            "single" => OrderBy(items, item => item.SingleAttemptSuccessRate, sort.Descending),
            "critical" => OrderBy(items, item => item.CriticalFailureRate, sort.Descending),
            "attempts" => OrderBy(items, item => item.ExpectedAttempts, sort.Descending),
            "modelcost" => OrderBy(items, item => item.ExpectedModelCostUsd, sort.Descending),
            "review" => OrderBy(items, item => item.ExpectedReviewCostUsd, sort.Descending),
            "retry" => OrderBy(items, item => item.ExpectedRetryOverheadUsd, sort.Descending),
            "latency" => OrderBy(items, item => item.Model.HasLatencyData ? item.ExpectedLatencySeconds : (sort.Descending ? double.NegativeInfinity : double.PositiveInfinity), sort.Descending),
            "latcost" => OrderBy(items, item => item.Model.HasLatencyData ? item.ExpectedLatencyCostUsd : (sort.Descending ? double.NegativeInfinity : double.PositiveInfinity), sort.Descending),
            "critfail" => OrderBy(items, item => item.ExpectedCriticalFailureCostUsd, sort.Descending),
            "benignfail" => OrderBy(items, item => item.ExpectedBenignFailureCostUsd, sort.Descending),
            "direct" => OrderBy(items, item => item.ExpectedTotalDirectCostUsd, sort.Descending),
            "costsuccess" => OrderBy(items, item => item.CostPerSuccessfulTaskUsd, sort.Descending),
            "successdollar" => OrderBy(items, item => item.SuccessPerDollar, sort.Descending),
            "monthly" => OrderBy(items, item => item.MonthlyExpectedValueUsd, sort.Descending),
            "status" => sort.Descending
                ? items.OrderByDescending(item => item.IsEligible)
                : items.OrderBy(item => item.IsEligible),
            "why" => sort.Descending
                ? items.OrderByDescending(item => item.RecommendationReason, StringComparer.CurrentCultureIgnoreCase)
                : items.OrderBy(item => item.RecommendationReason, StringComparer.CurrentCultureIgnoreCase),
            _ => OrderBy(items, item => item.ExpectedValuePerTaskUsd, sort.Descending)
        };

        if (useEligibilityTieBreak && sort.ColumnKey != "status")
        {
            ordered = ordered.ThenByDescending(item => item.IsEligible);
        }

        return ordered
            .ThenByDescending(item => item.ExpectedValuePerTaskUsd)
            .ThenByDescending(item => item.EffectiveSuccessRate)
            .ThenBy(item => item.ExpectedTotalDirectCostUsd)
            .ThenBy(item => item.Model.DisplayName, StringComparer.CurrentCultureIgnoreCase);
    }

    private static IOrderedEnumerable<RecommendationResult> OrderBy<TKey>(IEnumerable<RecommendationResult> items, Func<RecommendationResult, TKey> keySelector, bool descending)
    {
        return descending ? items.OrderByDescending(keySelector) : items.OrderBy(keySelector);
    }

    private static bool MatchesFilter(RecommendationResult item, string filter)
    {
        return item.Model.DisplayName.Contains(filter, StringComparison.CurrentCultureIgnoreCase)
            || (!string.IsNullOrWhiteSpace(item.Model.Notes) && item.Model.Notes.Contains(filter, StringComparison.CurrentCultureIgnoreCase))
            || (!string.IsNullOrWhiteSpace(item.RecommendationReason) && item.RecommendationReason.Contains(filter, StringComparison.CurrentCultureIgnoreCase));
    }

    private void ShowAdditionalData(string title, RecommendationResult item)
    {
        ActiveHelpKey = null;
        ActiveModalTitle = title;
        ActiveModalResult = item;
    }

    private void ShowFieldHelp(string key)
    {
        ActiveModalResult = null;
        ActiveModalTitle = string.Empty;
        ActiveHelpKey = key;
    }

    private void CloseFieldHelp()
    {
        ActiveHelpKey = null;
    }

    private static string GetFieldHelpAriaLabel(string key)
    {
        if (!FieldHelpContent.TryGetValue(key, out var help))
        {
            return "Explain this field";
        }

        return $"Explain {help.Title}. {help.Description} Calculation impact: {help.CalculationImpact}";
    }

    private void ShowRecommendationData(RecommendationResult item)
    {
        ShowAdditionalData("Recommendation details", item);
    }

    private void ShowComparisonData(RecommendationResult item)
    {
        ShowAdditionalData("Comparison details", item);
    }

    private void CloseAdditionalData()
    {
        ActiveModalResult = null;
        ActiveModalTitle = string.Empty;
    }

    private async Task DownloadComparisonCsv()
    {
        var summary = await RecommendationEngine.Analyze(Inputs);
        var comparisonRows = GetComparisonRows(summary).ToList();
        if (comparisonRows.Count == 0)
        {
            return;
        }

        var csv = BuildComparisonCsv(comparisonRows);
        await JsRuntime.InvokeVoidAsync(
            "aaInteractiveValueAnalyzer.downloadTextFile",
            BuildComparisonCsvFileName(),
            csv,
            "text/csv;charset=utf-8");
    }

    private static string Percent(double value, int decimals = 1)
    {
        if (double.IsNaN(value) || double.IsInfinity(value))
        {
            return "n/a";
        }

        return value.ToString(decimals == 2 ? "P2" : "P1", CultureInfo.CurrentCulture);
    }

    private static string Currency(double value) => RecommendationEngine.FormatCurrency(value);

    private static string BatchCurrency(double? value)
    {
        return value.HasValue
            ? Currency(value.Value * RecommendationEngine.TaskBatchSize)
            : "n/a";
    }

    private static string LatencySeconds(RecommendationResult item)
    {
        return item.Model.HasLatencyData
            ? $"{item.ExpectedLatencySeconds.ToString("0.0", CultureInfo.CurrentCulture)}s"
            : "n/a";
    }

    private static string LatencyCost(RecommendationResult item)
    {
        return item.Model.HasLatencyData
            ? Currency(item.ExpectedLatencyCostUsd)
            : "n/a";
    }

    private static string Number(double value, string format = "0.##")
    {
        if (double.IsNaN(value) || double.IsInfinity(value))
        {
            return "n/a";
        }

        return value.ToString(format, CultureInfo.CurrentCulture);
    }

    private static string Width(double value)
    {
        if (double.IsNaN(value) || double.IsInfinity(value))
        {
            return "0%";
        }

        return $"{Math.Clamp(value, 0, 1) * 100:0.##}%";
    }

    private string BuildComparisonCsv(IReadOnlyList<RecommendationResult> items)
    {
        var columns = ComparisonColumns.Where(column => VisibleComparisonColumns.Contains(column.Key)).ToList();
        var csv = new StringBuilder();

        csv.Append(EscapeCsv("Model"));
        foreach (var column in columns)
        {
            csv.Append(',').Append(EscapeCsv(column.Label));
        }

        csv.AppendLine();

        foreach (var item in items)
        {
            csv.Append(EscapeCsv(item.Model.DisplayName));
            foreach (var column in columns)
            {
                csv.Append(',').Append(EscapeCsv(GetComparisonCsvValue(column.Key, item)));
            }

            csv.AppendLine();
        }

        return csv.ToString();
    }

    private string BuildComparisonCsvFileName()
    {
        return $"model-ev-calculator-full-comparison-{DateTimeOffset.Now:yyyyMMdd-HHmmss}.csv";
    }

    private static string EscapeCsv(string value)
    {
        return $"\"{value.Replace("\"", "\"\"")}\"";
    }

    private static string GetComparisonCsvValue(string columnKey, RecommendationResult item)
    {
        return columnKey switch
        {
            "intel" => $"{item.AdjustedIntelligence:0.0} ({item.CapabilityIndexName})",
            "aacost" => BatchCurrency(item.Model.CostPerAaTaskUsd),
            "success" => Percent(item.EffectiveSuccessRate),
            "single" => Percent(item.SingleAttemptSuccessRate),
            "critical" => Percent(item.CriticalFailureRate, 2),
            "attempts" => Number(item.ExpectedAttempts, "0.00"),
            "modelcost" => Currency(item.ExpectedModelCostUsd),
            "review" => Currency(item.ExpectedReviewCostUsd),
            "retry" => Currency(item.ExpectedRetryOverheadUsd),
            "latency" => LatencySeconds(item),
            "latcost" => LatencyCost(item),
            "critfail" => item.Model.HasCostData ? Currency(item.ExpectedCriticalFailureCostUsd) : "n/a",
            "benignfail" => item.Model.HasCostData ? Currency(item.ExpectedBenignFailureCostUsd) : "n/a",
            "direct" => Currency(item.ExpectedTotalDirectCostUsd),
            "costsuccess" => Currency(item.CostPerSuccessfulTaskUsd),
            "successdollar" => Number(item.SuccessPerDollar, "0.00"),
            "ev" => Currency(item.ExpectedValuePerTaskUsd),
            "monthly" => Currency(item.MonthlyExpectedValueUsd),
            "status" => item.IsEligible ? "Eligible" : "Excluded",
            _ => string.Empty
        };
    }

    private static string BuildCategoryPresetSummary(TaskCategoryProfile profile)
    {
        if (!profile.HasPresetDefaults)
        {
            return "No category preset is defined for this option.";
        }

        var parts = new List<string>();

        if (profile.DefaultBaseDifficulty is { } baseDifficulty)
        {
            parts.Add($"Difficulty: {baseDifficulty:0.0}");
        }

        if (profile.DefaultContextRequirement is { } context)
        {
            parts.Add($"Context: {context.DisplayName()}");
        }

        if (profile.DefaultReasoningDepth is { } reasoning)
        {
            parts.Add($"Reasoning: {reasoning.DisplayName()}");
        }

        if (profile.DefaultToolUse is { } toolUse)
        {
            parts.Add($"Tool use: {toolUse.DisplayName()}");
        }

        if (profile.DefaultVerifiability is { } verifiability)
        {
            parts.Add($"Verifiability: {verifiability.DisplayName()}");
        }

        if (profile.DefaultOutputConstraint is { } output)
        {
            parts.Add($"Output: {output.DisplayName()}");
        }

        return string.Join(". ", parts) + ".";
    }

    private static IReadOnlyList<KeyValuePair<string, string>> BuildCategoryPresetDetails(TaskCategoryProfile profile)
    {
        if (!profile.HasPresetDefaults)
        {
            return [];
        }

        var details = new List<KeyValuePair<string, string>>();

        if (profile.DefaultBaseDifficulty is { } baseDifficulty)
        {
            details.Add(new("Difficulty", baseDifficulty.ToString("0.0", CultureInfo.CurrentCulture)));
        }

        AddPresetDetail(details, "Context", profile.DefaultContextRequirement);
        AddPresetDetail(details, "Reasoning", profile.DefaultReasoningDepth);
        AddPresetDetail(details, "Domain", profile.DefaultDomainSpecificity);
        AddPresetDetail(details, "Tool use", profile.DefaultToolUse);
        AddPresetDetail(details, "Verifiability", profile.DefaultVerifiability);
        AddPresetDetail(details, "Output", profile.DefaultOutputConstraint);
        AddPresetFlag(details, "Validation", profile.DefaultHasDeterministicValidation, "Deterministic", "Manual");
        if (profile.Category == TaskCategoryOption.Extraction)
        {
            AddPresetFlag(details, "Structured", profile.DefaultRequiresStrictStructuredOutput, "Strict", "Flexible");
        }
        AddPresetFlag(details, "Silent risk", profile.DefaultHasSilentFailureRisk, "High", "Lower");
        AddPresetFlag(details, "Customer-facing", profile.DefaultCustomerFacing, "Yes", "No");
        AddPresetFlag(details, "Approval", profile.DefaultHumanApprovalForHighRiskActions, "Required", "Optional");
        AddPresetFlag(details, "Retries", profile.DefaultRetriesAllowed, "Allowed", "Off");

        if (profile.DefaultMaxAttempts is { } maxAttempts)
        {
            details.Add(new("Max attempts", maxAttempts.ToString(CultureInfo.CurrentCulture)));
        }

        return details;
    }

    private static void AddPresetDetail<TEnum>(ICollection<KeyValuePair<string, string>> details, string label, TEnum? value)
        where TEnum : struct, Enum
    {
        if (value.HasValue)
        {
            details.Add(new(label, value.Value.DisplayName()));
        }
    }

    private static void AddPresetFlag(ICollection<KeyValuePair<string, string>> details, string label, bool? value, string trueLabel, string falseLabel)
    {
        if (value.HasValue)
        {
            details.Add(new(label, value.Value ? trueLabel : falseLabel));
        }
    }

    private static IReadOnlyList<string> GetRecommendationTags(AnalysisSummary summary, RecommendationResult item)
    {
        var tags = new List<string>();

        if (ReferenceEquals(item, summary.BestExpectedValue))
        {
            tags.Add("Best EV");
        }

        if (ReferenceEquals(item, summary.CheapestEligible))
        {
            tags.Add("Cheapest");
        }

        if (ReferenceEquals(item, summary.HighestQualityEligible))
        {
            tags.Add("Best quality");
        }

        if (ReferenceEquals(item, summary.BestSuccessPerDollar))
        {
            tags.Add("Best success/$");
        }

        return tags;
    }

    private static string BuildCostBreakdown(RecommendationResult item)
    {
        return $"Model/1k {Currency(item.ExpectedModelCostUsd)} | Review/1k {Currency(item.ExpectedReviewCostUsd)} | Retry/1k {Currency(item.ExpectedRetryOverheadUsd)}";
    }

    private static IReadOnlyList<KeyValuePair<string, string>> BuildAdditionalMetrics(RecommendationResult item)
    {
        return
        [
            new("Capability basis", $"{item.CapabilityIndexName}: {item.RawCapabilityScore:0.0}"),
            new("Single-attempt success", Percent(item.SingleAttemptSuccessRate)),
            new("Effective success", Percent(item.EffectiveSuccessRate)),
            new("Realized good-outcome share", Percent(item.RealizedGoodOutcomeShare)),
            new("Blended value / success", Currency(item.BlendedValuePerSuccessUsd)),
            new("Critical-failure rate", Percent(item.CriticalFailureRate, 2)),
            new("Expected attempts", Number(item.ExpectedAttempts, "0.00")),
            new("Expected model cost / 1k tasks", Currency(item.ExpectedModelCostUsd)),
            new("Expected review cost / 1k tasks", Currency(item.ExpectedReviewCostUsd)),
            new("Expected retry overhead / 1k tasks", Currency(item.ExpectedRetryOverheadUsd)),
            new("Expected direct cost / 1k tasks", Currency(item.ExpectedTotalDirectCostUsd)),
            new("Expected latency / task", LatencySeconds(item)),
            new("Expected latency cost / 1k tasks", LatencyCost(item)),
            new("Expected critical-failure cost / 1k tasks", item.Model.HasCostData ? Currency(item.ExpectedCriticalFailureCostUsd) : "n/a"),
            new("Expected benign-failure cost / 1k tasks", item.Model.HasCostData ? Currency(item.ExpectedBenignFailureCostUsd) : "n/a"),
            new("Worst-case (critical) failure cost / 1k tasks", item.Model.HasCostData ? Currency(item.WorstCaseFailureCostUsd) : "n/a"),
            new("Cost per 1k successful tasks", Currency(item.CostPerSuccessfulTaskUsd)),
            new("Success per dollar", Number(item.SuccessPerDollar, "0.00")),
            new("Expected value / 1k tasks", Currency(item.ExpectedValuePerTaskUsd)),
            new("Monthly expected value", Currency(item.MonthlyExpectedValueUsd))
        ];
    }
}
