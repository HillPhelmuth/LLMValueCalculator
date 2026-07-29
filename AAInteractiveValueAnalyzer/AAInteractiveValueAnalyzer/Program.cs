using System.Text.Json;
using AAInteractiveValueAnalyzer.Client.Pages;
using AAInteractiveValueAnalyzer.Components;
using AAInteractiveValueAnalyzer.Models;
using AAInteractiveValueAnalyzer.Services;

var builder = WebApplication.CreateBuilder(args);

// Add services to the container.
builder.Services.AddRazorComponents()
    .AddInteractiveWebAssemblyComponents();
builder.Services.AddHttpClient();
builder.Services.AddSingleton<CalibrationProfileStore>();
var app = builder.Build();

// Configure the HTTP request pipeline.
if (app.Environment.IsDevelopment())
{
    app.UseWebAssemblyDebugging();
}
else
{
    app.UseExceptionHandler("/Error", createScopeForErrors: true);
    // The default HSTS value is 30 days. You may want to change this for production scenarios, see https://aka.ms/aspnetcore-hsts.
    app.UseHsts();
}
app.UseStatusCodePagesWithReExecute("/not-found", createScopeForStatusCodePages: true);
app.UseHttpsRedirection();

app.UseAntiforgery();

app.MapStaticAssets();
app.MapRazorComponents<App>()
    .AddInteractiveWebAssemblyRenderMode()
    .AddAdditionalAssemblies(typeof(AAInteractiveValueAnalyzer.Client._Imports).Assembly);
app.MapGet("api/calibration-profile", async (HttpRequest request, HttpResponse response, CalibrationProfileStore profiles, CancellationToken cancellationToken) =>
{
    try
    {
        var active = await profiles.ReadActiveAsync(cancellationToken);
        var etag = $"\"{active.Hash}\"";
        response.Headers.ETag = etag;
        response.Headers.CacheControl = "no-cache";
        if (request.Headers.IfNoneMatch.Any(value => StringComparer.Ordinal.Equals(value, etag)))
            return Results.StatusCode(StatusCodes.Status304NotModified);
        return Results.Text(active.Json, "application/json");
    }
    catch (Exception error) when (error is IOException or JsonException or InvalidOperationException)
    {
        return Results.Problem("No valid active calibration profile is available.", statusCode: StatusCodes.Status503ServiceUnavailable);
    }
});
var apiKey = builder.Configuration["ArtificialAnalysis:ApiKey"];
app.MapGet("api/models", async () =>
{
    /*
     curl "https://artificialanalysis.ai/api/v2/language/models/free" \
       -H "x-api-key: {apiKey}"
     */
    var response = new AllModelsResponse();
    using var client = app.Services.GetRequiredService<IHttpClientFactory>().CreateClient();
    client.DefaultRequestHeaders.Add("x-api-key", apiKey);
    var responseJson = await client.GetFromJsonAsync<ArtificialAnalysisResponse>("https://artificialanalysis.ai/api/v2/language/models/free");
    var currentPage = responseJson.Pagination.Page;
    var totalPages = responseJson.Pagination.TotalPages;
    response.Models.AddRange(responseJson.Data!.Where(x => x.ArtificialAnalysisIntelligenceIndexCost is not null));
    for (var i = 2; i <= totalPages; i++)
    {
        responseJson = await client.GetFromJsonAsync<ArtificialAnalysisResponse>($"https://artificialanalysis.ai/api/v2/language/models/free?page={i}");
        response.Models.AddRange(responseJson.Data!.Where(x => x.ArtificialAnalysisIntelligenceIndexCost is not null));
    }
    response.Models = response.Models.DistinctBy(x => x.Id).ToList();
#if DEBUG
    var json = JsonSerializer.Serialize(response, new JsonSerializerOptions { WriteIndented = true });
    var basePath = @"C:\Users\adamh\source\repos\LLMValueCalculator";
    var aaOutputPath = $@"{basePath}\AAInteractiveValueAnalyzer\AAInteractiveValueAnalyzer\AAOutput";
    if (!Directory.Exists(aaOutputPath))
    {
        Directory.CreateDirectory(aaOutputPath);
    }
    File.WriteAllText($@"{aaOutputPath}\ArtificialAnalysisModelsWithCostPerTask-{DateTime.Now:yyyyMMddHHmmss}.json", json);
#endif

    return Results.Ok(response);
});
app.Run();
