# Run a DCF from Ruby (standard library only; Ruby 2.6+).
#   ruby examples/ruby/valuation.rb                                    # via the CLI
#   FIN_API_URL=http://127.0.0.1:8000 ruby examples/ruby/valuation.rb  # via the REST API
require "json"
require "net/http"
require "open3"
require "uri"

config = {
  name: "Acme (from Ruby)",
  model_type: "dcf",
  periods: { start_year: 2025, years: 5, type: "annual" },
  assumptions: { drivers: {
    revenue: { value: 10_000_000 },
    revenue_growth: { value: [0.3, 0.25, 0.2, 0.15, 0.1] }
  } }
}

if ENV["FIN_API_URL"]
  uri = URI("#{ENV['FIN_API_URL']}/dcf")
  headers = { "Content-Type" => "application/json" }
  headers["X-API-Key"] = ENV["FIN_API_KEY"] if ENV["FIN_API_KEY"]
  res = Net::HTTP.post(uri, JSON.generate(config: config), headers)
  abort "#{res.code}: #{res.body}" unless res.is_a?(Net::HTTPSuccess)
  body = JSON.parse(res.body)
  revenue, dcf = body["model_result"]["income_statement"], body["dcf"]
else
  exe = ENV.fetch("FINANCIAL_MODEL", "financial-model")
  out, err, status = Open3.capture3(exe, "run", "-", "-o", "-", stdin_data: JSON.generate(config))
  abort err unless status.success? # errors: one line on stderr, non-zero exit
  result = JSON.parse(out)
  revenue, dcf = result["income_statement"], result["analysis"]["DCF Valuation"]
end

revenue.each { |period, lines| puts format("%s  revenue %.0f", period, lines["Revenue"]) }
puts format("Enterprise value (Gordon): %.0f", dcf["ev_gordon"])
puts format("Equity value (Gordon):     %.0f", dcf["equity_gordon"])
