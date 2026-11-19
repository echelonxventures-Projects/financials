// Run a DCF from JavaScript (Node 18+), no npm packages.
//   node examples/node/valuation.mjs                 # via the CLI (stdin/stdout JSON)
//   FIN_API_URL=http://127.0.0.1:8000 node examples/node/valuation.mjs   # via the REST API
import { execFileSync } from "node:child_process";

const config = {
  name: "Acme (from JavaScript)",
  model_type: "dcf",
  periods: { start_year: 2025, years: 5, type: "annual" },
  assumptions: {
    drivers: {
      revenue: { value: 10_000_000 },
      revenue_growth: { value: [0.3, 0.25, 0.2, 0.15, 0.1] },
    },
  },
};

async function viaApi(url) {
  const headers = { "Content-Type": "application/json" };
  if (process.env.FIN_API_KEY) headers["X-API-Key"] = process.env.FIN_API_KEY;
  const res = await fetch(`${url}/dcf`, { method: "POST", headers, body: JSON.stringify({ config }) });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  const body = await res.json();
  return { revenue: body.model_result.income_statement, dcf: body.dcf };
}

function viaCli() {
  const exe = process.env.FINANCIAL_MODEL ?? "financial-model";
  // Errors: non-zero exit and a one-line message on stderr (execFileSync throws).
  const out = execFileSync(exe, ["run", "-", "-o", "-"], { input: JSON.stringify(config), encoding: "utf8" });
  const result = JSON.parse(out);
  return { revenue: result.income_statement, dcf: result.analysis["DCF Valuation"] };
}

const { revenue, dcf } = process.env.FIN_API_URL ? await viaApi(process.env.FIN_API_URL) : viaCli();
for (const [period, lines] of Object.entries(revenue)) {
  console.log(`${period}  revenue ${Math.round(lines.Revenue).toLocaleString("en-US")}`);
}
console.log(`Enterprise value (Gordon): ${Math.round(dcf.ev_gordon).toLocaleString("en-US")}`);
console.log(`Equity value (Gordon):     ${Math.round(dcf.equity_gordon).toLocaleString("en-US")}`);
