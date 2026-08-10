using AAInteractiveValueAnalyzer.Client.Services;
using Microsoft.AspNetCore.Components.WebAssembly.Hosting;
using Radzen;

var builder = WebAssemblyHostBuilder.CreateDefault(args);
builder.Services.AddHttpClient("ApiClient", client =>
{
    client.BaseAddress = new Uri(builder.HostEnvironment.BaseAddress);
});
builder.Services.AddScoped(sp => sp.GetRequiredService<IHttpClientFactory>().CreateClient("ApiClient"));
builder.Services.AddScoped<ModelCatalog>();
builder.Services.AddScoped<CalibrationProfileProvider>();
builder.Services.AddScoped<CalibrationSettingsService>();
builder.Services.AddScoped<RecommendationEngine>();
builder.Services.AddRadzenComponents();
await builder.Build().RunAsync();
