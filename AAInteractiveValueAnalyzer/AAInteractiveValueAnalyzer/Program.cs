using AAInteractiveValueAnalyzer.Client.Pages;
using AAInteractiveValueAnalyzer.Components;
using AAInteractiveValueAnalyzer.Models;

var builder = WebApplication.CreateBuilder(args);

// Add services to the container.
builder.Services.AddRazorComponents()
    .AddInteractiveWebAssemblyComponents();
builder.Services.AddHttpClient();
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
  
    return Results.Ok(response);
});
app.Run();
