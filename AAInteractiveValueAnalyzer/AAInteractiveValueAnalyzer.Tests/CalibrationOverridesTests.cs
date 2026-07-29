using System.Net;
using System.Text.Json;
using AAInteractiveValueAnalyzer.Client.Models;
using AAInteractiveValueAnalyzer.Client.Services;
using Microsoft.JSInterop;
using Xunit;

namespace AAInteractiveValueAnalyzer.Tests;

public class CalibrationOverridesTests
{
    private static readonly string BaselinePath = Path.GetFullPath(Path.Combine(
        AppContext.BaseDirectory, "..", "..", "..", "..", "AAInteractiveValueAnalyzer",
        "CalibrationProfiles", "profiles", "baseline-1.0.0",
        "359c92d24341f37d4e83a2fb7cf500859df74dc6c723e4a03fe799d6da81220d", "profile.json"));

    [Fact]
    public void OverridesApplyOnlyCalculationInputsAndKeepProfileProvenance()
    {
        var baseline = CalibrationProfile.FromJson(File.ReadAllText(BaselinePath));
        var riskMultipliers = CalibrationOverrides.FromProfile(baseline).RiskMultipliers
            .ToDictionary(pair => pair.Key, pair => pair.Value, StringComparer.Ordinal);
        riskMultipliers["retry_correlation_decay"] = 0.4;
        var overrides = CalibrationOverrides.FromProfile(baseline) with
        {
            CurveBreakpoints = [11, 21, 31, 41, 51],
            CurveSlopes = [1, 1.5, 1.9, 2.3, 2.7, 3.1],
            Tau = new Dictionary<string, double>(StringComparer.Ordinal)
            {
                ["soft"] = 9,
                ["normal"] = 6,
                ["sharp"] = 4
            },
            ErrorFloor = 0.02,
            RiskMultipliers = riskMultipliers
        };

        var effective = overrides.ApplyTo(baseline);

        Assert.Equal(baseline.ProfileVersion, effective.ProfileVersion);
        Assert.Equal(baseline.ProfileHash, effective.ProfileHash);
        Assert.Equal(baseline.ManifestHashes, effective.ManifestHashes);
        Assert.Equal(11, effective.CurveSegments[0].Upper);
        Assert.Equal(3.1, effective.CurveSegments[^1].Slope);
        Assert.Equal(6, effective.Tau["normal"]);
        Assert.Equal(0.02, effective.ErrorFloor);
        Assert.Equal(0.4, effective.RetryCorrelationDecay);
    }

    [Fact]
    public void InvalidCurveAndProbabilityDraftsAreRejected()
    {
        var baseline = CalibrationProfile.FromJson(File.ReadAllText(BaselinePath));
        var invalid = CalibrationOverrides.FromProfile(baseline) with
        {
            CurveBreakpoints = [10, 10, 30, 40, 50],
            CurveSlopes = [1, 1.4, 1.3, 2.2, 2.6, 3],
            ErrorFloor = 1
        };

        var errors = invalid.Validate();

        Assert.Contains("breakpoint-1", errors.Keys);
        Assert.Contains("slope-2", errors.Keys);
        Assert.Contains("error-floor", errors.Keys);
        Assert.Throws<InvalidOperationException>(() => invalid.ApplyTo(baseline));
    }

    [Fact]
    public void CurveAllowsAnyPositiveNumberOfAnchoredSegments()
    {
        var baseline = CalibrationProfile.FromJson(File.ReadAllText(BaselinePath));
        var oneSegment = CalibrationOverrides.FromProfile(baseline) with
        {
            CurveBreakpoints = [],
            CurveSlopes = [1]
        };
        var threeSegments = CalibrationOverrides.FromProfile(baseline) with
        {
            CurveBreakpoints = [25, 60],
            CurveSlopes = [1, 1.7, 2.4]
        };

        Assert.Empty(oneSegment.Validate());
        Assert.Single(oneSegment.ApplyTo(baseline).CurveSegments);
        Assert.Empty(threeSegments.Validate());
        Assert.Equal(3, threeSegments.ApplyTo(baseline).CurveSegments.Count);
    }

    [Fact]
    public async Task SettingsPersistAppliedOverridesAndResetToServerProfile()
    {
        var storage = new InMemoryJsRuntime();
        var settings = new CalibrationSettingsService(CreateProfileProvider(), storage);

        var cancellationToken = TestContext.Current.CancellationToken;
        var draft = await settings.GetDraftAsync(cancellationToken);
        await settings.ApplyAsync(draft with { ErrorFloor = 0.02 }, cancellationToken);

        Assert.True(settings.HasOverrides);
        Assert.Equal(0.02, settings.EffectiveProfile.ErrorFloor);
        Assert.Contains("ErrorFloor", storage["aaInteractiveValueAnalyzer.calibrationOverrides.v1"]);

        await settings.ResetAsync(cancellationToken);

        Assert.False(settings.HasOverrides);
        Assert.Equal(CalibrationProfile.FromJson(File.ReadAllText(BaselinePath)).ErrorFloor, settings.EffectiveProfile.ErrorFloor);
        Assert.Null(storage["aaInteractiveValueAnalyzer.calibrationOverrides.v1"]);
    }

    [Fact]
    public async Task SettingsKeepValidSavedOverridesWhenBaseProfileIdentityChanges()
    {
        var baseline = CalibrationProfile.FromJson(File.ReadAllText(BaselinePath));
        var storedOverride = CalibrationOverrides.FromProfile(baseline) with
        {
            BaseProfileVersion = "previous-profile",
            BaseProfileHash = new string('a', 64),
            ErrorFloor = 0.03
        };
        var storage = new InMemoryJsRuntime();
        storage["aaInteractiveValueAnalyzer.calibrationOverrides.v1"] = JsonSerializer.Serialize(storedOverride);

        var settings = new CalibrationSettingsService(CreateProfileProvider(), storage);
        await settings.InitializeAsync(TestContext.Current.CancellationToken);

        Assert.True(settings.HasOverrides);
        Assert.Equal(0.03, settings.EffectiveProfile.ErrorFloor);
        Assert.Equal(baseline.ProfileVersion, settings.BaseProfile.ProfileVersion);
    }

    private static CalibrationProfileProvider CreateProfileProvider()
    {
        var client = new HttpClient(new ProfileHandler(File.ReadAllText(BaselinePath)))
        {
            BaseAddress = new Uri("https://example.test/")
        };
        return new CalibrationProfileProvider(client);
    }

    private sealed class ProfileHandler(string json) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(json) });
    }

    private sealed class InMemoryJsRuntime : IJSRuntime
    {
        private readonly Dictionary<string, string> values = new(StringComparer.Ordinal);

        public string? this[string key]
        {
            get => values.GetValueOrDefault(key);
            set
            {
                if (value is null) values.Remove(key);
                else values[key] = value;
            }
        }

        public ValueTask<TValue> InvokeAsync<TValue>(string identifier, object?[]? args) => InvokeAsync<TValue>(identifier, default, args);

        public ValueTask<TValue> InvokeAsync<TValue>(string identifier, CancellationToken cancellationToken, object?[]? args)
        {
            var key = args?.FirstOrDefault() as string ?? string.Empty;
            switch (identifier)
            {
                case "aaInteractiveValueAnalyzer.getLocalStorage":
                    return ValueTask.FromResult((TValue)(object?)values.GetValueOrDefault(key)!);
                case "aaInteractiveValueAnalyzer.setLocalStorage":
                    values[key] = (string)args![1]!;
                    break;
                case "aaInteractiveValueAnalyzer.removeLocalStorage":
                    values.Remove(key);
                    break;
                default:
                    throw new InvalidOperationException($"Unexpected JavaScript call: {identifier}");
            }

            return ValueTask.FromResult(default(TValue)!);
        }
    }
}
