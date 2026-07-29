namespace AAInteractiveValueAnalyzer.Client.Models;

/// <summary>
/// Benchmark and operating data for one model configuration.
/// </summary>
/// <remarks>
/// Artificial Analysis publishes model configurations, not only base model families. Keep the
/// provider and complete configuration name so reasoning effort and routing variants remain distinct.
/// Null means the source did not measure a value; it never means zero.
/// </remarks>
public sealed record ModelProfile(
    string Provider,
    string Name,
    double IntelligenceIndex,
    double? CostPerAaTaskUsd,
    string Notes = "",
    double? EndToEndResponseSeconds = null,
    double? OutputTokensPerSecond = null,
    double? CodingIndex = null,
    double? AgenticIndex = null)
{
    public string DisplayName => Name;

    public bool HasCostData => CostPerAaTaskUsd is > 0;

    public bool HasLatencyData => EndToEndResponseSeconds is > 0;

    public bool HasCodingIndex => CodingIndex is >= 0;

    public bool HasAgenticIndex => AgenticIndex is >= 0;

    public double CapabilityIndexFor(TaskCategoryOption category) => category switch
    {
        TaskCategoryOption.CodeGeneration when HasCodingIndex => IntelligenceIndex,
        TaskCategoryOption.AgenticWorkflow when HasAgenticIndex => IntelligenceIndex,
        _ => IntelligenceIndex
    };

    public string CapabilityIndexNameFor(TaskCategoryOption category) => category switch
    {
        _ => "Artificial Analysis Intelligence Index"
    };
}
