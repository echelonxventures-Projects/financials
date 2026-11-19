# Contributing to Financial Model Toolkit

## Setup

1. **Python 3.15+** is required.
2. Clone the repo and install in editable mode with test dependencies:

   ```bash
   pip install -e ".[test]"
   ```

3. (Optional) Enable pre-commit hooks: `pre-commit install`.

## Running Tests

```bash
# Full suite
python3 -m pytest tests/ -v

# With coverage
python3 -m pytest tests/ --cov=financial_model --cov-report=term-missing
```

## Coding Conventions

* Follow **PEP 8** (line-length 120 as configured in `pyproject.toml`).
* **Type hints are required** on all public functions and methods.
* **No hardcoded magic numbers** — use `CONFIG` constants (see `config/*.yaml` and `financial_model/config/`).
* Financial sign convention: revenue/cash inflows positive; expenses/outflows negative.
* Run `ruff check . && ruff format . && mypy financial_model/` before committing.

## Project Structure

```
financial_model/
├── financial_model/          # Package
│   ├── core/                 # Engine: periods, line items, statements, balancing
│   ├── models/               # ThreeStatement, DCF, LBO, M&A, Budget
│   ├── data/                 # Data loaders (yfinance) and synthetic generators
│   ├── cli/                  # Typer CLI commands
│   ├── api/                  # FastAPI REST endpoints
│   ├── exporters/            # Excel, CSV, JSON writers
│   ├── utils/                # Shared helpers
│   └── visualization/        # Plotly chart builders
├── config/                   # Example YAML model configs
├── docs/                     # MkDocs documentation
├── tests/                    # Unit, property-based & golden-master tests
└── pyproject.toml            # Dependencies & tool config
```

## Submitting a Pull Request

1. Fork the repo and create a feature branch from `main`.
2. Add tests for new behaviour; ensure `pytest` passes at 100% coverage.
3. Update `docs/` if you change public interfaces or add model types.
4. Open a PR against `main` with a clear title and description of changes.
5. CI runs `ruff`, `mypy`, and the full test suite — all checks must pass.
