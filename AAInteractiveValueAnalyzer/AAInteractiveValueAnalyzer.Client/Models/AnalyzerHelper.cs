using AAInteractiveValueAnalyzer.Client.Services;

namespace AAInteractiveValueAnalyzer.Client.Models;

public static class AnalyzerHelper
{
    public static HashSet<string> CreateDefaultColumns(IEnumerable<TableColumn> columns)
    {
        return columns.Where(column => column.IsDefaultVisible).Select(column => column.Key).ToHashSet(StringComparer.Ordinal);
    }

    public static bool IsColumnVisible(HashSet<string> visibleColumns, string key) => visibleColumns.Contains(key);

    public static bool CanHideColumn(HashSet<string> visibleColumns, string key) => visibleColumns.Count > 1 || !visibleColumns.Contains(key);
    public static readonly int[] AttemptOptions = [1, 2, 3, 4, 5];
    public static readonly Dictionary<string, FieldHelp> FieldHelpContent = new(StringComparer.Ordinal)
    {
        ["task-category"] = new FieldHelp(
            "Task category",
            "Selects the closest workload family and unlocks its preset defaults plus category-specific guidance.",
            "Changes the category prior, the default baseline inputs, and any category-specific adjustment or warning logic."),
        ["sensitivity"] = new FieldHelp(
            "Sensitivity",
            "Controls how sharply model success falls as difficulty rises.",
            "Sets tau in the success curve. Lower tau makes small difficulty changes matter more; higher tau smooths them out."),
        ["context"] = new FieldHelp(
            "Context",
            "Describes how much source material the model must handle and how noisy that material is.",
            "Applies a percent-of-base difficulty adjustment from the context table in the recommendation engine."),
        ["reasoning"] = new FieldHelp(
            "Reasoning",
            "Captures how much multi-step inference or planning the task requires.",
            "Applies a percent-of-base difficulty adjustment from the reasoning table."),
        ["domain"] = new FieldHelp(
            "Domain",
            "Represents how specialized the underlying knowledge has to be.",
            "Applies a percent-of-base difficulty adjustment from the domain-specificity table."),
        ["tool-use"] = new FieldHelp(
            "Tool use",
            "Models how much external tool orchestration the workload needs.",
            "Applies a percent-of-base difficulty adjustment and can trigger extra guidance for agentic workflows."),
        ["verifiability"] = new FieldHelp(
            "Verifiability",
            "Measures how easy it is to tell whether an answer is correct.",
            "Applies a percent-of-base difficulty adjustment from the verifiability table."),
        ["output"] = new FieldHelp(
            "Output",
            "Defines the strictness and risk of the deliverable the model must produce.",
            "Applies a percent-of-base difficulty adjustment and influences category-specific warnings."),
        ["max-attempts"] = new FieldHelp(
            "Max attempts",
            "Caps the number of model tries allowed for a task when retries are enabled.",
            "Raises effective success through repeated attempts, but also increases expected attempts and direct cost."),
        ["base-difficulty"] = new FieldHelp(
            "Base difficulty",
            "Sets the starting difficulty before workload and category adjustments are applied.",
            "Sets the workload's baseline on the 0-100 difficulty scale. Context, reasoning, domain, tool, verifiability, output, and category adjustments are percentages of this value."),
        ["deterministic-validation"] = new FieldHelp(
            "Deterministic validation",
            "Signals that outputs can be checked programmatically instead of only by human review.",
            $"Multiplies the modeled critical-failure rate by {RecommendationEngine.DeterministicValidationCriticalMultiplier:0.##}, reducing expected critical-failure cost and helping models satisfy the critical-failure threshold."),
        ["strict-structure"] = new FieldHelp(
            "Strict structure",
            "Requires extraction output to match a schema or rigid format.",
            $"When paired with deterministic validation for Extraction, multiplies critical-failure exposure by {RecommendationEngine.ExtractionStrictValidationCriticalMultiplier:0.##}."),
        ["silent-failure-risk"] = new FieldHelp(
            "Silent failure risk",
            "Captures tasks where wrong answers may look plausible and escape easy detection.",
            $"Multiplies the critical share of failures by {RecommendationEngine.SilentFailureCriticalShareMultiplier:0.##}, increasing expected critical-failure cost and tightening the effective risk constraint."),
        ["customer-facing"] = new FieldHelp(
            "Customer-facing",
            "Marks outputs that are directly visible to end users or customers.",
            $"Multiplies the critical share of failures by {RecommendationEngine.CustomerFacingCriticalShareMultiplier:0.##} to represent the greater exposure of errors delivered to customers."),
        ["human-approval"] = new FieldHelp(
            "Human approval",
            "Requires a person to approve risky actions before execution.",
            $"Multiplies the modeled critical-failure rate by {RecommendationEngine.HumanApprovalCriticalMultiplier:0.##}, reducing expected critical-failure cost and helping models satisfy the critical-failure threshold."),
        ["retries-allowed"] = new FieldHelp(
            "Retries allowed",
            "Determines whether the model can make more than one attempt.",
            "Enables the retry success model and the max-attempts input, which affect both success rate and direct cost."),
        ["required-success"] = new FieldHelp(
            "Required success",
            "Sets the minimum effective success rate a model must clear.",
            "Acts as a hard eligibility threshold. Models below it are excluded."),
        ["allowed-critical-failure"] = new FieldHelp(
            "Allowed critical failure",
            "Sets the maximum critical-failure rate the scenario can tolerate.",
            "Acts as a hard eligibility threshold. Models above it are excluded."),
        ["critical-share"] = new FieldHelp(
            "Critical share of failures",
            "Defines how much of overall failure probability counts as critical.",
            "Raises or lowers the modeled critical-failure rate without changing success probability."),
        ["aa-task-multiplier"] = new FieldHelp(
            "AA task multiplier",
            "Scales the Artificial Analysis cost before it is projected to a 1,000-task batch.",
            "Multiplies the model-cost input before expected model cost, direct cost, cost per 1,000 successful tasks, and expected value are computed."),
        ["value-per-success"] = new FieldHelp(
            "Value per good success",
            "Business value captured by a fully-correct success. A passing task that is only degraded-but-acceptable is worth the lower acceptable value instead, so this is the upper end of the per-success value range.",
            "Sets the full-value end of the partial-credit blend. Raises expected value through the share of passes that land in the good tier, which rises with how comfortably a model clears the task."),
        ["acceptable-value"] = new FieldHelp(
            "Acceptable value per success",
            "Business value captured by a degraded-but-acceptable pass -- usable output that is not fully correct, as is common in summarization, research, or extraction that erodes rather than fails cleanly. Defaults to half the good value.",
            "Sets the lower end of the partial-credit blend. Each success is worth a mix of the good and acceptable values weighted by the realized good-share, so raising this lifts the value of every model and narrows the gap between strong and marginal ones."),
        ["good-share"] = new FieldHelp(
            "Good outcome share",
            "The baseline percentage of passes that are fully correct rather than merely acceptable. The realized share per model is tilted around this by how far the model clears the difficulty bar, so comfortable models realize more good outcomes than marginal ones with the same pass rate.",
            "At 100 every pass is treated as fully good and value reduces to the flat value-per-success model. Below 100 it blends in the acceptable value and lets model headroom move the realized quality, which is what makes partial credit affect the ranking."),
        ["failure-cost"] = new FieldHelp(
            "Critical failure cost",
            "Economic loss assigned to a critical (genuinely harmful) failed task -- a wrong answer that reached a customer, an irreversible action, or a silent error that propagated. This is the expensive tail of failure.",
            "Charged against expected value at the modeled critical-failure rate, which every guardrail (silent-failure risk, deterministic validation, human approval, agentic exposure) acts on. Also drives the worst-case failure cost metric."),
        ["benign-failure-cost"] = new FieldHelp(
            "Benign failure cost",
            "Economic loss assigned to a non-critical failed task -- one that was caught and retried or otherwise thrown away cheaply. Defaults equal to critical failure cost; lower it to express that ordinary failures are inexpensive.",
            "Charged against expected value at the remaining (non-critical) failure rate. When it equals critical failure cost, total failure cost is the old single-term model; lowering it rewards models whose failures are mostly benign."),
        ["review-cost"] = new FieldHelp(
            "Review cost",
            "Human review cost applied to each task.",
            "Adds directly to expected direct cost for every task in the modeled 1,000-task batch."),
        ["retry-overhead"] = new FieldHelp(
            "Retry overhead",
            "Operational cost of each extra attempt beyond the first.",
            "Raises expected direct cost for the modeled 1,000-task batch as expected attempts increase."),
        ["monthly-volume"] = new FieldHelp(
            "Monthly volume",
            "Forecast number of 1,000-task batches run each month.",
            "Multiplies expected value per 1,000 tasks into monthly expected value."),
        ["latency-cost"] = new FieldHelp(
            "Latency cost per second",
            "The dollar value of one second of end-to-end wait per task. Set this for interactive or customer-facing work where a user is blocked while the model responds; leave it at 0 for batch work where nothing waits on any single task.",
            "Multiplied by each model's expected end-to-end latency and expected attempts, then subtracted from expected value. A slower model loses more value, and a model that retries waits more than once. Models with no published latency data are excluded while this is above 0 rather than treated as instant."),
        ["max-latency"] = new FieldHelp(
            "Maximum latency",
            "The longest acceptable end-to-end response time per task, in seconds. Leave blank for no limit.",
            "Acts as a hard eligibility threshold, like required success: a model whose expected latency exceeds this is excluded. Models with no published latency data are excluded while a limit is set, rather than passing the gate by default.")
    };

    public static readonly IReadOnlyList<TableColumn> RecommendationColumns =
    [
        new("success", "Success", true),
        new("ev", "EV/1k", true),
        new("direct", "Direct/1k", true),
        new("monthly", "Monthly EV", true),
        new("why", "Why", false),
        new("single", "1-attempt", false),
        new("critical", "Crit. fail", false),
        new("attempts", "Exp. tries", false),
        new("latency", "Latency", false),
        new("costsuccess", "Cost/1k success", false),
        new("successdollar", "Success/$", false)
    ];
    public static readonly IReadOnlyList<TableColumn> ComparisonColumns =
    [
        new("intel", "Capability", true),
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
        new("latency", "Latency", false),
        new("latcost", "Latency/1k", false),
        new("critfail", "Crit fail/1k", false),
        new("benignfail", "Benign fail/1k", false),
        new("costsuccess", "Cost/1k success", false),
        new("successdollar", "Success/$", false),
        new("monthly", "Monthly EV", false)
    ];
}
public sealed record TableColumn(string Key, string Label, bool IsDefaultVisible);
public sealed record FieldHelp(string Title, string Description, string CalculationImpact);
public sealed record SortState(string ColumnKey, bool Descending);
