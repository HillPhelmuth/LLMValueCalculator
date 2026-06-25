using System.Net.Http.Json;
using Microsoft.AspNetCore.Components;

namespace AAInteractiveValueAnalyzer.Client.Pages;
public partial class Home
{
    [Inject] private HttpClient Client { get; set; } = null!;
    private async Task GetLatestModelData()
    {
        var result = await Client.GetStringAsync("api/models");
        Console.WriteLine($"Result:\n\n{result}");
    }
}
