# Data sources

`financial-model fetch SOURCE` (and `POST /fetch`, `POST /import`) turns historical
statements into a model config whose drivers (growth, margins, working-capital days, capex,
D&A, tax...) are derived from the history. Projections start the year after the last
historical period. Where the statements come from is pluggable.

```bash
financial-model providers                       # list what is available
financial-model fetch my_company.xlsx           # a path means the `file` provider
financial-model fetch ./statements/             # a directory of files
financial-model fetch SNOW --provider yfinance  # remote provider (needs the [yfinance] extra)
```

Without `--provider`, an existing local path uses `file`; anything else uses the settings
value `data.default_provider` (default `yfinance`; set `FIN_DATA_DEFAULT_PROVIDER` to change it).

## `file`: your own statements

| Layout | Example |
|---|---|
| One workbook, a sheet per statement | `acme.xlsx` with sheets `Income Statement`, `Balance Sheet`, `Cash Flow` (also `P&L`, `IS`, `BS`, `CF`...) |
| A directory, a file per statement | `income_statement.csv`, `balance_sheet.xlsx`, `cash_flow.json` (+ optional `key_stats.csv`) |
| One JSON file | `{"income_statement": {"FY2023": {"Revenue": 1000, ...}}, "balance_sheet": {...}, "cash_flow": {...}}` |

The toolkit's own exports (`run -o model.xlsx`, `-o model.json`, `-o csv_dir/`) are valid
inputs, so you can round-trip a model through the file provider.

The reader is tolerant of real-world spreadsheets:

- title or note rows above the header are skipped
- periods can run across the top or down the first column
- period labels: `2023`, `FY2023`, `Q1 FY2024`, `2023-12-31`, `Dec 2023`
- numbers: `1,200`, `$1,200`, `(300)` for negatives; blanks are ignored
- line names: the model's (`Revenue`, `COGS`, `Cash & Cash Equivalents`...) or provider-style
  names (`Total Revenue`, `Cost Of Revenue`, `Cash And Cash Equivalents`...)

Only the income statement is required. The `file` provider is CLI and Python only; the API
never reads server files. Over HTTP, post the statements to `POST /import` instead.

## `yfinance`

Yahoo Finance via the `yfinance` package (`pip install ".[yfinance]"`). The ticker is
validated before any network call (`BRK-B`, `RELIANCE.NS`, `7203.T`, `^GSPC` are accepted).
Remote providers are rate limited, with a circuit breaker; see `data.rate_limit` in
[Self-hosting](self-hosting.md).

## Adding a provider

```python
import pandas as pd
from financial_model.data import DataProvider, register_provider

class WarehouseProvider(DataProvider):
    name = "warehouse"
    description = "Statements from our data warehouse"
    remote = True          # rate limited + circuit breaker
    allow_in_api = True    # selectable via POST /fetch

    def fetch(self, source: str, years: int) -> dict:
        ...  # return {"income_statement": df, "balance_sheet": df, "cash_flow": df, "key_stats": {}}

register_provider(WarehouseProvider)
```

Rows are line items and columns are periods. To share it as a package without touching
this repo, publish an entry point:

```toml
[project.entry-points."financial_model.data_providers"]
warehouse = "my_pkg.providers:WarehouseProvider"
```

Model classes plug in the same way under `financial_model.models`. A plugin that fails to
import is logged and skipped; it never breaks the toolkit.


## Mappings and synthetic profiles are data

* `financial_model/data/mappings.yaml`: which sheet/file names count as each statement,
  how provider line labels map to the model's lines (e.g. `Total Revenue` -> `Revenue`),
  and how drivers are derived from history (long-run growth, fade, clipping). Add your own
  labels (another language, your ERP's chart of accounts) in a file listed in
  `data.mapping_paths`; it is merged over the built-in one.
* `financial_model/data/synthetic.yaml`: the synthetic company profiles behind
  `financial-model synthetic --industry <name>`. Profiles can `extends` another; add more
  via `data.synthetic_paths`.
