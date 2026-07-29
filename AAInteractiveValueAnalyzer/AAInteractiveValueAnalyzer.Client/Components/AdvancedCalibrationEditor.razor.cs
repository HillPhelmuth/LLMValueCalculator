using System.Globalization;
using AAInteractiveValueAnalyzer.Client.Models;
using Microsoft.AspNetCore.Components;

namespace AAInteractiveValueAnalyzer.Client.Components;

public partial class AdvancedCalibrationEditor
{
    private static readonly IReadOnlyList<(string Key, string Label, string Help)> TauFields =
    [
        ("soft", "Soft tau", "A gradual capability-to-success transition for soft sensitivity."),
        ("normal", "Normal tau", "The standard transition used for normal sensitivity."),
        ("sharp", "Sharp tau", "A steeper transition used for sharp sensitivity.")
    ];

    private static readonly IReadOnlyList<(string Key, string Label, string Help)> RiskFields =
    [
        ("customer_facing_critical_share_multiplier", "Customer-facing critical share", "Raises the share of failures treated as critical for customer-facing work."),
        ("silent_failure_critical_share_multiplier", "Silent-failure critical share", "Raises the critical share when failures can pass unnoticed."),
        ("deterministic_validation_critical_multiplier", "Deterministic validation", "Reduces modeled critical failures when validation can catch them."),
        ("human_approval_critical_multiplier", "Human approval", "Reduces modeled critical failures when a person approves high-risk actions."),
        ("extraction_strict_validation_critical_multiplier", "Strict extraction validation", "Further reduces critical exposure for validated, structured extraction."),
        ("quality_share_difficulty_tilt", "Quality-share difficulty tilt", "Changes the share of successful outcomes considered fully good as capability rises."),
        ("critical_share_difficulty_tilt", "Critical-share difficulty tilt", "Changes the critical share of failures as capability rises."),
        ("retry_correlation_decay", "Retry correlation decay", "Controls how independent repeated attempts are; lower values make retries more independent.")
    ];

    private List<double> CurveBreakpoints { get; set; } = [];
    private List<double> CurveSlopes { get; set; } = [];
    private Dictionary<string, double> Tau { get; set; } = new(StringComparer.Ordinal);
    private double ErrorFloor { get; set; }
    private Dictionary<string, double> RiskMultipliers { get; set; } = new(StringComparer.Ordinal);
    private string? StatusMessage { get; set; }
    private bool IsLoaded { get; set; }

    [Parameter]
    public bool IsOpen { get; set; }

    [Parameter]
    public EventCallback OnClose { get; set; }

    [Parameter]
    public EventCallback OnCalibrationChanged { get; set; }

    private IReadOnlyDictionary<string, string> Errors => IsLoaded ? BuildDraft().Validate() : new Dictionary<string, string>();

    protected override async Task OnInitializedAsync()
    {
        await LoadDraftAsync();
    }

    private async Task LoadDraftAsync()
    {
        var draft = await CalibrationSettings.GetDraftAsync();
        CurveBreakpoints = draft.CurveBreakpoints.ToList();
        CurveSlopes = draft.CurveSlopes.ToList();
        Tau = new Dictionary<string, double>(draft.Tau, StringComparer.Ordinal);
        RiskMultipliers = new Dictionary<string, double>(draft.RiskMultipliers, StringComparer.Ordinal);
        ErrorFloor = draft.ErrorFloor;
        IsLoaded = true;
    }

    private CalibrationOverrides BuildDraft() => new(
        CalibrationSettings.BaseProfile.ProfileVersion,
        CalibrationSettings.BaseProfile.ProfileHash,
        CurveBreakpoints,
        CurveSlopes,
        Tau,
        ErrorFloor,
        RiskMultipliers);

    private string Invalid(string field) => Errors.ContainsKey(field) ? "invalid-calibration-field" : string.Empty;

    private static string UpperHelpId(int index) => $"curve-upper-help-{index}";

    private static string SlopeHelpId(int index) => $"curve-slope-help-{index}";

    private static string TauHelpId(string key) => $"tau-help-{key}";

    private static string RiskHelpId(string key) => $"risk-help-{key}";

    private string FormatCurveStart(int segmentIndex) => (segmentIndex == 0 ? 0 : CurveBreakpoints[segmentIndex - 1])
        .ToString("0.##", CultureInfo.CurrentCulture);

    private void AddSegment()
    {
        var newUpperBound = CurveBreakpoints.Count == 0
            ? 10d
            : CurveBreakpoints[^1] + Math.Max(1d, Math.Abs(CurveBreakpoints[^1]) * 0.2d);
        CurveBreakpoints.Add(newUpperBound);
        CurveSlopes.Add(CurveSlopes[^1]);
    }

    private void RemoveSegment(int segmentIndex)
    {
        if (segmentIndex <= 0 || CurveSlopes.Count <= 1) return;

        CurveSlopes.RemoveAt(segmentIndex);
        CurveBreakpoints.RemoveAt(segmentIndex == CurveBreakpoints.Count ? segmentIndex - 1 : segmentIndex);
    }

    private async Task ApplyAsync()
    {
        var draft = BuildDraft();
        if (draft.Validate().Count > 0)
        {
            StatusMessage = "Fix the highlighted values before applying calibration changes.";
            return;
        }

        try
        {
            await CalibrationSettings.ApplyAsync(draft);
            StatusMessage = "Local calibration overrides are active and saved in this browser.";
            await OnCalibrationChanged.InvokeAsync();
        }
        catch (Exception error)
        {
            StatusMessage = $"Calibration changes could not be saved: {error.Message}";
        }
    }

    private async Task ResetAsync()
    {
        try
        {
            await CalibrationSettings.ResetAsync();
            await LoadDraftAsync();
            StatusMessage = "Calibration was reset to the current server profile.";
            await OnCalibrationChanged.InvokeAsync();
        }
        catch (Exception error)
        {
            StatusMessage = $"Calibration could not be reset: {error.Message}";
        }
    }

    private Task CloseAsync() => OnClose.InvokeAsync();

}
