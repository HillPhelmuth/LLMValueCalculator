using AAInteractiveValueAnalyzer.Client.Models;
using System.Text.Json;

namespace AAInteractiveValueAnalyzer.Client.Services;

public sealed class CalibrationProfileProvider(HttpClient client)
{
    private readonly SemaphoreSlim gate = new(1, 1);
    private CalibrationProfile? cached;

    public bool UsedFallback { get; private set; }
    public string? Warning { get; private set; }

    public async Task<CalibrationProfile> GetActiveAsync(CancellationToken cancellationToken = default)
    {
        if (cached is not null) return cached;
        await gate.WaitAsync(cancellationToken);
        try
        {
            if (cached is not null) return cached;
            try
            {
                var json = await client.GetStringAsync("api/calibration-profile", cancellationToken);
                cached = CalibrationProfile.FromJson(json);
            }
            catch (Exception error) when (error is HttpRequestException or TaskCanceledException or InvalidOperationException or JsonException)
            {
                cached = CalibrationProfile.Baseline;
                UsedFallback = true;
                Warning = $"Calibration profile could not be loaded; using {cached.ProfileVersion} ({cached.ProfileHash[..12]}): {error.Message}";
            }
            return cached;
        }
        finally { gate.Release(); }
    }
}
