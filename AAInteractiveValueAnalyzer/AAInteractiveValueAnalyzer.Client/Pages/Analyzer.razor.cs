using System.Globalization;
using System.IO;
using System.Text;
using AAInteractiveValueAnalyzer.Client.Models;
using AAInteractiveValueAnalyzer.Client.Services;
using Microsoft.AspNetCore.Components;
using Microsoft.JSInterop;

namespace AAInteractiveValueAnalyzer.Client.Pages;

public partial class Analyzer
{
    private sealed record TableColumn(string Key, string Label, bool IsDefaultVisible);
    private sealed record FieldHelp(string Title, string Description, string CalculationImpact);
    private sealed record SortState(string ColumnKey, bool Descending);
    private enum ComparisonEligibilityFilter
    {
        All,
        EligibleOnly,
        ExcludedOnly
    }

    private UseCaseInputs Inputs { get; set; } = new();
    private static readonly int[] AttemptOptions = [1, 2, 3, 4, 5];
    private static readonly IReadOnlyList<TableColumn> RecommendationColumns =
    [
        new("success", "Success", true),
        new("ev", "EV/1k", true),
        new("direct", "Direct/1k", true),
        new("monthly", "Monthly EV", true),
        new("why", "Why", false),
        new("single", "1-attempt", false),
        new("critical", "Crit. fail", false),
        new("attempts", "Exp. tries", false),
        new("costsuccess", "Cost/1k success", false),
        new("successdollar", "Success/$", false)
    ];
    private static readonly IReadOnlyList<TableColumn> ComparisonColumns =
    [
        new("intel", "Adj IQ", true),
        new("aacost", "AA/1k", true),
        new("success", "Success", true),
        new("direct", "Direct/1k", true),
        new("ev", "EV/1k", true),
        new("status", "Status", true),
        new("single", "1-attempt", false),
        new("critical", "Crit. fail", false),
        new("attempts", "Exp. tries", false),
        new("modelcost", "Model/1k", false),
        new("review", "Review/1k", false),
        new("retry", "Retry/1k", false),
        new("costsuccess", "Cost/1k success", false),
        new("successdollar", "Success/$", false),
        new("monthly", "Monthly EV", false)
    ];

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

    [Inject]
    private IJSRuntime JsRuntime { get; set; } = null!;

    private static readonly IReadOnlyDictionary<string, FieldHelp> FieldHelpContent = new Dictionary<string, FieldHelp>(StringComparer.Ordinal)
    {
        ["use-case-name"] = new(
            "Use-case name",
            "Names the current scenario so the recommendation and audit sections stay anchored to the workload you are modeling.",
            "No direct calculation effect. This value is presentation-only."),
        ["task-category"] = new(
            "Task category",
            "Selects the closest workload family and unlocks its preset defaults plus category-specific guidance.",
            "Changes the category prior, the default baseline inputs, and any category-specific adjustment or warning logic."),
        ["sensitivity"] = new(
            "Sensitivity",
            "Controls how sharply model success falls as difficulty rises.",
            "Sets tau in the success curve. Lower tau makes small difficulty changes matter more; higher tau smooths them out."),
        ["context"] = new(
            "Context",
            "Describes how much source material the model must handle and how noisy that material is.",
            "Applies a percent-of-base difficulty adjustment from the context table in the recommendation engine."),
        ["reasoning"] = new(
            "Reasoning",
            "Captures how much multi-step inference or planning the task requires.",
            "Applies a percent-of-base difficulty adjustment from the reasoning table."),
        ["domain"] = new(
            "Domain",
            "Represents how specialized the underlying knowledge has to be.",
            "Applies a percent-of-base difficulty adjustment from the domain-specificity table."),
        ["tool-use"] = new(
            "Tool use",
            "Models how much external tool orchestration the workload needs.",
            "Applies a percent-of-base difficulty adjustment and can trigger extra guidance for agentic workflows."),
        ["verifiability"] = new(
            "Verifiability",
            "Measures how easy it is to tell whether an answer is correct.",
            "Applies a percent-of-base difficulty adjustment and affects some grounding-related advice."),
        ["output"] = new(
            "Output",
            "Defines the strictness and risk of the deliverable the model must produce.",
            "Applies a percent-of-base difficulty adjustment and influences category-specific warnings."),
        ["eval-set-size"] = new(
            "Eval set size",
            "Counts the representative examples available for this use case.",
            "Does not change the score directly. It changes the guardrail note when a representative eval set is enabled."),
        ["max-attempts"] = new(
            "Max attempts",
            "Caps the number of model tries allowed for a task when retries are enabled.",
            "Raises effective success through repeated attempts, but also increases expected attempts and direct cost."),
        ["base-difficulty"] = new(
            "Base difficulty",
            "Sets the starting difficulty before workload modifiers and guardrails are applied.",
            "Most workload and guardrail adjustments are now percentages of this normalized base value."),
        ["representative-eval-set"] = new(
            "Representative eval set",
            "Indicates whether you have labeled examples that reflect the real workload.",
            "No direct difficulty change. It changes audit guidance and some category warnings."),
        ["deterministic-validation"] = new(
            "Deterministic validation",
            "Signals that outputs can be checked programmatically instead of only by human review.",
            $"Lowers effective difficulty by {Math.Abs(RecommendationEngine.DeterministicValidationPercent):0}% of normalized base difficulty and also reduces modeled critical-failure exposure."),
        ["rag-domain-context"] = new(
            "RAG / domain context",
            "Signals that grounded or retrieved context is supplied to the model.",
            $"Lowers effective difficulty by {Math.Abs(RecommendationEngine.RagOrDomainContextPercent):0}% of normalized base difficulty and reduces some grounding-related risk."),
        ["strict-structure"] = new(
            "Strict structure",
            "Requires the output to match a schema or rigid format.",
            $"Raises difficulty by {RecommendationEngine.StrictStructuredOutputPercent:0}% of normalized base difficulty and can improve extraction safety when paired with validation."),
        ["silent-failure-risk"] = new(
            "Silent failure risk",
            "Captures tasks where wrong answers may look plausible and escape easy detection.",
            $"Raises difficulty by {RecommendationEngine.SilentFailureRiskPercent:0}% of normalized base difficulty and increases the modeled critical-failure share."),
        ["customer-facing"] = new(
            "Customer-facing",
            "Marks outputs that are directly visible to end users or customers.",
            $"Raises difficulty by {RecommendationEngine.CustomerFacingPercent:0}% of normalized base difficulty because presentation and trust costs matter more."),
        ["human-approval"] = new(
            "Human approval",
            "Requires a person to approve risky actions before execution.",
            "No direct difficulty change. It reduces modeled critical-failure exposure for high-risk actions."),
        ["retries-allowed"] = new(
            "Retries allowed",
            "Determines whether the model can make more than one attempt.",
            "Enables the retry success model and the max-attempts input, which affect both success rate and direct cost."),
        ["required-success"] = new(
            "Required success",
            "Sets the minimum effective success rate a model must clear.",
            "Acts as a hard eligibility threshold. Models below it are excluded."),
        ["allowed-critical-failure"] = new(
            "Allowed critical failure",
            "Sets the maximum critical-failure rate the scenario can tolerate.",
            "Acts as a hard eligibility threshold. Models above it are excluded."),
        ["critical-share"] = new(
            "Critical share of failures",
            "Defines how much of overall failure probability counts as critical.",
            "Raises or lowers the modeled critical-failure rate without changing success probability."),
        ["aa-task-multiplier"] = new(
            "AA task multiplier",
            "Scales the Artificial Analysis cost before it is projected to a 1,000-task batch.",
            "Multiplies the model-cost input before expected model cost, direct cost, cost per 1,000 successful tasks, and expected value are computed."),
        ["value-per-success"] = new(
            "Value per success",
            "Business value captured when one task succeeds.",
            "Raises expected value linearly with effective success."),
        ["failure-cost"] = new(
            "Failure cost",
            "Economic loss assigned to a failed task.",
            "Reduces expected value in proportion to failure probability."),
        ["review-cost"] = new(
            "Review cost",
            "Human review cost applied to each task.",
            "Adds directly to expected direct cost for every task in the modeled 1,000-task batch."),
        ["retry-overhead"] = new(
            "Retry overhead",
            "Operational cost of each extra attempt beyond the first.",
            "Raises expected direct cost for the modeled 1,000-task batch as expected attempts increase."),
        ["monthly-volume"] = new(
            "Monthly volume",
            "Forecast number of 1,000-task batches run each month.",
            "Multiplies expected value per 1,000 tasks into monthly expected value.")
    };

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

    private void ResetDefaults()
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
    }

    private void ApplyCategoryDefaults()
    {
        if (!ActiveTaskCategoryProfile.HasPresetDefaults)
        {
            Inputs.LastAppliedTaskCategory = Inputs.TaskCategory;
            return;
        }

        Inputs.ApplyCategoryDefaults(ActiveTaskCategoryProfile);
    }

    private void KeepCurrentInputs()
    {
        Inputs.LastAppliedTaskCategory = Inputs.TaskCategory;
    }

    private static HashSet<string> CreateDefaultColumns(IEnumerable<TableColumn> columns)
    {
        return columns.Where(column => column.IsDefaultVisible).Select(column => column.Key).ToHashSet(StringComparer.Ordinal);
    }

    private static bool IsColumnVisible(HashSet<string> visibleColumns, string key) => visibleColumns.Contains(key);

    private static bool CanHideColumn(HashSet<string> visibleColumns, string key) => visibleColumns.Count > 1 || !visibleColumns.Contains(key);

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

    private IEnumerable<RecommendationResult> GetRecommendationRows(AnalysisSummary summary)
    {
        return SortRecommendationRows(GetFilteredRecommendationRows(summary)).Take(8);
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
            "intel" => OrderBy(items, item => item.Model.AdjustedIntelligence, sort.Descending),
            "aacost" => OrderBy(items, item => item.Model.CostPerAaTaskUsd ?? double.MaxValue, sort.Descending),
            "success" => OrderBy(items, item => item.EffectiveSuccessRate, sort.Descending),
            "single" => OrderBy(items, item => item.SingleAttemptSuccessRate, sort.Descending),
            "critical" => OrderBy(items, item => item.CriticalFailureRate, sort.Descending),
            "attempts" => OrderBy(items, item => item.ExpectedAttempts, sort.Descending),
            "modelcost" => OrderBy(items, item => item.ExpectedModelCostUsd, sort.Descending),
            "review" => OrderBy(items, item => item.ExpectedReviewCostUsd, sort.Descending),
            "retry" => OrderBy(items, item => item.ExpectedRetryOverheadUsd, sort.Descending),
            "direct" => OrderBy(items, item => item.ExpectedTotalDirectCostUsd, sort.Descending),
            "costsuccess" => OrderBy(items, item => item.CostPerSuccessfulTaskUsd, sort.Descending),
            "successdollar" => OrderBy(items, item => item.SuccessPerDollar, sort.Descending),
            "monthly" => OrderBy(items, item => item.MonthlyExpectedValueUsd, sort.Descending),
            "status" => sort.Descending
                ? items.OrderByDescending(item => item.IsEligible)
                : items.OrderBy(item => item.IsEligible),
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
        var summary = RecommendationEngine.Analyze(Inputs);
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
        var baseName = string.IsNullOrWhiteSpace(Inputs.UseCaseName)
            ? "model-ev-calculator"
            : Inputs.UseCaseName.Trim();

        var sanitized = new string(baseName
            .Select(character => Path.GetInvalidFileNameChars().Contains(character) ? '-' : character)
            .ToArray())
            .Replace(' ', '-');

        return $"{sanitized.ToLowerInvariant()}-full-comparison-{DateTime.Now:yyyyMMdd-HHmmss}.csv";
    }

    private static string EscapeCsv(string value)
    {
        return $"\"{value.Replace("\"", "\"\"")}\"";
    }

    private static string GetComparisonCsvValue(string columnKey, RecommendationResult item)
    {
        return columnKey switch
        {
            "intel" => $"{item.Model.AdjustedIntelligence:0} (Raw {item.Model.IntelligenceIndex:0})",
            "aacost" => BatchCurrency(item.Model.CostPerAaTaskUsd),
            "success" => Percent(item.EffectiveSuccessRate),
            "single" => Percent(item.SingleAttemptSuccessRate),
            "critical" => Percent(item.CriticalFailureRate, 2),
            "attempts" => Number(item.ExpectedAttempts, "0.00"),
            "modelcost" => Currency(item.ExpectedModelCostUsd),
            "review" => Currency(item.ExpectedReviewCostUsd),
            "retry" => Currency(item.ExpectedRetryOverheadUsd),
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
        AddPresetFlag(details, "Eval set", profile.DefaultHasRepresentativeEvalSet, "Representative", "Not expected");
        AddPresetFlag(details, "Validation", profile.DefaultHasDeterministicValidation, "Deterministic", "Manual");
        AddPresetFlag(details, "Grounding", profile.DefaultHasRagOrDomainContext, "RAG/context", "None");
        AddPresetFlag(details, "Structured", profile.DefaultRequiresStrictStructuredOutput, "Strict", "Flexible");
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
            new("Single-attempt success", Percent(item.SingleAttemptSuccessRate)),
            new("Effective success", Percent(item.EffectiveSuccessRate)),
            new("Critical-failure rate", Percent(item.CriticalFailureRate, 2)),
            new("Expected attempts", Number(item.ExpectedAttempts, "0.00")),
            new("Expected model cost / 1k tasks", Currency(item.ExpectedModelCostUsd)),
            new("Expected review cost / 1k tasks", Currency(item.ExpectedReviewCostUsd)),
            new("Expected retry overhead / 1k tasks", Currency(item.ExpectedRetryOverheadUsd)),
            new("Expected direct cost / 1k tasks", Currency(item.ExpectedTotalDirectCostUsd)),
            new("Cost per 1k successful tasks", Currency(item.CostPerSuccessfulTaskUsd)),
            new("Success per dollar", Number(item.SuccessPerDollar, "0.00")),
            new("Expected value / 1k tasks", Currency(item.ExpectedValuePerTaskUsd)),
            new("Monthly expected value", Currency(item.MonthlyExpectedValueUsd))
        ];
    }
}