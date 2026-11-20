# Financial Model Toolkit

Extensible, config-driven financial modeling in Python. Build three-statement models, DCF valuations, LBO analyses, M&A accretion/dilution, and driver-based budgets — all from YAML configuration.

## Features

- **Three-Statement Model**: Integrated IS/BS/CF with automatic balancing (cash/revolver/RE plugs)
- **DCF Valuation**: WACC (CAPM), terminal value (Gordon Growth + Exit Multiple), sensitivity tables
- **LBO Model**: Sources & uses, multi-tranche debt schedule (interest, amortization, cash sweep), IRR/MOIC
- **M&A Model**: Accretion/dilution by year, synergy phase-in, contribution analysis
- **Budget Model**: Bottom-up driver trees (ARR, headcount, departments), quarterly granularity
- **Business Models**: PaaS (usage-based), B2B SaaS (sales-led) and B2C subscription companies, forecast from customer acquisition, churn, expansion and pricing into tied three statements, with a Unit Economics table (CAC, LTV, NRR, payback, rule of 40). See `docs/models/business-models.md`
- **Config-Driven**: All assumptions, line items, and topology in YAML — no code changes needed. Every model's defaults live in `models/definitions/*.yaml`, and a new model type is just a YAML file with safe formulas (`docs/models/definitions.md`)
- **Data Pipeline**: Derive drivers from your own CSV / Excel / JSON statements (offline), from Yahoo Finance (optional), or from any plugin provider; or generate synthetic data
- **Multi-Format Export**: Excel (formatted), CSV, JSON — including each model's analysis tables (DCF valuation & sensitivity, LBO sources/uses, debt schedule & returns, M&A accretion)
- **Charts**: Interactive Plotly dashboards for every model type, as one self-contained HTML file
- **Interfaces**: CLI, REST API (FastAPI), Python library
- **Agnostic**: Lean core install with optional extras; Linux / macOS / Windows; any ASGI server; recipes for systemd, launchd, Windows, any OCI runtime (Docker, Podman...), Compose, Kubernetes and PaaS; data providers and model types plug in via entry points
- **Commercial platform** (opt-in): run it as a hosted PaaS API with B2C self-serve accounts, B2B organisations (members, roles, seats), plan entitlements and quotas, per-tenant API keys, usage metering and pluggable billing (manual invoicing or Stripe). See `docs/commercial.md`
- **Self-hosted**: optional API key, CORS, request limits and fetch rate limits via a settings file or `FIN_*` env vars; no CDN or cloud services required
- **Testing**: Property-based (Hypothesis), hand-derived golden master, statement tie-out checks, CI/CD

## Quick Start

```bash
# Install (core: models, exports, CLI). Add extras as needed: [api] [charts] [yfinance] or [all]
cd financial_model
pip install -e ".[all]"

# Generate a SaaS three-statement model config
financial-model init --type three-statement --industry saas --years 5 -o my_model.yaml

# Or a PaaS / B2B SaaS / B2C subscription business from go-to-market drivers
financial-model init --type b2b-saas -o b2b.yaml   # paas | b2b-saas | b2c-subscription

# Run the model
financial-model run my_model.yaml --output model.xlsx

# Run a scenario, export JSON or CSVs (format follows the extension)
financial-model run my_model.yaml --scenario upside --output model.json

# DCF valuation and LBO analysis (use a matching template or the examples in config/)
financial-model dcf config/dcf_saas.yaml
financial-model lbo config/lbo_saas.yaml

# Check a config loads, validates and balances
financial-model validate my_model.yaml

# M&A accretion / dilution (acquirer + target + deal terms in one file)
financial-model ma config/ma_saas.yaml -o deal.xlsx

# Interactive chart dashboard for any model
financial-model chart config/lbo_saas.yaml -o lbo.html

# Start the REST API (any OS; or systemd / launchd / Windows / containers / k8s / PaaS: docs/deployment.md)
financial-model serve

# Derive a model from your own statements (Excel / CSV / JSON), or a public company
financial-model fetch my_company.xlsx -o my_model.yaml
financial-model fetch SNOW --provider yfinance --years 5 -o snow_data.yaml

# Generate synthetic SaaS data
financial-model synthetic --industry saas --years 5 -o synthetic.yaml
```

## Project Structure

```
financial_model/
├── financial_model/
│   ├── core/           # Engine: periods, line items, statements, balancing
│   ├── models/         # ThreeStatement, DCF, LBO, M&A, Budget
│   ├── models/registry # model_type -> class mapping shared by CLI and API
│   ├── data/           # Pluggable providers (file, yfinance, plugins), driver derivation, synthetic data
│   ├── exporters/      # Excel, CSV, JSON
│   ├── visualization/  # Plotly chart builder and HTML dashboards
│   ├── utils/          # Validation, rate limiting / circuit breaker, error types
│   ├── config_loader.py# Runtime settings (defaults < YAML < FIN_* env)
│   ├── cli/            # Typer CLI
│   ├── api/            # FastAPI REST API
│   ├── platform/       # Commercial mode: tenants, plans, keys, metering, billing, /v1 API
├── deploy/             # systemd, launchd, Windows, Compose, Kubernetes recipes
├── config/             # Example model configs (three-statement, DCF, LBO, budget, M&A) + settings example
├── docs/               # MkDocs documentation
├── tests/              # Unit, property-based, golden master tests
├── .github/workflows/  # CI/CD
└── pyproject.toml
```

## Configuration

Models are defined entirely in YAML. Example (`config/three_statement_saas.yaml`):

```yaml
name: "SaaS Three-Statement Model"
periods:
  start_year: 2024
  years: 5
  type: "annual"

assumptions:
  drivers:
    revenue:
      value: 10000000
      description: "Starting revenue"
      unit: "$"
    revenue_growth:
      value: [0.35, 0.30, 0.25, 0.20, 0.15]
      description: "YoY growth (decelerating)"
      unit: "%"
    # ... all drivers configurable

  scenarios:
    base: {}
    upside:
      revenue_growth: [0.45, 0.40, 0.35, 0.30, 0.25]
    downside:
      revenue_growth: [0.20, 0.15, 0.10, 0.08, 0.05]
```

## Python API

```python
from financial_model.models.three_statement import ThreeStatementModel
from financial_model.models.dcf import DCFModel
from financial_model.models.lbo import LBOModel
from financial_model.core.period import Period, PeriodIndex, PeriodType

# Three-statement
model = ThreeStatementModel()
model.set_periods(PeriodIndex.generate(start_year=2024, years=5))
result = model.calculate()            # or model.calculate(scenario="upside")
print(result.labelled(result.income_statement))
model.export_excel("output.xlsx", result)

# DCF
dcf_model = DCFModel()
dcf_model.set_periods(...)
result = dcf_model.calculate()
dcf_result = dcf_model.calculate_dcf(result)
print(f"EV: ${dcf_result['ev_gordon']:,.0f}")
print(f"Price: ${dcf_result['price_gordon']:,.2f}")

# LBO
lbo_model = LBOModel()
lbo_model.set_periods(...)
result = lbo_model.calculate()
returns = lbo_model.calculate_returns(result)
print(f"MOIC: {returns['moic']:.2f}x, IRR: {returns['irr']:.2%}")
```

## REST API

```bash
# Start server (host/port/API key/CORS from settings; see docs/self-hosting.md)
financial-model serve

# Calculate model
curl -X POST http://localhost:8000/calculate \
  -H "Content-Type: application/json" \
  -d '{"config": {...}, "scenario": "base"}'

# DCF valuation
curl -X POST http://localhost:8000/dcf \
  -H "Content-Type: application/json" \
  -d '{"config": {...}, "wacc_range": [0.08, 0.12], "growth_range": [0.02, 0.04]}'
```

Also `POST /lbo`, `/ma`, `/charts` (HTML dashboard), `/export/excel`, `/fetch`, `/synthetic`
and `GET /health`, `/models`. Full reference: `docs/interfaces.md`.

## Other languages

Use it from JavaScript, Go, Ruby, C#, Java, R, shell or anything else, with no Python code:

```bash
financial-model run - -o - < config.json > result.json   # CLI as a JSON filter (stdin -> stdout)
financial-model serve                                    # REST API; OpenAPI spec at /openapi.json
financial-model schema                                   # JSON Schema for configs (editors, validators)
```

Tested examples for five languages are in [`examples/`](examples/); see
[docs/other-languages.md](docs/other-languages.md).

## Testing

```bash
# Run all tests
pytest tests -v

# With coverage
pytest tests --cov=financial_model --cov-report=term-missing

# Property-based tests (Hypothesis)
pytest tests/test_core.py::TestProperties -v
```

## Extending

### Add a New Model Type

1. Create `financial_model/models/my_model.py` inheriting from `ThreeStatementModel`
2. Add model-specific assumptions in `__init__` and set a `model_type`
3. Add custom calculation methods
4. Register it once: add it to `MODEL_CLASSES` in `financial_model/models/registry.py` (or call
   `register_model("my-type", MyModel)`) — the CLI, API and `model_type:` in YAML pick it up
5. Optionally override `analysis_tables(result)` to add sheets/tables to every export

### Add Custom Line Items

In YAML config under `statements`:

```yaml
statements:
  - type: "income_statement"
    line_items:
      - name: "Custom Metric"
        line_type: "calculated"
        statement_type: "income_statement"
        calculation_rule: "pct_of_revenue"
        calculation_params:
          driver: "custom_pct"
        driver:
          name: "custom_pct"
          value: 0.05
          description: "Custom % of revenue"
          unit: "%"
        order: 95
```

## Conventions

- **FAST Standard** compliant: Flexible, Appropriate, Structured, Transparent
- **Sign convention**: Revenue positive, expenses negative, assets positive, liabilities/equity positive. Percentage and magnitude drivers can be entered with either sign
- **Opening balance sheet**: built from the period-0 cash, PP&E, debt and paid-in capital drivers; opening retained earnings is the plug
- **Config overlay**: YAML drivers overlay model defaults; `model_type` selects the model class
- **Balancing**: Cash is the plug, floored at `min_cash`; the revolver is drawn for any shortfall. The cash flow must foot to the balance-sheet cash movement
- **Periods**: Annual, quarterly, monthly supported via `PeriodType`
- **Scenarios**: Override any driver per scenario (base, upside, downside, custom)

See `docs/` (or `mkdocs serve`) for the full conventions and per-model mechanics.

## Requirements

- Python 3.9 or newer (no upper bound; CI covers 3.9 through the newest release, including free-threaded builds) on Linux, macOS or Windows. See [docs/compatibility.md](docs/compatibility.md)
- Core: pandas, numpy, openpyxl, pydantic, pyyaml, typer
- Optional: `[api]` fastapi + uvicorn, `[charts]` plotly, `[yfinance]` yfinance

## License

MIT