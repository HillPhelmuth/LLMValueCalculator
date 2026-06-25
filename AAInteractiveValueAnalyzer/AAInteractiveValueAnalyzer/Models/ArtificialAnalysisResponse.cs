using System.Text.Json.Serialization;

namespace AAInteractiveValueAnalyzer.Models;

public class ArtificialAnalysisResponse
{
    [JsonPropertyName("tier")]
    public string? Tier { get; set; }

    [JsonPropertyName("intelligence_index_version")]
    public double IntelligenceIndexVersion { get; set; }

    [JsonPropertyName("pagination")]
    public Pagination? Pagination { get; set; }

    [JsonPropertyName("data")]
    public List<ModelData>? Data { get; set; }
}

public class ModelData
{
    [JsonPropertyName("id")]
    public Guid Id { get; set; }

    [JsonPropertyName("name")]
    public string? Name { get; set; }

    [JsonPropertyName("slug")]
    public string? Slug { get; set; }

    [JsonPropertyName("release_date")]
    public string ReleaseDate { get; set; }

    [JsonPropertyName("model_creator")]
    public ModelCreator? ModelCreator { get; set; }

    [JsonPropertyName("evaluations")]
    public Evaluations? Evaluations { get; set; }

    [JsonPropertyName("artificial_analysis_intelligence_index_cost")]
    public ArtificialAnalysisIntelligenceIndexCost? ArtificialAnalysisIntelligenceIndexCost { get; set; }

    [JsonPropertyName("pricing")]
    public Pricing? Pricing { get; set; }

    [JsonPropertyName("performance")]
    public Performance? Performance { get; set; }
}

public class ArtificialAnalysisIntelligenceIndexCost
{
    [JsonPropertyName("total_cost")]
    public double TotalCost { get; set; }

    [JsonPropertyName("cost_per_task")]
    public CostPerTask? CostPerTask { get; set; }
}

public class CostPerTask
{
    [JsonPropertyName("total_cost")]
    public double TotalCost { get; set; }
}

public class Evaluations
{
    [JsonPropertyName("artificial_analysis_intelligence_index")]
    public double? ArtificialAnalysisIntelligenceIndex { get; set; }

    [JsonPropertyName("artificial_analysis_coding_index")]
    public double? ArtificialAnalysisCodingIndex { get; set; }

    [JsonPropertyName("artificial_analysis_agentic_index")]
    public double? ArtificialAnalysisAgenticIndex { get; set; }
}

public class ModelCreator
{
    [JsonPropertyName("id")]
    public Guid Id { get; set; }

    [JsonPropertyName("name")]
    public string? Name { get; set; }
}

public class Performance
{
    [JsonPropertyName("median_output_tokens_per_second")]
    public double? MedianOutputTokensPerSecond { get; set; }

    [JsonPropertyName("median_time_to_first_token_seconds")]
    public double? MedianTimeToFirstTokenSeconds { get; set; }

    [JsonPropertyName("median_time_to_first_answer_token_seconds")]
    public double? MedianTimeToFirstAnswerTokenSeconds { get; set; }

    [JsonPropertyName("median_end_to_end_response_time_seconds")]
    public double? MedianEndToEndResponseTimeSeconds { get; set; }
}

public class Pricing
{
    [JsonPropertyName("price_1m_input_tokens")]
    public double? Price1MInputTokens { get; set; }

    [JsonPropertyName("price_1m_output_tokens")]
    public double? Price1MOutputTokens { get; set; }

    [JsonPropertyName("price_1m_cache_hit_tokens")]
    public double? Price1MCacheHitTokens { get; set; }

    [JsonPropertyName("price_1m_cache_write_tokens")]
    public double? Price1MCacheWriteTokens { get; set; }
}

public class Pagination
{
    [JsonPropertyName("page")]
    public long Page { get; set; }

    [JsonPropertyName("page_size")]
    public long PageSize { get; set; }

    [JsonPropertyName("total_pages")]
    public long TotalPages { get; set; }

    [JsonPropertyName("has_more")]
    public bool HasMore { get; set; }
}

public class AllModelsResponse
{
    [JsonPropertyName("models")]
    public List<ModelData> Models { get; set; } = [];
}
