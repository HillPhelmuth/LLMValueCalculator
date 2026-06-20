namespace AAInteractiveValueAnalyzer.Client.Models;

public sealed class RecommendationResult
{
    public ModelProfile Model { get; init; } = default!;
    public double EffectiveDifficulty { get; init; }
    public double Tau { get; init; }
    public double SingleAttemptSuccessRate { get; init; }
    public double EffectiveSuccessRate { get; init; }
    public double CriticalFailureRate { get; init; }
    public int Attempts { get; init; }
    public double ExpectedAttempts { get; init; }
    public double ExpectedModelCostUsd { get; init; }
    public double ExpectedReviewCostUsd { get; init; }
    public double ExpectedRetryOverheadUsd { get; init; }
    public double ExpectedTotalDirectCostUsd { get; init; }
    public double CostPerSuccessfulTaskUsd { get; init; }
    public double ExpectedValuePerTaskUsd { get; init; }
    public double MonthlyExpectedValueUsd { get; init; }
    public double SuccessPerDollar { get; init; }
    public bool IsEligible { get; init; }
    public List<string> ExclusionReasons { get; init; } = [];
    public string RecommendationReason { get; init; } = string.Empty;
}

public sealed class AnalysisSummary
{
    public double EffectiveDifficulty { get; init; }
    public double Tau { get; init; }
    public IReadOnlyList<string> DifficultyFactors { get; init; } = [];
    public IReadOnlyList<string> GuardrailFactors { get; init; } = [];
    public IReadOnlyList<RecommendationResult> Results { get; init; } = [];
    public IReadOnlyList<RecommendationResult> EligibleResults { get; init; } = [];
    public RecommendationResult? BestExpectedValue { get; init; }
    public RecommendationResult? CheapestEligible { get; init; }
    public RecommendationResult? HighestQualityEligible { get; init; }
    public RecommendationResult? BestSuccessPerDollar { get; init; }
}
