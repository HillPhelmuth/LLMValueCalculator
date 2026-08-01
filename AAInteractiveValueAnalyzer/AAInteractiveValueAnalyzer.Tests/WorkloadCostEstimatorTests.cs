using System.Net;
using AAInteractiveValueAnalyzer.Client.Models;
using AAInteractiveValueAnalyzer.Client.Services;
using Xunit;

namespace AAInteractiveValueAnalyzer.Tests;

public class WorkloadCostEstimatorTests
{
    [Fact]
    public void ReferenceMediansNormalizeToKnownRelativeCostMultipliers()
    {
        var gdpval = WorkloadCostEstimator.BenchmarkReferences.Single(reference => reference.Benchmark == "GDPval-AA v2");
        var hle = WorkloadCostEstimator.BenchmarkReferences.Single(reference => reference.Benchmark == "Humanity's Last Exam");

        Assert.Equal(2.022727272727272, gdpval.NormalizedMedianMultiplier, 12);
        Assert.Equal(0.398582600195503, hle.NormalizedMedianMultiplier, 12);
    }

    [Fact]
    public void EveryWorkloadCombinationProducesABoundedFiniteMultiplier()
    {
        foreach (var context in Enum.GetValues<ContextRequirementOption>())
        foreach (var reasoning in Enum.GetValues<ReasoningDepthOption>())
        foreach (var toolUse in Enum.GetValues<ToolUseOption>())
        {
            var factor = WorkloadCostEstimator.Estimate(context, reasoning, toolUse);

            Assert.True(double.IsFinite(factor));
            Assert.InRange(factor, WorkloadCostEstimator.MinimumObservedMultiplier, WorkloadCostEstimator.MaximumObservedMultiplier);
        }

        var clampedHigh = WorkloadCostEstimator.Estimate(
            ContextRequirementOption.LargeNoisy,
            ReasoningDepthOption.ResearchGradeSynthesisPlanning,
            ToolUseOption.AutonomousToolSequence);

        Assert.Equal(WorkloadCostEstimator.MaximumObservedMultiplier, clampedHigh, 12);
    }

    [Fact]
    public void FactorIsMonotonicForEachCalibratedWorkloadDimension()
    {
        AssertNonDecreasing(
            Enum.GetValues<ToolUseOption>()
                .Select(toolUse => WorkloadCostEstimator.Estimate(
                    ContextRequirementOption.MediumMostlyRelevant,
                    ReasoningDepthOption.ModerateMultiStep,
                    toolUse)));
        AssertNonDecreasing(
            Enum.GetValues<ReasoningDepthOption>()
                .Select(reasoning => WorkloadCostEstimator.Estimate(
                    ContextRequirementOption.MediumMostlyRelevant,
                    reasoning,
                    ToolUseOption.MultipleToolsWithValidation)));
        AssertNonDecreasing(
            Enum.GetValues<ContextRequirementOption>()
                .Select(context => WorkloadCostEstimator.Estimate(
                    context,
                    ReasoningDepthOption.ModerateMultiStep,
                    ToolUseOption.MultipleToolsWithValidation)));

        var autonomous = WorkloadCostEstimator.Estimate(
            ContextRequirementOption.MediumMostlyRelevant,
            ReasoningDepthOption.ModerateMultiStep,
            ToolUseOption.AutonomousToolSequence);
        var irreversible = WorkloadCostEstimator.Estimate(
            ContextRequirementOption.MediumMostlyRelevant,
            ReasoningDepthOption.ModerateMultiStep,
            ToolUseOption.AgenticWorkflowWithIrreversibleActions);

        Assert.Equal(autonomous, irreversible, 12);
    }

    [Fact]
    public void ScreenshotWorkloadUsesTheFittedAutomaticFactor()
    {
        var factor = WorkloadCostEstimator.Estimate(
            ContextRequirementOption.MediumMostlyRelevant,
            ReasoningDepthOption.ModerateMultiStep,
            ToolUseOption.AutonomousToolSequence);

        Assert.Equal(0.580259521450848, factor, 12);
    }

    [Fact]
    public void UncalibratedWorkloadFieldsDoNotChangeTheAutomaticFactor()
    {
        var baseline = new UseCaseInputs
        {
            ContextRequirement = ContextRequirementOption.MediumMostlyRelevant,
            ReasoningDepth = ReasoningDepthOption.ModerateMultiStep,
            ToolUse = ToolUseOption.AutonomousToolSequence,
            DomainSpecificity = DomainSpecificityOption.GeneralKnowledge,
            Verifiability = VerifiabilityOption.DeterministicallyTestable,
            OutputConstraint = OutputConstraintOption.FreeText
        };
        var changed = new UseCaseInputs
        {
            ContextRequirement = baseline.ContextRequirement,
            ReasoningDepth = baseline.ReasoningDepth,
            ToolUse = baseline.ToolUse,
            DomainSpecificity = DomainSpecificityOption.ExpertOrRegulatedDomain,
            Verifiability = VerifiabilityOption.HardToDetectWrongAnswers,
            OutputConstraint = OutputConstraintOption.ExternalFacingOrRegulatedArtifact
        };

        Assert.Equal(WorkloadCostEstimator.Estimate(baseline), WorkloadCostEstimator.Estimate(changed), 12);
    }

    [Fact]
    public async Task EngineAppliesCombinedFactorToModelCostAndLatencyBeforeAttemptsAndBatchSize()
    {
        using var client = new HttpClient(new SingleModelHandler()) { BaseAddress = new Uri("https://example.test/") };
        var engine = new RecommendationEngine(new ModelCatalog(client), CalibrationProfile.Baseline);
        var inputs = new UseCaseInputs
        {
            BaseDifficulty = 30,
            ContextRequirement = ContextRequirementOption.MediumMostlyRelevant,
            ReasoningDepth = ReasoningDepthOption.ModerateMultiStep,
            ToolUse = ToolUseOption.AutonomousToolSequence,
            CostMultiplier = 1.5,
            RetriesAllowed = true,
            MaxAttempts = 2,
            RequiredSuccessRate = 0,
            AllowedCriticalFailureRate = 100,
            HasSilentFailureRisk = false,
            LatencyCostPerSecondUsd = 0.25
        };

        var summary = await engine.Analyze(inputs);
        var result = Assert.Single(summary.Results);
        var expectedModelCost = 2d * summary.EffectiveCostMultiplier * result.ExpectedAttempts * RecommendationEngine.TaskBatchSize;
        var expectedLatencySeconds = 10d * summary.EffectiveCostMultiplier * result.ExpectedAttempts;
        var expectedLatencyCost = expectedLatencySeconds * inputs.LatencyCostPerSecondUsd * RecommendationEngine.TaskBatchSize;

        Assert.Equal(1.5, summary.ManualCostMultiplier, 12);
        Assert.Equal(0.580259521450848, summary.WorkloadCostMultiplier, 12);
        Assert.Equal(summary.WorkloadCostMultiplier * summary.ManualCostMultiplier, summary.EffectiveCostMultiplier, 12);
        Assert.Equal(summary.ManualCostMultiplier, result.ManualCostMultiplier, 12);
        Assert.Equal(summary.WorkloadCostMultiplier, result.WorkloadCostMultiplier, 12);
        Assert.Equal(summary.EffectiveCostMultiplier, result.EffectiveCostMultiplier, 12);
        Assert.True(result.ExpectedAttempts > 1);
        Assert.Equal(expectedModelCost, result.ExpectedModelCostUsd, 10);
        Assert.Equal(expectedLatencySeconds, result.ExpectedLatencySeconds, 10);
        Assert.Equal(expectedLatencyCost, result.ExpectedLatencyCostUsd, 10);
    }

    private static void AssertNonDecreasing(IEnumerable<double> values)
    {
        var previous = double.NegativeInfinity;
        foreach (var value in values)
        {
            Assert.True(value >= previous);
            previous = value;
        }
    }

    private sealed class SingleModelHandler : HttpMessageHandler
    {
        private const string ModelJson = """
            {
              "data": [
                {
                  "name": "Cost test model",
                  "model_creator": { "name": "Test provider" },
                  "evaluations": { "artificial_analysis_intelligence_index": 20 },
                  "artificial_analysis_intelligence_index_cost": {
                    "cost_per_task": { "total_cost": 2.0 }
                  },
                  "performance": { "median_end_to_end_response_time_seconds": 10.0 }
                }
              ]
            }
            """;

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(ModelJson) });
    }
}
