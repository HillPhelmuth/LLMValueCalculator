namespace AAInteractiveValueAnalyzer.Client.Models;

public sealed record ModelProfile(
    string Provider,
    string Name,
    double IntelligenceIndex,
    double? CostPerAaTaskUsd,
    string Notes = "")
{
    public string DisplayName => string.IsNullOrWhiteSpace(Provider) ? Name : $"{Provider} {Name}";
    public double AdjustedIntelligence => IntelligenceIndex switch
    {
        <= 20 => IntelligenceIndex,
        <= 40 => 20 + (2 * (IntelligenceIndex - 20)),
        _ => 60 + (3 * (IntelligenceIndex - 40))
    };

    public bool HasCostData => CostPerAaTaskUsd.HasValue;
}
