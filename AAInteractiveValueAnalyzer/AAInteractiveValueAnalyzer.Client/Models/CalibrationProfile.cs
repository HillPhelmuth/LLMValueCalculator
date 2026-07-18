using System.Text.Json;
using System.Text.Json.Serialization;

namespace AAInteractiveValueAnalyzer.Client.Models;

/// <summary>
/// Immutable, application-facing view of a generated calibration profile.
/// </summary>
public sealed record CalibrationProfile(
    [property: JsonPropertyName("profile_version")]
    string ProfileVersion,
    [property: JsonPropertyName("profile_hash")]
    string ProfileHash,
    [property: JsonPropertyName("tau")]
    IReadOnlyDictionary<string, double> Tau,
    [property: JsonPropertyName("error_floor")]
    double ErrorFloor,
    [property: JsonPropertyName("customer_facing_critical_share_multiplier")]
    double CustomerFacingCriticalShareMultiplier,
    [property: JsonPropertyName("silent_failure_critical_share_multiplier")]
    double SilentFailureCriticalShareMultiplier,
    [property: JsonPropertyName("deterministic_validation_critical_multiplier")]
    double DeterministicValidationCriticalMultiplier,
    [property: JsonPropertyName("human_approval_critical_multiplier")]
    double HumanApprovalCriticalMultiplier,
    [property: JsonPropertyName("extraction_strict_validation_critical_multiplier")]
    double ExtractionStrictValidationCriticalMultiplier,
    [property: JsonPropertyName("quality_share_difficulty_tilt")]
    double QualityShareDifficultyTilt,
    [property: JsonPropertyName("critical_share_difficulty_tilt")]
    double CriticalShareDifficultyTilt,
    [property: JsonPropertyName("retry_correlation_decay")]
    double RetryCorrelationDecay,
    [property: JsonPropertyName("fitting_data_hash")]
    string FittingDataHash,
    [property: JsonPropertyName("aa_snapshot")]
    string ArtificialAnalysisSnapshot)
{
    public static CalibrationProfile Baseline { get; } = new(
        "baseline-1.0.0",
        "359c92d24341f37d4e83a2fb7cf500859df74dc6c723e4a03fe799d6da81220d",
        new Dictionary<string, double>
        {
            ["soft"] = 8,
            ["normal"] = 5,
            ["sharp"] = 3
        },
        0.01,
        1.25,
        1.5,
        0.65,
        0.5,
        0.85,
        0.30,
        0.30,
        0.6,
        "baseline-fitting-data-1.0.0",
        "baseline-aa-snapshot-1.0.0");

    public void Validate()
    {
        if (string.IsNullOrWhiteSpace(ProfileVersion) || string.IsNullOrWhiteSpace(ProfileHash))
        {
            throw new InvalidOperationException("Calibration profile version and hash are required.");
        }

        if (ErrorFloor is < 0 or >= 1 || Tau.Count == 0 || Tau.Values.Any(value => value <= 0))
        {
            throw new InvalidOperationException("Calibration profile contains invalid curve inputs.");
        }
    }

    public static CalibrationProfile FromJson(string json)
    {
        var profile = JsonSerializer.Deserialize<CalibrationProfile>(json)
            ?? throw new InvalidOperationException("Calibration profile JSON was empty.");
        profile.Validate();
        return profile;
    }
}
