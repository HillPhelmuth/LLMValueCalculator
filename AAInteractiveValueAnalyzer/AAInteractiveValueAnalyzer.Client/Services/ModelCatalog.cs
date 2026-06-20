using AAInteractiveValueAnalyzer.Client.Models;

namespace AAInteractiveValueAnalyzer.Client.Services;

public static class ModelCatalog
{
    // Values are hard-coded from the two Artificial Analysis charts supplied with the prompt.
    // Intelligence: Artificial Analysis Intelligence Index.
    // Cost: Cost per Artificial Analysis Intelligence Index task in USD.

    public static IReadOnlyList<ModelProfile> Models { get; } =
    [
        new("Anthropic", "Claude Fable 5 (with fallback)", 60, 3.2542, "Highest intelligence shown; highest cost shown."),
        new("Anthropic", "Claude Opus 4.8 (max)", 56, 2.0479, "Very high intelligence with very high chart cost."),
        new("OpenAI", "GPT-5.5 (xhigh)", 55, 0.8261, "Very high intelligence with very low verbosity."),
        new("Anthropic", "Claude Opus 4.7 (max)", 54, 1.9733, "Very high intelligence with very high chart cost."),
        new("OpenAI", "GPT-5.5 (high)", 53, 0.719, "Very high intelligence with low-moderate chart cost."),
        new("OpenAI", "GPT-5.4 (xhigh)", 51, 0.993, "Strong intelligence with high chart cost."),
        new("Z-AI", "GLM-5.2 (max)", 51, 0.4157, "Strong intelligence with moderate chart cost."),
        new("Google", "Gemini 3.5 Flash", 50, 0.6811, "Strong intelligence with moderate chart cost."),
        new("Anthropic", "Claude Sonnet 4.6 (max)", 47, 1.1356, "Moderate-high intelligence with high chart cost."),
        new("Google", "Gemini 3.1 Pro Preview", 46, 0.3372, "Moderate-high intelligence with low chart cost."),
        new("Alibaba", "Qwen3.7 Max", 46, 0.5601, "Moderate-high intelligence with moderate chart cost."),
        new("MiniMax", "Minimax-M3", 44, 0.1567, "Moderate intelligence with low chart cost."),
        new("DeepSeek", "V4 Pro Max", 44, 0.0484, "Moderate intelligence with very low chart cost."),
        new("Kimi", "K2.6", 43, 0.3146, "Moderate intelligence with low-moderate chart cost."),
        new("Xiaomi", "MiMo-V2.5-Pro", 42, 0.0322, "Moderate-lower intelligence with very low chart cost."),
        new("Kimi", "K2.7 Code", 42, 0.1838, "Moderate-lower intelligence with low chart cost."),
        new("DeepSeek", "V4 Flash (Max)", 40, 0.0279, "Lower intelligence with very low chart cost."),
        new("Z-AI", "GLM-5.1", 40, 0.2404, "Lower intelligence with low chart cost."),
        new("OpenAI", "GPT-5.4 mini (xhigh)", 40, 0.5048, "Lower intelligence with moderate chart cost."),
        new("Alibaba", "Qwen3.6 Plus", 40, 0.2667, "Lower intelligence with low-moderate chart cost."),
        new("Alibaba", "Qwen3.7 Plus", 39, 0.0492, "Lower intelligence with very low chart cost."),
        new("OpenAI", "GPT-5.4 nano (xhigh)", 38, 0.1428, "Lower intelligence with low chart cost."),
        new("MiniMax", "MiniMax-M2.7", 38, 0.0742, "Lower intelligence with very low chart cost."),
        new("NVIDIA", "Nemotron 3.0 Ultra", 38, 0.2446, "Lower intelligence with low chart cost."),
        new("xAI", "Grok 4.3 (high)", 38, 0.1858, "Lower intelligence with low chart cost."),
        new("Alibaba", "Qwen3.6 27B", 37, 0.2653, "Lower intelligence with low-moderate chart cost."),
        new("Alibaba", "Qwen3.5 397B A17B", 34, 0.3331, "Low-mid intelligence with low-moderate chart cost."),
        new("Alibaba", "Qwen3.5 122B A10B", 32, 0.2412, "Low-mid intelligence with low chart cost."),
        new("Alibaba", "Qwen3.6 35B A3B", 32, 0.1784, "Low-mid intelligence with low chart cost."),
        new("InclusionAI", "Ring-2.6-1T", 31, 0.3448, "Low-mid intelligence with low-moderate chart cost."),
        new("Mistral", "Medium 3.5", 30, 0.5971, "Lower intelligence with moderate chart cost."),
        new("StepFun", "Step 3.7 Flash", 30, 0.0884, "Lower intelligence with very low chart cost."),
        new("Anthropic", "Claude 4.5 Haiku", 30, 0.2375, "Lower intelligence with low chart cost."),
        new("Google", "Gemma 4 26B A4B", 26, 0.032, "Low intelligence score with very low chart cost."),
        new("NVIDIA", "Nemotron 3 Super 120B A12B", 25, 0.2435, "Low intelligence score with low chart cost."),
        new("Google", "Gemini 3.1 Flash-Lite", 25, 0.043, "Low intelligence score with very low chart cost."),
        new("xAI", "Grok 4.3 (Non-reasoning)", 25, 0.4526, "Low intelligence score with moderate chart cost."),
        new("OpenAI", "gpt-oss-120b (high)", 24, 0.0607, "Low intelligence score among listed models with very low chart cost."),
        new("AWS", "Nova 2.0 Pro Preview (Medium)", 22, 0.1728, "Low intelligence score with low chart cost."),
        new("OpenAI", "gpt-oss-20b (high)", 15, 0.0178, "Lowest intelligence score among listed models with lowest chart cost.")
    ];


}
