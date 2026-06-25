using System.Net.Http.Json;
using System.Text.Json;
using AAInteractiveValueAnalyzer.Client.Models;
using System.Text.Json.Serialization;

namespace AAInteractiveValueAnalyzer.Client.Services;

public class ModelCatalog
{
    public ModelCatalog(HttpClient httpClient)
    {
        _httpClient = httpClient;
    }

    private static List<ModelProfile>? _cachedProfiles;
    private HttpClient _httpClient;
    public async Task<List<ModelProfile>> GetLatestModelData()
    {
        try
        {
            if (_cachedProfiles is { Count: > 0 }) return _cachedProfiles;
            var jsonResponse = await _httpClient.GetStringAsync("api/models");
            var response = JsonSerializer.Deserialize<ArtificialAnalysisModels>(jsonResponse, new JsonSerializerOptions(){PropertyNameCaseInsensitive = true});
            var result = response.Models.Select(model => new ModelProfile(model.ModelCreator?.Name ?? "",
                model.Name ?? "", model.Evaluations.ArtificialAnalysisIntelligenceIndex,
                model.ArtificialAnalysisIntelligenceIndexCost?.CostPerTask?.TotalCost ?? 0, "",
                model.Performance?.MedianEndToEndResponseTimeSeconds ?? 0,
                model.Performance?.MedianOutputTokensPerSecond ?? 0)).ToList();
            _cachedProfiles = result;
            return result;
        }
        catch (Exception ex)
        {
            Console.WriteLine($"Error fetching model data: {ex}. Falling back to hard-coded data.");
            return Models.ToList();
        }
    }
    // Values are hard-coded from the two Artificial Analysis charts supplied with the prompt.
    // Intelligence: Artificial Analysis Intelligence Index.
    // Cost: Cost per Artificial Analysis Intelligence Index task in USD.
    // Latency: median end-to-end response time (seconds) and median output tokens/second, from the
    //   Artificial Analysis "performance" block. Fable 5 has no own perf row (it is a fallback
    //   config) so it borrows Opus 4.8's figures. Gemma 4 26B reports no perf data; it is left null
    //   and the engine treats it as latency-neutral.

    public static IReadOnlyList<ModelProfile> Models { get; } =
    [
        new("Anthropic", "Claude Fable 5 (with fallback)", 60, 3.2542, "Highest intelligence shown; highest cost shown.", 25.67, 58.41),
        new("Anthropic", "Claude Opus 4.8 (max)", 56, 2.0479, "Very high intelligence with very high chart cost.", 25.67, 58.41),
        new("OpenAI", "GPT-5.5 (xhigh)", 55, 0.8261, "Very high intelligence with very low verbosity.", 47.01, 63.06),
        new("Anthropic", "Claude Opus 4.7 (max)", 54, 1.9733, "Very high intelligence with very high chart cost.", 32.13, 50.55),
        new("OpenAI", "GPT-5.5 (high)", 53, 0.719, "Very high intelligence with low-moderate chart cost.", 30.34, 57.63),
        new("OpenAI", "GPT-5.4 (xhigh)", 51, 0.993, "Strong intelligence with high chart cost.", 97.82, 149.37),
        new("Z-AI", "GLM-5.2 (max)", 51, 0.4157, "Strong intelligence with moderate chart cost.", 29.04, 90.72),
        new("Google", "Gemini 3.5 Flash", 50, 0.6811, "Strong intelligence with moderate chart cost.", 23.28, 163.11),
        new("Anthropic", "Claude Sonnet 4.6 (max)", 47, 1.1356, "Moderate-high intelligence with high chart cost.", 115.91, 49.69),
        new("Google", "Gemini 3.1 Pro Preview", 46, 0.3372, "Moderate-high intelligence with low chart cost.", 32.37, 128.33),
        new("Alibaba", "Qwen3.7 Max", 46, 0.5601, "Moderate-high intelligence with moderate chart cost.", 17.93, 191.11),
        new("MiniMax", "Minimax-M3", 44, 0.1567, "Moderate intelligence with low chart cost.", 46.49, 57.86),
        new("DeepSeek", "V4 Pro Max", 44, 0.0484, "Moderate intelligence with very low chart cost.", 56.76, 88.5),
        new("Kimi", "K2.6", 43, 0.3146, "Moderate intelligence with low-moderate chart cost.", 112.65, 45.03),
        new("Xiaomi", "MiMo-V2.5-Pro", 42, 0.0322, "Moderate-lower intelligence with very low chart cost.", 48.93, 53.85),
        new("Kimi", "K2.7 Code", 42, 0.1838, "Moderate-lower intelligence with low chart cost.", 50.28, 56.79),
        new("DeepSeek", "V4 Flash (Max)", 40, 0.0279, "Lower intelligence with very low chart cost.", 57.45, 109.08),
        new("Z-AI", "GLM-5.1", 40, 0.2404, "Lower intelligence with low chart cost.", 48.94, 90.47),
        new("OpenAI", "GPT-5.4 mini (xhigh)", 40, 0.5048, "Lower intelligence with moderate chart cost.", 8.34, 174.88),
        new("Alibaba", "Qwen3.6 Plus", 40, 0.2667, "Lower intelligence with low-moderate chart cost.", 117.64, 52.65),
        new("Alibaba", "Qwen3.7 Plus", 39, 0.0492, "Lower intelligence with very low chart cost.", 51.23, 51.6),
        new("OpenAI", "GPT-5.4 nano (xhigh)", 38, 0.1428, "Lower intelligence with low chart cost.", 7.94, 162.01),
        new("MiniMax", "MiniMax-M2.7", 38, 0.0742, "Lower intelligence with very low chart cost.", 63.07, 48.74),
        new("NVIDIA", "Nemotron 3.0 Ultra", 38, 0.2446, "Lower intelligence with low chart cost.", 17.42, 170.37),
        new("xAI", "Grok 4.3 (high)", 38, 0.1858, "Lower intelligence with low chart cost.", 14.41, 136.6),
        new("Alibaba", "Qwen3.6 27B", 37, 0.2653, "Lower intelligence with low-moderate chart cost.", 114.79, 55.59),
        new("Alibaba", "Qwen3.5 397B A17B", 34, 0.3331, "Low-mid intelligence with low-moderate chart cost.", 74.73, 51.26),
        new("Alibaba", "Qwen3.5 122B A10B", 32, 0.2412, "Low-mid intelligence with low chart cost.", 20.57, 138.07),
        new("Alibaba", "Qwen3.6 35B A3B", 32, 0.1784, "Low-mid intelligence with low chart cost.", 36.97, 170.8),
        new("InclusionAI", "Ring-2.6-1T", 31, 0.3448, "Low-mid intelligence with low-moderate chart cost.", 22.2, 133.2),
        new("Mistral", "Medium 3.5", 30, 0.5971, "Lower intelligence with moderate chart cost.", 19.9, 141.05),
        new("StepFun", "Step 3.7 Flash", 30, 0.0884, "Lower intelligence with very low chart cost.", 7.28, 394.59),
        new("Anthropic", "Claude 4.5 Haiku", 30, 0.2375, "Lower intelligence with low chart cost.", 22.13, 92.18),
        new("Google", "Gemma 4 26B A4B", 26, 0.032, "Low intelligence score with very low chart cost.", null, null),
        new("NVIDIA", "Nemotron 3 Super 120B A12B", 25, 0.2435, "Low intelligence score with low chart cost.", 10.03, 299.35),
        new("Google", "Gemini 3.1 Flash-Lite", 25, 0.043, "Low intelligence score with very low chart cost.", 7.03, 296.38),
        new("xAI", "Grok 4.3 (Non-reasoning)", 25, 0.4526, "Low intelligence score with moderate chart cost.", 4.75, 124.17),
        new("OpenAI", "gpt-oss-120b (high)", 24, 0.0607, "Low intelligence score among listed models with very low chart cost.", 8.24, 343.13),
        new("AWS", "Nova 2.0 Pro Preview (Medium)", 22, 0.1728, "Low intelligence score with low chart cost.", 33.07, 120.47),
        new("OpenAI", "gpt-oss-20b (high)", 15, 0.0178, "Lowest intelligence score among listed models with lowest chart cost.", 11.97, 223.95)
    ];
}
public partial class ArtificialAnalysisModels
{
    [JsonPropertyName("models")]
    public List<Model> Models { get; set; }
}

public partial class Model
{
    [JsonPropertyName("id")]
    public Guid Id { get; set; }

    [JsonPropertyName("name")]
    public string Name { get; set; }

    [JsonPropertyName("slug")]
    public string Slug { get; set; }

    [JsonPropertyName("release_date")]
    public DateTimeOffset? ReleaseDate { get; set; }

    [JsonPropertyName("model_creator")]
    public ModelCreator ModelCreator { get; set; }

    [JsonPropertyName("evaluations")]
    public Evaluations Evaluations { get; set; }

    [JsonPropertyName("artificial_analysis_intelligence_index_cost")]
    public ArtificialAnalysisIntelligenceIndexCost ArtificialAnalysisIntelligenceIndexCost { get; set; }

    [JsonPropertyName("pricing")]
    public Pricing Pricing { get; set; }

    [JsonPropertyName("performance")]
    public Performance Performance { get; set; }
}

public partial class ArtificialAnalysisIntelligenceIndexCost
{
    [JsonPropertyName("total_cost")]
    public double TotalCost { get; set; }

    [JsonPropertyName("cost_per_task")]
    public CostPerTask CostPerTask { get; set; }
}

public partial class CostPerTask
{
    [JsonPropertyName("total_cost")]
    public double TotalCost { get; set; }
}

public partial class Evaluations
{
    [JsonPropertyName("artificial_analysis_intelligence_index")]
    public double ArtificialAnalysisIntelligenceIndex { get; set; }

    [JsonPropertyName("artificial_analysis_coding_index")]
    public double ArtificialAnalysisCodingIndex { get; set; }

    [JsonPropertyName("artificial_analysis_agentic_index")]
    public double ArtificialAnalysisAgenticIndex { get; set; }
}

public partial class ModelCreator
{
    [JsonPropertyName("id")]
    public Guid Id { get; set; }

    [JsonPropertyName("name")]
    public string Name { get; set; }
}

public partial class Performance
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

public partial class Pricing
{
    [JsonPropertyName("price_1m_input_tokens")]
    public double Price1MInputTokens { get; set; }

    [JsonPropertyName("price_1m_output_tokens")]
    public double Price1MOutputTokens { get; set; }

    [JsonPropertyName("price_1m_cache_hit_tokens")]
    public double? Price1MCacheHitTokens { get; set; }

    [JsonPropertyName("price_1m_cache_write_tokens")]
    public double? Price1MCacheWriteTokens { get; set; }
}
