using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace AAInteractiveValueAnalyzer.Client.Models;

public sealed record CalibrationCurveSegment(
    [property: JsonPropertyName("upper")] double? Upper,
    [property: JsonPropertyName("slope")] double Slope);

/// <summary>Canonical application view of a Python-generated calibration profile.</summary>
public sealed record CalibrationProfile(
    [property: JsonPropertyName("schema_version")] string SchemaVersion,
    [property: JsonPropertyName("profile_version")] string ProfileVersion,
    [property: JsonPropertyName("profile_hash")] string ProfileHash,
    [property: JsonPropertyName("curve_segments")] IReadOnlyList<CalibrationCurveSegment> CurveSegments,
    [property: JsonPropertyName("tau")] IReadOnlyDictionary<string, double> Tau,
    [property: JsonPropertyName("error_floor")] double ErrorFloor,
    [property: JsonPropertyName("adjustments")] IReadOnlyDictionary<string, JsonElement> Adjustments,
    [property: JsonPropertyName("risk_multipliers")] IReadOnlyDictionary<string, JsonElement> RiskMultipliers,
    [property: JsonPropertyName("uncertainty")] IReadOnlyDictionary<string, JsonElement> Uncertainty,
    [property: JsonPropertyName("manifest_hashes")] IReadOnlyList<string> ManifestHashes,
    [property: JsonPropertyName("fitting_data_hash")] string FittingDataHash,
    [property: JsonPropertyName("aa_snapshot")] string ArtificialAnalysisSnapshot,
    [property: JsonPropertyName("source_estimate_ids")] IReadOnlyList<string>? SourceEstimateIds = null,
    [property: JsonPropertyName("promotion_decisions")] IReadOnlyDictionary<string, string>? PromotionDecisions = null)
{
    public const double BaselineCustomerFacingCriticalShareMultiplier = 1.25;
    public const double BaselineSilentFailureCriticalShareMultiplier = 1.5;
    public const double BaselineDeterministicValidationCriticalMultiplier = 0.65;
    public const double BaselineHumanApprovalCriticalMultiplier = 0.5;
    public const double BaselineExtractionStrictValidationCriticalMultiplier = 0.85;
    public const double BaselineQualityShareDifficultyTilt = 0.30;
    public const double BaselineCriticalShareDifficultyTilt = 0.30;
    public const double BaselineRetryCorrelationDecay = 0.6;

    public static CalibrationProfile Baseline { get; } = new(
        "1.0", "baseline-1.0.0",
        "359c92d24341f37d4e83a2fb7cf500859df74dc6c723e4a03fe799d6da81220d",
        [
            new(10, 1), new(20, 1.4), new(30, 1.8),
            new(40, 2.2), new(50, 2.6), new(null, 3)
        ],
        new Dictionary<string, double> { ["soft"] = 8, ["normal"] = 5, ["sharp"] = 3 },
        0.01,
        new Dictionary<string, JsonElement>(),
        new Dictionary<string, JsonElement>(),
        new Dictionary<string, JsonElement>(),
        ["baseline-manifest-1.0.0"],
        "baseline-fitting-data-1.0.0", "baseline-aa-snapshot-1.0.0");

    public double CustomerFacingCriticalShareMultiplier => Risk("customer_facing_critical_share_multiplier", BaselineCustomerFacingCriticalShareMultiplier);
    public double SilentFailureCriticalShareMultiplier => Risk("silent_failure_critical_share_multiplier", BaselineSilentFailureCriticalShareMultiplier);
    public double DeterministicValidationCriticalMultiplier => Risk("deterministic_validation_critical_multiplier", BaselineDeterministicValidationCriticalMultiplier);
    public double HumanApprovalCriticalMultiplier => Risk("human_approval_critical_multiplier", BaselineHumanApprovalCriticalMultiplier);
    public double ExtractionStrictValidationCriticalMultiplier => Risk("extraction_strict_validation_critical_multiplier", BaselineExtractionStrictValidationCriticalMultiplier);
    public double QualityShareDifficultyTilt => Risk("quality_share_difficulty_tilt", BaselineQualityShareDifficultyTilt);
    public double CriticalShareDifficultyTilt => Risk("critical_share_difficulty_tilt", BaselineCriticalShareDifficultyTilt);
    public double RetryCorrelationDecay => Risk("retry_correlation_decay", BaselineRetryCorrelationDecay);

    public double AdjustedIntelligence(double rawIndex)
    {
        var adjusted = 0d;
        var lower = 0d;
        foreach (var segment in CurveSegments)
        {
            if (rawIndex <= lower) break;
            var top = segment.Upper is null ? rawIndex : Math.Min(rawIndex, segment.Upper.Value);
            adjusted += (top - lower) * segment.Slope;
            lower = segment.Upper ?? rawIndex;
        }
        return adjusted;
    }

    public void Validate()
    {
        if (string.IsNullOrWhiteSpace(SchemaVersion) || string.IsNullOrWhiteSpace(ProfileVersion) ||
            ProfileHash.Length != 64 || string.IsNullOrWhiteSpace(FittingDataHash) ||
            string.IsNullOrWhiteSpace(ArtificialAnalysisSnapshot))
            throw new InvalidOperationException("Calibration profile identity and provenance are required.");
        if (CurveSegments.Count != 6 || CurveSegments[0].Slope != 1 ||
            CurveSegments.Any(x => x.Slope <= 0) ||
            CurveSegments.Zip(CurveSegments.Skip(1)).Any(x => x.First.Slope > x.Second.Slope) ||
            CurveSegments.Take(5).Any(x => x.Upper is null) || CurveSegments[^1].Upper is not null ||
            CurveSegments.Take(5).Select(x => x.Upper!.Value).Zip(CurveSegments.Skip(1).Take(4).Select(x => x.Upper!.Value)).Any(x => x.First >= x.Second))
            throw new InvalidOperationException("Calibration curve must contain six ordered monotone segments.");
        if (ErrorFloor is < 0 or >= 1 || !Tau.Keys.ToHashSet().IsSupersetOf(["soft", "normal", "sharp"]) || Tau.Values.Any(x => x <= 0))
            throw new InvalidOperationException("Calibration profile contains invalid probability inputs.");
        if (ManifestHashes.Count == 0)
            throw new InvalidOperationException("Calibration profile must identify its source manifests.");
    }

    public static CalibrationProfile FromJson(string json, bool verifyHash = true)
    {
        var profile = JsonSerializer.Deserialize<CalibrationProfile>(json)
            ?? throw new InvalidOperationException("Calibration profile JSON was empty.");
        profile.Validate();
        if (verifyHash && !StringComparer.OrdinalIgnoreCase.Equals(profile.ProfileHash, ComputeCanonicalHash(json)))
            throw new InvalidOperationException("Calibration profile hash verification failed.");
        return profile;
    }

    public static string ComputeCanonicalHash(string json)
    {
        return Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(CanonicalJson(json))));
    }

    /// <summary>Returns the cross-language canonical JSON form used for profile hashing.</summary>
    public static string CanonicalJson(string json)
    {
        using var document = JsonDocument.Parse(json);
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream, new JsonWriterOptions
        {
            Indented = false,
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
        }))
            WriteCanonical(writer, document.RootElement, omitRootProfileHash: true);
        return Encoding.UTF8.GetString(stream.ToArray());
    }

    private double Risk(string key, double fallback) =>
        RiskMultipliers.TryGetValue(key, out var value) && value.ValueKind == JsonValueKind.Number
            ? value.GetDouble() : fallback;

    private static void WriteCanonical(Utf8JsonWriter writer, JsonElement element, bool omitRootProfileHash = false)
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.Object:
                writer.WriteStartObject();
                foreach (var property in element.EnumerateObject().Where(x => !omitRootProfileHash || x.Name != "profile_hash").OrderBy(x => x.Name, StringComparer.Ordinal))
                {
                    writer.WritePropertyName(property.Name);
                    WriteCanonical(writer, property.Value);
                }
                writer.WriteEndObject();
                break;
            case JsonValueKind.Array:
                writer.WriteStartArray();
                foreach (var item in element.EnumerateArray()) WriteCanonical(writer, item);
                writer.WriteEndArray();
                break;
            case JsonValueKind.String: writer.WriteStringValue(element.GetString()); break;
            case JsonValueKind.Number: writer.WriteRawValue(element.GetRawText()); break;
            case JsonValueKind.True: writer.WriteBooleanValue(true); break;
            case JsonValueKind.False: writer.WriteBooleanValue(false); break;
            case JsonValueKind.Null: writer.WriteNullValue(); break;
            default: throw new InvalidOperationException("Unsupported JSON token in calibration profile.");
        }
    }
}
