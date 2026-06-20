using System.Text.Json;
using Microsoft.AspNetCore.Components;
using Microsoft.JSInterop;
using Markdig;

namespace AAInteractiveValueAnalyzer.Client.Pages;
public partial class IntroInfo
{
    private string? _markdownText;

    [Inject]
    private HttpClient HttpClient { get; set; } = null!;

    [Inject]
    private IJSRuntime JsRuntime { get; set; } = null!;

    protected override async Task OnInitializedAsync()
    {
        var markdownOutput = await HttpClient.GetStringAsync("Readme.md");
        _markdownText = MarkdownToHtml(markdownOutput);
    }

    private async Task GoBackToAnalyzer()
    {
        await JsRuntime.InvokeVoidAsync("aaInteractiveValueAnalyzer.goBackOrHome", "/");
    }

    private string MarkdownToHtml(string markdown)
    {
        var pipeline = new MarkdownPipelineBuilder().UseAdvancedExtensions().Build();
        return Markdown.ToHtml(markdown, pipeline);
    }
}
