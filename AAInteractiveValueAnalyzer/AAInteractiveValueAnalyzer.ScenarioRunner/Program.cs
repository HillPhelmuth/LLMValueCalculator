using System.Net;
using System.Text.Json;
using AAInteractiveValueAnalyzer.Client.Models;
using AAInteractiveValueAnalyzer.Client.Services;

if (args.Length != 3)
{
    Console.Error.WriteLine(
        "Usage: ScenarioRunner <baseline-profile.json> <candidate-profile.json> <output.json>");
    return 2;
}

var baselineJson = await File.ReadAllTextAsync(args[0]);
var candidateJson = await File.ReadAllTextAsync(args[1]);
var baseline = CalibrationProfile.FromJson(baselineJson);
if (!StringComparer.OrdinalIgnoreCase.Equals(
        JsonDocument.Parse(candidateJson).RootElement.GetProperty("profile_hash").GetString(),
        CalibrationProfile.ComputeCanonicalHash(candidateJson)))
{
    throw new InvalidOperationException(
        $"Candidate profile hash verification failed. Expected " +
        $"{JsonDocument.Parse(candidateJson).RootElement.GetProperty("profile_hash").GetString()}, " +
        $"computed {CalibrationProfile.ComputeCanonicalHash(candidateJson)}.");
}
var candidate = CalibrationProfile.FromJson(candidateJson);
using var http = new HttpClient(new UnavailableHandler())
{
    BaseAddress = new Uri("https://model-catalog.invalid/")
};
var catalog = new ModelCatalog(http);
var baselineEngine = new RecommendationEngine(catalog, baseline);
var candidateEngine = new RecommendationEngine(catalog, candidate);
var rows = new List<object>();
var singleTransformVerified = true;
var onlyCurveAndTauDiffer =
    baseline.ErrorFloor == candidate.ErrorFloor
    && JsonMapsEqual(baseline.Adjustments, candidate.Adjustments)
    && JsonMapsEqual(baseline.RiskMultipliers, candidate.RiskMultipliers);

var categories = new[]
{
    TaskCategoryOption.ClassificationRouting,
    TaskCategoryOption.CodeGeneration,
    TaskCategoryOption.ResearchAnalysis
};
var difficulties = new[] { 10d, 22d, 30d };
var sensitivities = Enum.GetValues<DifficultySensitivityOption>();
var requiredSuccessRates = new[] { 80d, 95d };
var attempts = new[] { 1, 2 };

foreach (var category in categories)
foreach (var difficulty in difficulties)
foreach (var sensitivity in sensitivities)
foreach (var requiredSuccess in requiredSuccessRates)
foreach (var maxAttempts in attempts)
{
    var scenarioId =
        $"{category}|{difficulty}|{sensitivity}|{requiredSuccess}|{maxAttempts}";
    var inputs = new UseCaseInputs
    {
        UseCaseName = scenarioId,
        TaskCategory = category,
        LastAppliedTaskCategory = category,
        BaseDifficulty = difficulty,
        DifficultySensitivity = sensitivity,
        RequiredSuccessRate = requiredSuccess,
        RetriesAllowed = maxAttempts > 1,
        MaxAttempts = maxAttempts,
        HasSilentFailureRisk = false,
        AllowedCriticalFailureRate = 100,
        CriticalFailureShareOfFailures = 0
    };
    var baselineSummary = await baselineEngine.Analyze(inputs);
    var candidateSummary = await candidateEngine.Analyze(inputs);
    var baselineByModel = baselineSummary.Results
        .Select((result, rank) => (result, rank: rank + 1))
        .ToDictionary(item => item.result.Model.Name);
    var candidateByModel = candidateSummary.Results
        .Select((result, rank) => (result, rank: rank + 1))
        .ToDictionary(item => item.result.Model.Name);

    foreach (var model in baselineByModel.Keys.Order())
    {
        var left = baselineByModel[model];
        var right = candidateByModel[model];
        singleTransformVerified &=
            Math.Abs(
                left.result.AdjustedIntelligence
                - baseline.AdjustedIntelligence(left.result.RawCapabilityScore)) < 1e-9
            && Math.Abs(
                right.result.AdjustedIntelligence
                - candidate.AdjustedIntelligence(right.result.RawCapabilityScore)) < 1e-9;
        rows.Add(new
        {
            scenario_id = scenarioId,
            category = category.ToString(),
            base_difficulty = difficulty,
            sensitivity = sensitivity.ToString(),
            required_success_rate = requiredSuccess,
            max_attempts = maxAttempts,
            model,
            baseline = Snapshot(left.result, left.rank, baselineSummary),
            candidate = Snapshot(right.result, right.rank, candidateSummary),
            delta = new
            {
                rank = right.rank - left.rank,
                success_probability =
                    right.result.EffectiveSuccessRate - left.result.EffectiveSuccessRate,
                expected_value =
                    right.result.ExpectedValuePerTaskUsd - left.result.ExpectedValuePerTaskUsd,
                adjusted_capability =
                    right.result.AdjustedIntelligence - left.result.AdjustedIntelligence
            },
            attribution = "Experiment 1 capability curve and tau only"
        });
    }
}

var output = new
{
    baseline_profile_hash = baseline.ProfileHash,
    candidate_profile_hash = candidate.ProfileHash,
    scenarios = categories.Length * difficulties.Length * sensitivities.Length
        * requiredSuccessRates.Length * attempts.Length,
    rows,
    invariants = new
    {
        fallback = false,
        only_curve_and_tau_may_differ = onlyCurveAndTauDiffer,
        raw_capability_is_profile_transformed_once = singleTransformVerified
    }
};
var json = JsonSerializer.Serialize(output, new JsonSerializerOptions
{
    WriteIndented = true,
    PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower
});
Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(args[2]))!);
await File.WriteAllTextAsync(args[2], json + Environment.NewLine);
return 0;

static object Snapshot(
    RecommendationResult result,
    int rank,
    AnalysisSummary summary) => new
{
    rank,
    eligible = result.IsEligible,
    raw_capability = result.RawCapabilityScore,
    adjusted_capability = result.AdjustedIntelligence,
    success_probability = result.EffectiveSuccessRate,
    expected_value = result.ExpectedValuePerTaskUsd,
    profile_version = result.CalibrationProfileVersion,
    profile_hash = result.CalibrationProfileHash,
    fallback = summary.UsedCalibrationFallback
};

static bool JsonMapsEqual(
    IReadOnlyDictionary<string, JsonElement> left,
    IReadOnlyDictionary<string, JsonElement> right) =>
    left.Count == right.Count
    && left.All(item =>
        right.TryGetValue(item.Key, out var value)
        && item.Value.GetRawText() == value.GetRawText());

file sealed class UnavailableHandler : HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken) =>
        Task.FromResult(new HttpResponseMessage(HttpStatusCode.ServiceUnavailable));
}
