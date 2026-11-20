# Model definitions: defaults and new models as data

Every model's defaults live in YAML, not in Python: one file per model type in
`financial_model/models/definitions/<model_type>.yaml`. A definition can also add, change
or remove line items with formulas, so **a new model type needs only a YAML file**.

| Built-in definition | Holds |
|---|---|
| `three-statement.yaml` | every driver's default value, description and unit; aliases (`3s`, `ts`...); `init` starters |
| `dcf.yaml` (extends three-statement) | WACC / terminal-value drivers, the sensitivity grid |
| `lbo.yaml` (extends three-statement) | deal and debt drivers, **the tranche set** (add a seller note or a unitranche by adding an entry) |
| `budget.yaml` | departments built by default and the seed for new departments |
| `ma.yaml` | deal-term defaults, which model types may be acquirer/target, the loss-making share-price fallback |
| `business.yaml` (abstract) | Unit Economics table name and row labels shared by the business models |
| `paas.yaml`, `b2b-saas.yaml`, `b2c-subscription.yaml` (extend business) | engine drivers, KPI rows and labels, starters |

## Changing defaults

Put your own `<model_type>.yaml` in a directory listed in the settings key
`models.definition_paths` (or `FIN_MODELS_DEFINITION_PATHS`, comma-separated). Files there
are found before the built-in ones. Use `extends` to inherit everything and change only
what differs:

```yaml
# my-definitions/dcf.yaml
model_type: dcf
extends: three-statement
drivers:
  terminal_growth: {value: 0.03, description: Long-run growth, unit: "%"}
sensitivity: {wacc_range: [0.06, 0.14], growth_range: [0.0, 0.05], steps: 9}
```

Merging rules: mappings deep-merge, lists and scalars are replaced, and `null` deletes an
inherited entry. `aliases`, `abstract` and `starter` belong to one file and are not inherited.

In Python, keyword arguments override any driver (`DCFModel(beta=1.1)`), and drivers read as
attributes (`model.beta`).

## New model types without code

Any definition file whose `model_type` has no Python class becomes a model type. It runs on
the nearest engine in its `extends` chain (`three-statement`, `dcf`, `lbo`, `budget`, a
business model, or another definition):

```yaml
# my-definitions/marketplace.yaml
model_type: marketplace
aliases: [two-sided]
extends: three-statement
name: Marketplace Model
drivers:
  starting_gmv: {value: 50000000, unit: $}
  gmv_growth: {value: [0.6, 0.45, 0.35], unit: "%"}
  take_rate: {value: 0.12, unit: "%"}
  avg_order_value: {value: 80, unit: $}
  cost_per_order: {value: 0.9, unit: $}
line_items:
  income_statement:
    GMV: {formula: "prev('GMV', starting_gmv) * (1 + gmv_growth) if t > 0 else starting_gmv", order: 5}
    Orders: {formula: "line('GMV') / avg_order_value", order: 6, unit: "#"}
    Revenue: {formula: "line('GMV') * take_rate"}                 # replaces the growth-rate revenue
    COGS: {formula: "-line('Orders') * cost_per_order - line('Revenue') * cogs_pct"}
    Revenue Growth: null                                          # removes an inherited line
analysis:
  Marketplace KPIs:
    Take Rate: "safe_div(line('Revenue'), line('GMV'))"
    Contribution per Order: "safe_div(line('Gross Profit'), line('Orders'))"
```

Then `model_type: marketplace` (or `two-sided`) works everywhere: `financial-model run`,
`init --type marketplace`, the REST API, exports and charts. The statements still balance
because the engine's balance sheet and cash flow logic is inherited. A full copy is in
`config/definitions/marketplace.yaml`.

Line-item fields: `formula`, `order`, `unit`, `format`, `description`, `is_total`,
`children`, `line_type` (`input` with `driver: <name>`, `aggregate`, `link`...), and
`add_to: [<total line>]` to include the line in a subtotal. A spec for an existing line
changes only the fields it gives.

`analysis` tables are evaluated after the model runs and appear in every export and API
response next to the engine's own tables.

`abstract: true` marks a definition that only exists to be extended. A definition without a
`starter` gets one from its own drivers for `financial-model init`.

## Formula language

Formulas are single expressions evaluated per period, also allowed in model configs
(`statements[].line_items[].formula`) and API requests. They are parsed and checked, never
`eval`-ed: no attribute access, subscripts, imports, lambdas or comprehensions.

| Element | Meaning |
|---|---|
| `revenue_growth` | a driver's value this period |
| `t`, `n_periods`, `periods_per_year`, `year` | period index (0-based), count, frequency, year |
| `line('Name', offset=0)` | a line's value; `offset=-1` is the previous period |
| `prev('Name', default=0)` | previous period's value, `default` in the first period |
| `opening('Name')`, `delta('Name')` | opening balance; change versus the previous period |
| `cumulative('Name')` | running total from the first period |
| `driver('name', offset)` | a driver in another period |
| `total('A', 'B', ...)` | sum of lines |
| `safe_div(a, b, default=0)`, `clamp(x, lo, hi)`, `min`, `max`, `abs`, `round` | helpers |
| `+ - * / // % **`, comparisons, `and or not`, `a if c else b` | operators (`x / 0` is 0) |

## Starters

`financial-model init --type <t> --industry <variant>` writes the definition's `starter`:
named `variants` of a full model config, plus rules for the requested years
(`period_paths`, `truncate_to_years`, `cap_at_years`). Unknown industries use
`fallback_variant`.
