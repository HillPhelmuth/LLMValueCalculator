using System.Text.Json;

namespace AAInteractiveValueAnalyzer.Client.Models;

/// <summary>
/// The browser-persisted, calculation-facing portion of a calibration profile.
/// Provenance and profile identity always remain owned by the server profile.
/// </summary>
public sealed record CalibrationOverrides(
    string BaseProfileVersion,
    string BaseProfileHash,
    IReadOnlyList<double> CurveBreakpoints,
    IReadOnlyList<double> CurveSlopes,
    IReadOnlyDictionary<string, double> Tau,
    double ErrorFloor,
    IReadOnlyDictionary<string, double> RiskMultipliers)
{
    public static readonly IReadOnlyList<string> TauKeys = ["soft", "normal", "sharp"];

    public static readonly IReadOnlyList<string> RiskMultiplierKeys =
    [
        "customer_facing_critical_share_multiplier",
        "silent_failure_critical_share_multiplier",
        "deterministic_validation_critical_multiplier",
        "human_approval_critical_multiplier",
        "extraction_strict_validation_critical_multiplier",
        "quality_share_difficulty_tilt",
        "critical_share_difficulty_tilt",
        "retry_correlation_decay"
    ];

    public static CalibrationOverrides FromProfile(CalibrationProfile profile) => new(
        profile.ProfileVersion,
        profile.ProfileHash,
        profile.CurveSegments.Take(profile.CurveSegments.Count - 1).Select(segment => segment.Upper!.Value).ToArray(),
        profile.CurveSegments.Select(segment => segment.Slope).ToArray(),
        TauKeys.ToDictionary(key => key, key => profile.Tau[key], StringComparer.Ordinal),
        profile.ErrorFloor,
        new Dictionary<string, double>(StringComparer.Ordinal)
        {
            ["customer_facing_critical_share_multiplier"] = profile.CustomerFacingCriticalShareMultiplier,
            ["silent_failure_critical_share_multiplier"] = profile.SilentFailureCriticalShareMultiplier,
            ["deterministic_validation_critical_multiplier"] = profile.DeterministicValidationCriticalMultiplier,
            ["human_approval_critical_multiplier"] = profile.HumanApprovalCriticalMultiplier,
            ["extraction_strict_validation_critical_multiplier"] = profile.ExtractionStrictValidationCriticalMultiplier,
            ["quality_share_difficulty_tilt"] = profile.QualityShareDifficultyTilt,
            ["critical_share_difficulty_tilt"] = profile.CriticalShareDifficultyTilt,
            ["retry_correlation_decay"] = profile.RetryCorrelationDecay
        });

    public IReadOnlyDictionary<string, string> Validate()
    {
        var errors = new Dictionary<string, string>(StringComparer.Ordinal);

        if (CurveSlopes.Count < 1)
            errors["curve-slopes"] = "At least one curve segment is required.";
        else if (CurveBreakpoints.Count != CurveSlopes.Count - 1)
            errors["curve-breakpoints"] = "Every segment except the final open-ended segment needs one upper bound.";
        else
        {
            for (var index = 0; index < CurveBreakpoints.Count; index++)
            {
                var value = CurveBreakpoints[index];
                if (!double.IsFinite(value) || value <= 0)
                    errors[$"breakpoint-{index}"] = "Breakpoints must be positive finite numbers.";
                else if (index > 0 && value <= CurveBreakpoints[index - 1])
                    errors[$"breakpoint-{index}"] = "Each breakpoint must be greater than the preceding breakpoint.";
            }
        }

        if (CurveSlopes.Count > 0)
        {
            for (var index = 0; index < CurveSlopes.Count; index++)
            {
                var value = CurveSlopes[index];
                if (!double.IsFinite(value) || value <= 0)
                    errors[$"slope-{index}"] = "Slopes must be positive finite numbers.";
                else if (index > 0 && value < CurveSlopes[index - 1])
                    errors[$"slope-{index}"] = "Slopes must not decrease between curve segments.";
            }

            if (CurveSlopes[0] != 1)
                errors["slope-0"] = "The first slope anchors the curve and must remain 1.";
        }

        foreach (var key in TauKeys)
        {
            if (!Tau.TryGetValue(key, out var value) || !double.IsFinite(value) || value <= 0)
                errors[$"tau-{key}"] = "Tau values must be positive finite numbers.";
        }

        if (!double.IsFinite(ErrorFloor) || ErrorFloor < 0 || ErrorFloor >= 1)
            errors["error-floor"] = "The error floor must be at least 0 and less than 1.";

        foreach (var key in RiskMultiplierKeys)
        {
            if (!RiskMultipliers.TryGetValue(key, out var value) || !double.IsFinite(value))
                errors[$"risk-{key}"] = "Calibration values must be finite numbers.";
        }

        return errors;
    }

    public CalibrationProfile ApplyTo(CalibrationProfile baseProfile)
    {
        var errors = Validate();
        if (errors.Count > 0)
            throw new InvalidOperationException(string.Join(" ", errors.Values));

        var tau = new Dictionary<string, double>(baseProfile.Tau, StringComparer.Ordinal);
        foreach (var key in TauKeys) tau[key] = Tau[key];

        var risk = new Dictionary<string, JsonElement>(baseProfile.RiskMultipliers, StringComparer.Ordinal);
        foreach (var key in RiskMultiplierKeys) risk[key] = JsonSerializer.SerializeToElement(RiskMultipliers[key]);

        var curve = CurveSlopes.Select((slope, index) =>
            new CalibrationCurveSegment(index < CurveBreakpoints.Count ? CurveBreakpoints[index] : null, slope)).ToArray();

        return new CalibrationProfile(
            baseProfile.SchemaVersion,
            baseProfile.ProfileVersion,
            baseProfile.ProfileHash,
            curve,
            tau,
            ErrorFloor,
            baseProfile.Adjustments,
            risk,
            baseProfile.Uncertainty,
            baseProfile.ManifestHashes,
            baseProfile.FittingDataHash,
            baseProfile.ArtificialAnalysisSnapshot,
            baseProfile.SourceEstimateIds,
            baseProfile.PromotionDecisions);
    }
}
