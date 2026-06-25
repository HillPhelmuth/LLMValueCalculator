using AAInteractiveValueAnalyzer.Client.Services;

namespace AAInteractiveValueAnalyzer.Client.Models;

public sealed record ModelProfile(
    string Provider,
    string Name,
    double IntelligenceIndex,
    double? CostPerAaTaskUsd,
    string Notes = "",
    // Latency / throughput, sourced from Artificial Analysis "performance" block.
    // EndToEndResponseSeconds is the only latency figure that is comparable across closed and open
    // models: TTFT/TTFA diverge only because some providers stream reasoning tokens and others hide
    // them, which is a visibility artifact, not a real difference in when useful work begins.
    // End-to-end captures the wall-clock the task actually occupies regardless of that, so it is the
    // figure used everywhere downstream. Nullable: not every model reports performance data.
    double? EndToEndResponseSeconds = null,
    double? OutputTokensPerSecond = null)
{
    public string DisplayName => Name;

    public double AdjustedIntelligence => RecommendationEngine.AdjustedIntelligence(IntelligenceIndex);

    public bool HasCostData => CostPerAaTaskUsd.HasValue;

    /// <summary>
    /// True when end-to-end latency is available, so the engine can apply latency cost and the
    /// latency eligibility gate. When false, the model is treated as latency-neutral (no cost, no
    /// gate) rather than penalized for missing data.
    /// </summary>
    public bool HasLatencyData => EndToEndResponseSeconds.HasValue;
}
