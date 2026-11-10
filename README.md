# Financial Model Toolkit

Extensible, config-driven financial modeling in Python. Build three-statement models, DCF valuations, LBO analyses, M&A accretion/dilution, and driver-based budgets — all from YAML configuration.

## Features

- **Three-Statement Model**: Integrated IS/BS/CF with automatic balancing (cash/revolver/RE plugs)
- **DCF Valuation**: WACC (CAPM), terminal value (Gordon Growth + Exit Multiple), sensitivity tables
- **LBO Model**: Sources & uses, multi-tranche debt, cash sweep, IRR/MOIC waterfall
- **M&A Model**: Accretion/dilution, synergy phase-in, contribution analysis
- **Budget Model**: Bottom-up driver trees (ARR, headcount, departments), quarterly granularity
- **Config-Driven**: All assumptions, line items, and topology in YAML — no code changes needed
- **Data Pipeline**: Fetch public company data (yfinance) or generate synthetic industry data
- **Multi-Format Export**: Excel (formatted), CSV, JSON, HTML charts
- **Interfaces**: CLI, REST API (FastAPI), Python library
- **Testing**: Property-based (Hypothesis), golden master vs Excel, CI/CD

## Quick Start

```bash
# Install
cd financial_model
pip install -e ".[dev]"

# Generate a SaaS three-statement model config
financial-model init --type three-statement --industry saas --years 5 -o my_model.yaml

# Run the model
financial-model run my_model.yaml --output model.xlsx

# Run DCF valuation
financial-model dcf my_model.yaml

# Run LBO analysis
financial-model lbo my_model.yaml

# Fetch public company data
financial-model fetch SNOW --years 5 -o snow_data.yaml

# Generate synthetic SaaS data
financial-model synthetic --industry saas --years 5 -o synthetic.yaml
```

## Project Structure

```
financial_model/
├── financial_model/
│   ├── core/           # Engine: periods, line items, statements, balancing
│   ├── models/         # ThreeStatement, DCF, LBO, M&A, Budget
│   ├── data/           # Loaders (yfinance), synthetic generators
│   ├── cli/            # Typer CLI
│   ├── api/            # FastAPI REST API
│   └── config/         # YAML templates
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
model.set_periods(PeriodIndex([Period(year=2024+i, period_type=PeriodType.ANNUAL) for i in range(5)]))
result = model.calculate()
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
# Start server
uvicorn financial_model.api.main:app --reload

# Calculate model
curl -X POST http://localhost:8000/calculate \
  -H "Content-Type: application/json" \
  -d '{"config": {...}, "scenario": "base"}'

# DCF valuation
curl -X POST http://localhost:8000/dcf \
  -H "Content-Type: application/json" \
  -d '{"config": {...}, "wacc_range": [0.08, 0.12], "growth_range": [0.02, 0.04]}'
```

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
2. Add model-specific assumptions in `__post_init__`
3. Add custom calculation methods
4. Register in `financial_model/models/__init__.py`

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
          value: 0.05
          description: "Custom % of revenue"
          unit: "%"
        order: 95
```

## Conventions

- **FAST Standard** compliant: Flexible, Appropriate, Structured, Transparent
- **Sign convention**: Revenue positive, expenses negative, assets positive, liabilities/equity positive
- **Balancing**: Cash (asset plug), Revolver (liability plug), Retained Earnings (equity plug)
- **Periods**: Annual, quarterly, monthly supported via `PeriodType`
- **Scenarios**: Override any driver per scenario (base, upside, downside, custom)

## Requirements

- Python 3.11+
- pandas, numpy, pydantic, pyyaml, yfinance, fastapi, typer, plotly, openpyxl

## License

MIT