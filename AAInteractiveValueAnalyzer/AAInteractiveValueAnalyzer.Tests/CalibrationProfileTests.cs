using System.Text.Json;
using AAInteractiveValueAnalyzer.Client.Models;
using Xunit;
using AAInteractiveValueAnalyzer.Services;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.FileProviders;
using Microsoft.Extensions.Hosting;
using Microsoft.AspNetCore.Hosting;

namespace AAInteractiveValueAnalyzer.Tests;

public class CalibrationProfileTests
{
    private static readonly string BaselinePath = Path.GetFullPath(Path.Combine(
        AppContext.BaseDirectory, "..", "..", "..", "..", "AAInteractiveValueAnalyzer",
        "CalibrationProfiles", "profiles", "baseline-1.0.0",
        "359c92d24341f37d4e83a2fb7cf500859df74dc6c723e4a03fe799d6da81220d", "profile.json"));

    [Fact]
    public void PythonProfileHashAndCurveAreConsumedExactly()
    {
        var profile = CalibrationProfile.FromJson(File.ReadAllText(BaselinePath));
        Assert.Equal("359c92d24341f37d4e83a2fb7cf500859df74dc6c723e4a03fe799d6da81220d", profile.ProfileHash);
        Assert.Equal(0, profile.AdjustedIntelligence(0));
        Assert.Equal(10, profile.AdjustedIntelligence(10), 8);
        Assert.Equal(24, profile.AdjustedIntelligence(20), 8);
        Assert.Equal(90, profile.AdjustedIntelligence(50), 8);
        Assert.Equal(120, profile.AdjustedIntelligence(60), 8);
    }

    [Fact]
    public void TamperedProfileIsRejected()
    {
        var json = File.ReadAllText(BaselinePath).Replace("1.4", "1.5");
        Assert.Throws<InvalidOperationException>(() => CalibrationProfile.FromJson(json));
    }

    [Fact]
    public async Task ClientProviderUsesVisibleBaselineOnApiFailure()
    {
        using var client = new HttpClient(new FailureHandler()) { BaseAddress = new Uri("https://example.test/") };
        var provider = new AAInteractiveValueAnalyzer.Client.Services.CalibrationProfileProvider(client);
        var profile = await provider.GetActiveAsync(TestContext.Current.CancellationToken);
        Assert.Same(CalibrationProfile.Baseline, profile);
        Assert.True(provider.UsedFallback);
        Assert.Contains("baseline-1.0.0", provider.Warning);
    }

    [Fact]
    public async Task DeploymentStoreRejectsPathTraversal()
    {
        var root = Path.Combine(Path.GetTempPath(), $"calibration-profile-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        try
        {
            await File.WriteAllTextAsync(Path.Combine(root, "index.json"),
                "{\"active_profile_path\":\"../outside.json\"}", TestContext.Current.CancellationToken);
            var configuration = new ConfigurationBuilder().AddInMemoryCollection(
                new Dictionary<string, string?> { ["CalibrationProfiles:Root"] = root }).Build();
            var store = new CalibrationProfileStore(new TestEnvironment(root), configuration);
            await Assert.ThrowsAsync<InvalidOperationException>(() =>
                store.ReadActiveAsync(TestContext.Current.CancellationToken));
        }
        finally { Directory.Delete(root, recursive: true); }
    }

    private sealed class FailureHandler : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) =>
            Task.FromResult(new HttpResponseMessage(System.Net.HttpStatusCode.ServiceUnavailable));
    }

    private sealed class TestEnvironment(string root) : IWebHostEnvironment
    {
        public string ApplicationName { get; set; } = "tests";
        public IFileProvider WebRootFileProvider { get; set; } = new NullFileProvider();
        public string WebRootPath { get; set; } = root;
        public string EnvironmentName { get; set; } = Environments.Development;
        public string ContentRootPath { get; set; } = root;
        public IFileProvider ContentRootFileProvider { get; set; } = new NullFileProvider();
    }
}
