using System.Text.Json;
using AAInteractiveValueAnalyzer.Client.Models;

namespace AAInteractiveValueAnalyzer.Services;

public sealed record ActiveCalibrationProfile(string Json, string Hash);

public sealed class CalibrationProfileStore(IWebHostEnvironment environment, IConfiguration configuration)
{
    private readonly string root = Path.GetFullPath(
        configuration["CalibrationProfiles:Root"] ?? Path.Combine(environment.ContentRootPath, "CalibrationProfiles"));

    public async Task<ActiveCalibrationProfile> ReadActiveAsync(CancellationToken cancellationToken = default)
    {
        var indexPath = Path.Combine(root, "index.json");
        if (!File.Exists(indexPath))
            throw new FileNotFoundException("The calibration profile index is missing.", indexPath);

        await using var indexStream = File.OpenRead(indexPath);
        using var index = await JsonDocument.ParseAsync(indexStream, cancellationToken: cancellationToken);
        var configuredPath = index.RootElement.GetProperty("active_profile_path").GetString();
        if (string.IsNullOrWhiteSpace(configuredPath))
            throw new InvalidOperationException("The calibration profile index has no active profile.");

        var candidate = Path.IsPathRooted(configuredPath)
            ? Path.GetFullPath(configuredPath)
            : Path.GetFullPath(Path.Combine(root, configuredPath));
        var rootPrefix = root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!candidate.StartsWith(rootPrefix, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("The active calibration profile path escapes the configured profile root.");

        var json = await File.ReadAllTextAsync(candidate, cancellationToken);
        var profile = CalibrationProfile.FromJson(json);
        return new ActiveCalibrationProfile(json, profile.ProfileHash);
    }
}
