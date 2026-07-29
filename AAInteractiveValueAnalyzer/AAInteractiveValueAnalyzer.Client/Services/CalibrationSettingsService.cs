using System.Text.Json;
using AAInteractiveValueAnalyzer.Client.Models;
using Microsoft.JSInterop;

namespace AAInteractiveValueAnalyzer.Client.Services;

public sealed class CalibrationSettingsService(CalibrationProfileProvider profileProvider, IJSRuntime jsRuntime)
{
    private const string StorageKey = "aaInteractiveValueAnalyzer.calibrationOverrides.v1";
    private CalibrationProfile? baseProfile;
    private CalibrationOverrides? appliedOverrides;
    private bool initialized;

    public bool HasOverrides => appliedOverrides is not null;
    public bool UsedFallback => profileProvider.UsedFallback;
    public string? Warning { get; private set; }
    public CalibrationProfile BaseProfile => baseProfile ?? throw new InvalidOperationException("Calibration settings have not been initialized.");
    public CalibrationProfile EffectiveProfile => appliedOverrides?.ApplyTo(BaseProfile) ?? BaseProfile;

    public async Task InitializeAsync(CancellationToken cancellationToken = default)
    {
        if (initialized) return;

        baseProfile = await profileProvider.GetActiveAsync(cancellationToken);
        Warning = profileProvider.Warning;
        initialized = true;

        try
        {
            var stored = await jsRuntime.InvokeAsync<string?>("aaInteractiveValueAnalyzer.getLocalStorage", cancellationToken, new object?[] { StorageKey });
            if (string.IsNullOrWhiteSpace(stored)) return;

            var overrides = JsonSerializer.Deserialize<CalibrationOverrides>(stored);
            if (overrides is null)
            {
                Warning = "Saved calibration overrides could not be read. Defaults are active; you can reset the saved values from Advanced calibration.";
                return;
            }

            var errors = overrides.Validate();
            if (errors.Count > 0)
            {
                Warning = "Saved calibration overrides are invalid for the current calculator. Defaults are active; review or reset them in Advanced calibration.";
                return;
            }

            appliedOverrides = overrides;
        }
        catch (Exception error) when (error is JSException or JsonException or InvalidOperationException)
        {
            Warning = $"Saved calibration overrides could not be loaded. Defaults are active: {error.Message}";
        }
    }

    public async Task ApplyAsync(CalibrationOverrides overrides, CancellationToken cancellationToken = default)
    {
        await InitializeAsync(cancellationToken);
        var errors = overrides.Validate();
        if (errors.Count > 0)
            throw new InvalidOperationException(string.Join(" ", errors.Values));

        // Rebuild against the latest loaded profile so profile metadata is never persisted or modified.
        appliedOverrides = overrides with { BaseProfileVersion = BaseProfile.ProfileVersion, BaseProfileHash = BaseProfile.ProfileHash };
        _ = appliedOverrides.ApplyTo(BaseProfile);
        await jsRuntime.InvokeVoidAsync("aaInteractiveValueAnalyzer.setLocalStorage", StorageKey, JsonSerializer.Serialize(appliedOverrides));
    }

    public async Task ResetAsync(CancellationToken cancellationToken = default)
    {
        await InitializeAsync(cancellationToken);
        appliedOverrides = null;
        await jsRuntime.InvokeVoidAsync("aaInteractiveValueAnalyzer.removeLocalStorage", StorageKey);
    }

    public async Task<CalibrationOverrides> GetDraftAsync(CancellationToken cancellationToken = default)
    {
        await InitializeAsync(cancellationToken);
        return appliedOverrides ?? CalibrationOverrides.FromProfile(BaseProfile);
    }
}
