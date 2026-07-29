using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace AAInteractiveValueAnalyzer.Client.Models;

/// <param name="Upper">Highest raw intelligence index covered by this segment, inclusive. A null value denotes the final segment.</param>
/// <param name="Slope">Adjusted intelligence points produced per raw intelligence index point within this segment.</param>
public sealed record CalibrationCurveSegment(
    [property: JsonPropertyName("upper")] double? Upper,
    [property: JsonPropertyName("slope")] double Slope);

/// <summary>Canonical application view of a Python-generated calibration profile.</summary>
/// <param name="SchemaVersion">Schema version of the serialized calibration profile.</param>
/// <param name="ProfileVersion">Human-readable version identifier for this calibration profile.</param>
/// <param name="ProfileHash">SHA-256 hash of the canonical profile JSON, excluding this field.</param>
/// <param name="CurveSegments">Configurable convex transform applied to the raw intelligence index before it is compared to task difficulty. The transform is deliberately convex: a gap near the top of the index reflects a larger real capability difference than the same nominal gap near the bottom, so per-point value rises with the index. The piecewise-linear shape introduces slope kinks at each breakpoint, but can be tuned or fitted without changing the model type.</param>
/// <param name="Tau">The sigmoid slope used for each difficulty sensitivity setting. Tau is expressed in published 0-100 capability-index points.</param>
/// <param name="ErrorFloor">Systematic single-attempt failure floor applied to every model regardless of task difficulty: refusals, formatting flukes, infrastructure errors, and truncation. The floor is retry-resistant, so retries recover only capability failures and no amount of retrying approaches 100%. The baseline 1% value is a prior, not a measurement.</param>
/// <param name="Adjustments">Calibration adjustments reserved for profile-defined difficulty inputs.</param>
/// <param name="RiskMultipliers">Calibration multipliers for critical-failure and retry-risk modeling.</param>
/// <param name="Uncertainty">Uncertainty metadata and values associated with the calibration.</param>
/// <param name="ManifestHashes">Hashes identifying the source manifests used to produce the profile.</param>
/// <param name="FittingDataHash">Hash identifying the fitting data used to produce the profile.</param>
/// <param name="ArtificialAnalysisSnapshot">Identifier for the Artificial Analysis data snapshot used by the profile.</param>
/// <param name="SourceEstimateIds">Optional identifiers for the source estimates used during calibration.</param>
/// <param name="PromotionDecisions">Optional promotion decisions associated with calibrated values.</param>
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
    /// <summary>
    /// Prior applied to the critical share of failures for output delivered directly to customers.
    /// Customer exposure changes consequence and detection risk, not model capability.
    /// </summary>
    public const double BaselineCustomerFacingCriticalShareMultiplier = 1.25;

    /// <summary>
    /// Multiplier applied to the critical-failure share of failures when silent failures are likely.
    /// This is the sole channel for silent-failure risk.
    /// </summary>
    public const double BaselineSilentFailureCriticalShareMultiplier = 1.5;

    /// <summary>
    /// Multiplier applied to the critical-failure rate when deterministic validation is present.
    /// Validators catch deterministic (schema/syntax) failures but miss semantic ones, so this is a
    /// partial cut, not a halving.
    /// </summary>
    public const double BaselineDeterministicValidationCriticalMultiplier = 0.65;

    /// <summary>
    /// Multiplier applied to the critical-failure rate when high-risk actions require human approval.
    /// </summary>
    public const double BaselineHumanApprovalCriticalMultiplier = 0.5;

    /// <summary>
    /// Additional critical-failure multiplier for extraction workloads that require strict
    /// structured output and use deterministic validation.
    /// </summary>
    public const double BaselineExtractionStrictValidationCriticalMultiplier = 0.85;

    /// <summary>
    /// How strongly a model's headroom above the task tilts the realized good-output share around the
    /// user's base assumption (<see cref="UseCaseInputs.GoodOutcomeShareOfSuccesses"/>).
    /// </summary>
    /// <remarks>
    /// Partial credit splits a passing task into a fully-correct ("good") outcome worth the full
    /// business value and a degraded-but-acceptable outcome worth less. A model that clears the
    /// difficulty bar with room to spare produces cleaner output than one that barely clears it, so
    /// two models with the same pass rate can still differ in realized value. The base good-share is
    /// tilted by the model's single-attempt headroom, clamped to [0,1]. Set to 0 to make good-share
    /// flat and decouple realized quality from model strength.
    /// </remarks>
    public const double BaselineQualityShareDifficultyTilt = 0.30;

    /// <summary>
    /// How strongly a model's headroom tilts the critical share of its failures, mirroring
    /// <see cref="BaselineQualityShareDifficultyTilt"/> on the downside.
    /// </summary>
    /// <remarks>
    /// A marginal model's failures skew toward catastrophic misreadings of the task, while a
    /// comfortable model's rare failures skew toward benign slips. Negative headroom therefore
    /// raises the critical share of failures and positive headroom lowers it, before the detection
    /// multipliers apply. Set to 0 to make the critical share independent of model strength.
    /// </remarks>
    public const double BaselineCriticalShareDifficultyTilt = 0.30;

    /// <summary>
    /// Independence weight of each additional retry attempt. The 2nd attempt counts as this
    /// fraction of a fresh independent try, the 3rd as the square, and so on. Shared by the
    /// success and cost sides of the ledger so both use one retry model.
    /// </summary>
    public const double BaselineRetryCorrelationDecay = 0.6;

    /// <summary>Default calibration profile used when no custom profile is configured.</summary>
    public static CalibrationProfile Baseline { get; } = new(
        "1.0", "baseline-1.0.0",
        "359c92d24341f37d4e83a2fb7cf500859df74dc6c723e4a03fe799d6da81220d",
        [
            new CalibrationCurveSegment(10, 1), new CalibrationCurveSegment(20, 1.4), new CalibrationCurveSegment(30, 1.8),
            new CalibrationCurveSegment(40, 2.2), new CalibrationCurveSegment(50, 2.6), new CalibrationCurveSegment(null, 3)
        ],
        new Dictionary<string, double> { ["soft"] = 8, ["normal"] = 5, ["sharp"] = 3 },
        0.01,
        new Dictionary<string, JsonElement>(),
        new Dictionary<string, JsonElement>(),
        new Dictionary<string, JsonElement>(),
        ["baseline-manifest-1.0.0"],
        "baseline-fitting-data-1.0.0", "baseline-aa-snapshot-1.0.0");

    /// <summary>
    /// Prior applied to the critical share of failures for output delivered directly to customers.
    /// Customer exposure changes consequence and detection risk, not model capability.
    /// </summary>
    public double CustomerFacingCriticalShareMultiplier => Risk("customer_facing_critical_share_multiplier", BaselineCustomerFacingCriticalShareMultiplier);

    /// <summary>
    /// Multiplier applied to the critical-failure share of failures when silent failures are likely.
    /// This is the sole channel for silent-failure risk.
    /// </summary>
    public double SilentFailureCriticalShareMultiplier => Risk("silent_failure_critical_share_multiplier", BaselineSilentFailureCriticalShareMultiplier);

    /// <summary>
    /// Multiplier applied to the critical-failure rate when deterministic validation is present.
    /// Validators catch deterministic (schema/syntax) failures but miss semantic ones, so this is a
    /// partial cut, not a halving.
    /// </summary>
    public double DeterministicValidationCriticalMultiplier => Risk("deterministic_validation_critical_multiplier", BaselineDeterministicValidationCriticalMultiplier);

    /// <summary>Multiplier applied to the critical-failure rate when high-risk actions require human approval.</summary>
    public double HumanApprovalCriticalMultiplier => Risk("human_approval_critical_multiplier", BaselineHumanApprovalCriticalMultiplier);

    /// <summary>
    /// Additional critical-failure multiplier for extraction workloads that require strict
    /// structured output and use deterministic validation.
    /// </summary>
    public double ExtractionStrictValidationCriticalMultiplier => Risk("extraction_strict_validation_critical_multiplier", BaselineExtractionStrictValidationCriticalMultiplier);

    /// <summary>
    /// How strongly a model's headroom above the task tilts the realized good-output share around the
    /// user's base assumption (<see cref="UseCaseInputs.GoodOutcomeShareOfSuccesses"/>).
    /// </summary>
    public double QualityShareDifficultyTilt => Risk("quality_share_difficulty_tilt", BaselineQualityShareDifficultyTilt);

    /// <summary>
    /// How strongly a model's headroom tilts the critical share of its failures, mirroring
    /// <see cref="QualityShareDifficultyTilt"/> on the downside.
    /// </summary>
    public double CriticalShareDifficultyTilt => Risk("critical_share_difficulty_tilt", BaselineCriticalShareDifficultyTilt);

    /// <summary>
    /// Independence weight of each additional retry attempt. The 2nd attempt counts as this
    /// fraction of a fresh independent try, the 3rd as the square, and so on. Shared by the
    /// success side (<see cref="RecommendationEngine"/>) and the cost side so both halves of the
    /// ledger use one retry model.
    /// </summary>
    public double RetryCorrelationDecay => Risk("retry_correlation_decay", BaselineRetryCorrelationDecay);

    /// <summary>
    /// Applies the configured convex transform to a raw intelligence index. This is the profile-side
    /// source of truth for the curve.
    /// </summary>
    /// <param name="rawIndex">The raw Artificial Analysis Intelligence Index value.</param>
    /// <returns>The convex-adjusted intelligence used in the success model.</returns>
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
