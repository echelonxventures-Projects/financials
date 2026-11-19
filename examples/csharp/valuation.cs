// Run a DCF from C# (.NET 10+: `dotnet run examples/csharp/valuation.cs`; no NuGet packages).
//   FIN_API_URL=http://127.0.0.1:8000 dotnet run examples/csharp/valuation.cs   # via the REST API
using System.Diagnostics;
using System.Text.Json.Nodes;

var config = new JsonObject
{
    ["name"] = "Acme (from C#)",
    ["model_type"] = "dcf",
    ["periods"] = new JsonObject { ["start_year"] = 2025, ["years"] = 5, ["type"] = "annual" },
    ["assumptions"] = new JsonObject
    {
        ["drivers"] = new JsonObject
        {
            ["revenue"] = new JsonObject { ["value"] = 10_000_000 },
            ["revenue_growth"] = new JsonObject { ["value"] = new JsonArray(0.3, 0.25, 0.2, 0.15, 0.1) },
        },
    },
};

JsonNode revenue, dcf;
var apiUrl = Environment.GetEnvironmentVariable("FIN_API_URL");
if (!string.IsNullOrEmpty(apiUrl))
{
    using var http = new HttpClient();
    var key = Environment.GetEnvironmentVariable("FIN_API_KEY");
    if (!string.IsNullOrEmpty(key)) http.DefaultRequestHeaders.Add("X-API-Key", key);
    var payload = new StringContent(new JsonObject { ["config"] = config }.ToJsonString(), System.Text.Encoding.UTF8, "application/json");
    var res = await http.PostAsync($"{apiUrl}/dcf", payload);
    var text = await res.Content.ReadAsStringAsync();
    if (!res.IsSuccessStatusCode) throw new Exception($"{(int)res.StatusCode}: {text}");
    var body = JsonNode.Parse(text)!;
    (revenue, dcf) = (body["model_result"]!["income_statement"]!, body["dcf"]!);
}
else
{
    var exe = Environment.GetEnvironmentVariable("FINANCIAL_MODEL") ?? "financial-model";
    var psi = new ProcessStartInfo(exe, ["run", "-", "-o", "-"])
    {
        RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
    };
    using var proc = Process.Start(psi)!;
    await proc.StandardInput.WriteAsync(config.ToJsonString());
    proc.StandardInput.Close();
    var stdout = await proc.StandardOutput.ReadToEndAsync();
    var stderr = await proc.StandardError.ReadToEndAsync();
    proc.WaitForExit();
    if (proc.ExitCode != 0) throw new Exception(stderr); // errors: one line on stderr
    var result = JsonNode.Parse(stdout)!;
    (revenue, dcf) = (result["income_statement"]!, result["analysis"]!["DCF Valuation"]!);
}

foreach (var (period, lines) in revenue.AsObject())
    Console.WriteLine($"{period}  revenue {lines!["Revenue"]!.GetValue<double>():F0}");
Console.WriteLine($"Enterprise value (Gordon): {dcf["ev_gordon"]!.GetValue<double>():F0}");
Console.WriteLine($"Equity value (Gordon):     {dcf["equity_gordon"]!.GetValue<double>():F0}");
