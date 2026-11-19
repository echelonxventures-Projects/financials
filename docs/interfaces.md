# CLI and API

## CLI

| Command | Purpose |
|---|---|
| `financial-model init --type {three-statement,dcf,lbo,budget,ma}` | Write a starter config |
| `financial-model run CONFIG [-s SCENARIO] [-o FILE]` | Run any model; output format follows the extension (`.xlsx`, `.json`, or a directory for CSVs). Exports include the model's analysis tables (DCF valuation & sensitivity, LBO sources/uses, debt schedule & returns) |
| `financial-model dcf CONFIG` | DCF valuation, FCF build and sensitivity table |
| `financial-model lbo CONFIG` | Sources & uses, debt schedule, MOIC and IRR |
| `financial-model ma CONFIG [-s SCENARIO] [-o FILE]` | M&A deal summary, accretion/dilution by year, contribution |
| `financial-model chart CONFIG [-o charts.html] [--theme dark] [--cdn]` | Interactive chart dashboard (one self-contained HTML file, or a directory of files) |
| `financial-model serve [--host] [--port] [--settings FILE]` | Start the REST API (see [Self-hosting](self-hosting.md)) |
| `financial-model fetch SOURCE [--provider NAME]` | Derive a model config from historical statements: your own files (`file` provider) or a remote provider such as `yfinance` (see [Data sources](data.md)) |
| `financial-model providers` | List the data providers (built-in and plugins) |
| `financial-model synthetic` | Generate a synthetic SaaS company config |
| `financial-model validate CONFIG` | Check structure and that the model balances (both sides for M&A) |
| `financial-model --version` | Print the version |

The config's `model_type` key selects the model class; `--type` overrides it within the
single-entity types (an M&A deal file cannot be run as a DCF and vice versa).
`python -m financial_model` is equivalent to the `financial-model` script.

## REST API

```bash
financial-model serve          # or: uvicorn financial_model.api.main:app
```

| Endpoint | Body | Returns |
|---|---|---|
| `POST /calculate` | `{config, scenario?}` | Statements, balance checks, `analysis` tables |
| `POST /dcf` | `{config, scenario?, wacc_range?, growth_range?}` | Model result, valuation, FCF build, optional sensitivity |
| `POST /lbo` | `{config, scenario?}` | Model result, returns, sources & uses, debt schedule |
| `POST /ma` | `{config, scenario?}` (inline `acquirer`/`target`) | Deal summary, accretion/dilution, contribution |
| `POST /charts` | `{config, scenario?, theme?, offline?}` | HTML chart dashboard (any model type) |
| `POST /export/excel` | `{config, scenario?}` | `.xlsx` download with analysis sheets |
| `POST /fetch` | `{ticker, years?, provider?}` | Statements from a remote data provider (default `data.default_provider`) |
| `POST /import` | `{name?, income_statement, balance_sheet?, cash_flow?, key_stats?, years?}` | A model config derived from statements you send (`{period: {line: value}}`) — post it to `/calculate` |
| `POST /synthetic` | `{industry?, years?, start_year?, ...}` | Synthetic statements |
| `GET /health`, `GET /models`, `GET /providers` | | Liveness + version; model types; data providers usable over the API |

Request `config` bodies use the same structure as the YAML files. Errors: 400 bad config,
401 missing/invalid `X-API-Key` (when configured), 413 body too large, 422 invalid ticker,
429 rate limited, 501 optional package not installed on the server, 502 data provider failure,
503 circuit breaker open. Interactive docs are
served at `/docs`.

## Python

```python
from financial_model.models import build_model
from financial_model.visualization import ChartBuilder
import yaml

model = build_model(yaml.safe_load(open("config/dcf_saas.yaml")))
result = model.calculate(scenario="upside")
model.export_excel("dcf.xlsx", result)        # + export_csv(dir), export_json(path)
tables = model.analysis_tables(result)        # {"DCF Valuation": {...}, "Sensitivity": DataFrame, ...}
ChartBuilder().export_dashboard(ChartBuilder().model_charts(model, result), "dcf.html")
```

Register your own model class with `financial_model.models.register_model("my-type", MyModel)`;
it then works with `model_type: my-type` in YAML, the CLI and the API.
