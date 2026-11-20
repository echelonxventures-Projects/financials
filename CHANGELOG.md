# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Engines read statement lines through roles: `lines` (role -> line), `line_groups` and `balancing` (tie tolerance, solve passes and tolerances) in `three-statement.yaml` drive the balancer, DCF, LBO, M&A and business engines, so renaming a line the engines use is data (`lines: {revolver: Credit Facility}`). LBO close re-cut (`opening_at_close`), sweep working capital, exit debt and implied entry EBITDA; M&A contribution metrics; business-engine lines; output labels and analysis table names; and the `dcf` / `lbo` / `ma` console output (`console`) are definition data. Outputs are unchanged
- Charts are data: each definition's `charts` section says which charts a model has and what they plot (bar, line, waterfall, heatmap, pie; series from statement lines, engine tables or named values, with `divide_by`, `match`, `group`, overlays and `requires`), inherited through `extends`. New chart data sources with `register_chart_source`; waterfall values are safe expressions (`sum(pv_fcf) - net_debt`). The formula limits are settings (`formulas.max_length`, `formulas.max_depth`). Charts are unchanged
- The statements themselves are data: the three-statement layout (every line, rule or formula, order, total), the opening balance sheet and opening retained earnings are `structure`, `opening_balances` and `opening_retained_earnings` in `three-statement.yaml`; the budget's statements and per-department lines (`department_template`, `department_keys`); where the LBO debt-schedule lines go (`layout`). Formula language: `running(expr, start)` and named `expressions`. Workbook and chart styling (`presentation.excel`, `presentation.charts`), ticker patterns and parameter ranges (`validation`) and rate-limit defaults are settings. Outputs are unchanged
- Every customer-facing platform message (API errors, notes) is a template in the catalogue's `messages`, so wording and language are configuration; a replacement catalogue inherits messages it omits
- Built-in runtime settings are data (`financial_model/settings.yaml`), with a new `defaults` section for CLI options and API request fields; `start_year` defaults to the current year. Synthetic companies are profiles in `data/synthetic.yaml` (`extends`, add industries via `data.synthetic_paths`); statement aliases, line-label mappings and the driver-derivation policy are in `data/mappings.yaml` (`data.mapping_paths`), with fallbacks taken from the three-statement definition
- YAML-only model types: any definition without a Python class becomes a `model_type`, running on the nearest engine in its `extends` chain; `line_items` add, change (`add_to` subtotals) or remove lines; `analysis` tables of formulas. A safe formula language (`core/expressions.py`: `line`, `prev`, `delta`, `cumulative`, `opening`, `safe_div`...) usable in definitions, model configs and API requests. Example: `config/definitions/marketplace.yaml`
- Model defaults are data: `models/definitions/<model_type>.yaml` for three-statement, DCF (incl. sensitivity grid), LBO (incl. the debt tranche set), budget (departments), M&A (deal terms, allowed side types), the business models (via a shared `business.yaml`), model-type aliases and every `init` starter. Definitions inherit with `extends:`; `models.definition_paths` puts operator copies first. Model constructors take driver overrides by keyword (`DCFModel(beta=1.1)`) and expose drivers as attributes
- No commercial value in code: plans, prices, pay-to-use metering (included units, block or bill overage, flat or tiered unit prices, caps), limits, features, roles and permissions, account kinds and statuses, metrics and per-endpoint rules live in `platform/catalog.yaml`; runtime and billing-provider defaults (Stripe URL, tolerance, checkout fields, status map, webhook event handling) in `platform/settings.yaml`. Override inline under `platform:` or layer/replace catalogue files with `platform.catalog`. Pay-as-you-go plan, `GET /v1/invoice`, `platform invoice` and `platform sync-usage` (Stripe billing meters). `POST /v1/accounts` replaces `/v1/organizations`; `platform create-account` replaces `create-org`
- Commercial platform (`platform.enabled`, off by default): B2C self-serve sign-up, B2B organisations with members, roles and seats, a configurable plan catalogue (Free, Pro, Team, Business, Enterprise) enforced on every model endpoint, per-tenant API keys and signed login tokens, monthly usage metering, and pluggable billing (`manual`, `stripe` with signed webhooks, or a plugin). `/v1` account API, `financial-model platform` operator commands, `deploy/compose.platform.yaml`, `docs/commercial.md`. Standard library only (SQLite store, pluggable)
- Business-model types `paas`, `b2b-saas` and `b2c-subscription` (aliases `platform`, `b2b`, `b2c`...): a customer engine drives revenue, COGS and S&M of a full three-statement model; a `Unit Economics` analysis table (customers, ARR, NRR/GRR, CAC, LTV, LTV/CAC, payback, rule of 40) in every export and the API; `financial-model init --type` templates and example configs in `config/`
- Python 3.9+ with no upper bound (was 3.11+). CI tests 3.9 through 3.14, the newest stable `3.x` on Linux, macOS and Windows, the free-threaded 3.14t build, every dependency at its declared minimum, and (weekly) the newest pre-release dependencies. See `docs/compatibility.md`
- `scripts/min_constraints.py`: pip constraints pinning each runtime dependency to its floor
- Use from any language: `financial-model run - -o -` reads a JSON/YAML config on stdin and writes the result as JSON on stdout (`ma` and `validate` accept `-` too); config errors are a one-line `Error:` on stderr with exit 1 instead of a traceback
- `financial-model schema` and the committed `schemas/config.schema.json` (JSON Schema 2020-12 for model and M&A configs; editor autocomplete via yaml-language-server); `financial-model openapi` prints the API spec without a server
- `examples/` for JavaScript, Go, Ruby, C# and POSIX shell, each via the CLI and the REST API; a CI job checks that they all report the same valuation
- `financial_model.schema`: the config models shared by the API and the JSON Schema
- Container image follows the newest Python by default (`--build-arg PYTHON_VERSION=` to pin)

- Pluggable data providers (`financial_model.data.providers`): `file` reads your own statements offline (Excel workbook, directory of CSV/XLSX/JSON, or JSON — including the toolkit's own exports; tolerant of title rows, transposed sheets, `FY2023`/`Q1 FY2024`/date labels and accounting number formats); `yfinance` is now one provider among others; `register_provider()` and the `financial_model.data_providers` entry point for third-party sources
- `financial-model fetch SOURCE --provider`, `financial-model providers`; API `GET /providers`, `POST /import` (derive a config from statements in the request), `provider` on `POST /fetch`
- Model plugins via the `financial_model.models` entry point
- Install extras: core install no longer pulls fastapi, uvicorn, plotly or yfinance; `[api]`, `[charts]`, `[yfinance]`, `[all]`; missing extras fail with the exact install command (API: 501)
- Deployment recipes: systemd, launchd, Windows Task Scheduler, Compose (Docker / Podman / nerdctl), Kubernetes, Procfile + requirements.txt for PaaS; docs for running under hypercorn / gunicorn / granian
- `tests/conftest.py` skips optional-extra test modules when that extra is absent, so the suite runs on any install profile
- CI on Linux, macOS and Windows; a core-only install job; container builds with both Docker and Podman

### Changed

- `yfinance` floor is 0.2.1 (0.2.0 was never published, so `>=0.2` could not be installed at its minimum)
- Dropped the `pytest<9` cap

- Derived configs start projecting the year after the last historical period (was: the current year) and record the actual data source
- `data.default_provider` setting; rate limiting applies to every remote provider
- Removed the unused `rich`, `jinja2` and `httpx` runtime dependencies (`httpx` is a dev dependency)

### Fixed

- Text files are read and written as UTF-8 on every OS (Windows defaulted to cp1252); CLI output is ASCII-safe on legacy Windows consoles

## [0.3.0] - 2026-11-19

### Added

- `financial-model ma`, `chart`, `serve` and `--version` commands; `init --type ma`
- M&A deal configs (`model_type: ma`, `config/ma_saas.yaml`) with acquirer/target as paths or inline configs, scenario support, and validation of deal terms
- API: `POST /ma`, `POST /charts`; `analysis` tables in `/calculate`; FCF build in `/dcf`; version in `/health`
- Self-hosting settings (`config_loader`, `config/financial.example.yaml`, `docs/self-hosting.md`): bind address, port, workers, CORS, optional `X-API-Key`, request size limit, fetch rate limits
- `analysis_tables()` model hook: Excel/CSV/JSON exports now include DCF valuation, FCF build and sensitivity, LBO returns, sources & uses and debt schedule
- Chart dashboards for every model type (statements, margins, cash flow, DCF bridge & sensitivity heatmap, LBO debt paydown & returns, budget ARR/headcount, M&A accretion), self-contained HTML by default
- Shared model registry (`financial_model.models.registry`, `register_model`)

### Changed

- Single exporter implementation (`financial_model.exporters`) used by the models, CLI and API
- yfinance fetches validate the ticker first, go through a thread-safe rate limiter / circuit breaker, and raise `DataFetchError` when the provider fails or returns nothing
- API maps errors to 401/413/422/429/502/503 instead of 400 for everything
- Version is single-sourced from `financial_model.__version__`
- Docker image builds the package properly, runs as non-root with a healthcheck; CI installs `.[dev]` and smoke-tests the image

### Fixed

- `make test` crashed: a test module replaced `plotly` in `sys.modules` with mocks for the whole session
- Runtime-config env overrides mutated the built-in defaults; explicit settings paths returned a stale cached config
- `/export/excel` leaked a temp file per request
- Ticker validation rejected `BRK-B`, `RELIANCE.NS`, `^GSPC`, 6-digit BSE codes and alphanumeric TSE codes
- Excel exporter wrote to column 0 and overwrote its own header row
- `--type dcf` on an M&A config silently ran a default model

## [0.2.0] - 2026-11-11

### Added

- **Financial models**
  - Budget model — top-down and bottom-up budgeting scenarios
  - DCF (Discounted Cash Flow) model — multi-stage valuation with terminal value
  - LBO (Leveraged Buyout) model — buy-side and sell-side IRR analysis
  - Three-statement model — integrated income statement, balance sheet, and cash flow statement
  - M&A (Mergers & Acquisitions) model — accretion/dilution and synergy analysis
- **FastAPI REST API**
  - Endpoints for running each model against YAML config or JSON payloads
  - Health check and version endpoints
- **Click/Typer CLI**
  - `financial-model run` — execute a model from a config file
  - `financial-model export` — write results to Excel or CSV
  - `financial-model serve` — launch the FastAPI server
- **Config-driven architecture**
  - YAML configuration files for all models (sample configs included)
  - Pydantic-based validation of assumptions and drivers
- **Exporters**
  - Excel exporter with multiple sheets (statements, assumptions, outputs)
  - CSV exporter for flat-file consumption
- **Data tools**
  - Synthetic data generator for testing and demos
  - Market data loader via yfinance (historical prices, fundamentals)
- **Core engine**
  - Period-based timeline with driver interpolation
  - Line-item formula engine and statement balancer
- **Visualization**
  - Chart builder using Plotly (revenue trends, IRR waterfall, balance sheet)
- **Utilities**
  - Centralised error handling and validation
  - Rate limiter for API endpoints
