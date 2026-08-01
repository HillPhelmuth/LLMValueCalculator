using System.Net;
using AAInteractiveValueAnalyzer.Client.Models;
using AAInteractiveValueAnalyzer.Client.Services;
using Xunit;

namespace AAInteractiveValueAnalyzer.Tests;

public class RecommendationEngineEconomicsTests
{
    [Fact]
    public void PerThousandEconomicInputsKeepTheirExistingNumericDefaults()
    {
        var inputs = new UseCaseInputs();

        Assert.Equal(2, inputs.BusinessValuePerThousandSuccessesUsd);
        Assert.Equal(1, inputs.AcceptableValuePerThousandSuccessesUsd);
        Assert.Equal(0.25, inputs.BenignFailureCostPerThousandFailuresUsd);
    }

    [Fact]
    public async Task PerThousandValuesAreProratedWithoutAnExtraBatchMultiplier()
    {
        using var client = new HttpClient(new SingleModelHandler())
        {
            BaseAddress = new Uri("https://example.test/")
        };
        var engine = new RecommendationEngine(new ModelCatalog(client), CalibrationProfile.Baseline);
        var inputs = new UseCaseInputs
        {
            BaseDifficulty = 30,
            RequiredSuccessRate = 0,
            AllowedCriticalFailureRate = 100,
            CriticalFailureShareOfFailures = 40,
            HasSilentFailureRisk = false,
            RetriesAllowed = false,
            CostMultiplier = 0,
            BusinessValuePerThousandSuccessesUsd = 2,
            AcceptableValuePerThousandSuccessesUsd = 1,
            GoodOutcomeShareOfSuccesses = 60,
            FailureCostUsd = 10,
            BenignFailureCostPerThousandFailuresUsd = 0.25,
            HumanReviewCostUsd = 0,
            OperationalRetryCostUsd = 0,
            LatencyCostPerSecondUsd = 0
        };

        var summary = await engine.Analyze(inputs);
        var result = Assert.Single(summary.Results);
        var expectedBlendedValue =
            inputs.BusinessValuePerThousandSuccessesUsd * result.RealizedGoodOutcomeShare
            + inputs.AcceptableValuePerThousandSuccessesUsd * (1 - result.RealizedGoodOutcomeShare);
        var benignFailureRate = Math.Max(
            0,
            (1 - result.EffectiveSuccessRate) - result.CriticalFailureRate);
        var expectedCriticalFailureCost =
            inputs.FailureCostUsd * result.CriticalFailureRate * RecommendationEngine.TaskBatchSize;
        var expectedBenignFailureCost =
            inputs.BenignFailureCostPerThousandFailuresUsd * benignFailureRate;
        var expectedValue =
            expectedBlendedValue * result.EffectiveSuccessRate
            - expectedCriticalFailureCost
            - expectedBenignFailureCost;

        Assert.True(result.CriticalFailureRate > 0);
        Assert.Equal(0, result.ExpectedTotalDirectCostUsd, 12);
        Assert.Equal(expectedBlendedValue, result.BlendedValuePerThousandSuccessesUsd, 12);
        Assert.Equal(expectedCriticalFailureCost, result.ExpectedCriticalFailureCostUsd, 12);
        Assert.Equal(expectedBenignFailureCost, result.ExpectedBenignFailureCostUsd, 12);
        Assert.Equal(expectedValue, result.ExpectedValuePerTaskUsd, 12);
    }

    private sealed class SingleModelHandler : HttpMessageHandler
    {
        private const string ModelJson = """
            {
              "data": [
                {
                  "name": "Economics test model",
                  "model_creator": { "name": "Test provider" },
                  "evaluations": { "artificial_analysis_intelligence_index": 20 },
                  "artificial_analysis_intelligence_index_cost": {
                    "cost_per_task": { "total_cost": 2.0 }
                  }
                }
              ]
            }
            """;

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken) =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(ModelJson)
            });
    }
}
