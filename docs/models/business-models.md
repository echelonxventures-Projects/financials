# Business models: PaaS, B2B SaaS, B2C subscription

Three model types forecast a company from how it acquires and keeps customers rather
than from a revenue growth rate. Each one is a full three-statement model: the balance
sheet balances, the cash flow ties, and scenarios, YAML overlays, exports, charts, the
CLI and the API work the same way as for `three-statement`.

| `model_type` | Aliases | Revenue engine |
|---|---|---|
| `paas` | `platform`, `usage-based` | Sign-ups convert to paying accounts; usage per account compounds; revenue = platform fee + billable units x price |
| `b2b-saas` | `b2b`, `saas-b2b` | Ramped reps x quota x attainment books new ARR; existing ARR expands and churns; revenue = average ARR + services |
| `b2c-subscription` | `b2c`, `consumer`, `subscription` | Paid marketing / CAC buys subscribers, organic adds more, monthly churn removes them; revenue = subscribers x ARPU |

```bash
financial-model init --type b2b-saas -o b2b.yaml   # or paas, b2c-subscription
financial-model run b2b.yaml --scenario downside -o b2b.xlsx
```

Example configs: `config/paas.yaml`, `config/b2b_saas.yaml`, `config/b2c_subscription.yaml`.

Every default (drivers, the three-statement defaults each type changes, scenarios, the
template name) is data in `financial_model/models/templates/<model_type>.yaml`, not code.
Override per model in the model YAML, or ship your own defaults: put a
`<model_type>.yaml` in a directory listed in the settings key `models.template_paths`
(or `FIN_MODELS_TEMPLATE_PATHS`); it is used instead of the built-in one.

## What the engine replaces

Only three income-statement lines change. Everything else (R&D, G&A, SBC, D&A, working
capital, capex, debt, equity, tax) keeps its three-statement driver.

| Line | PaaS | B2B SaaS | B2C subscription |
|---|---|---|---|
| Revenue | usage + platform fees | average ARR + services | average subscribers x ARPU |
| COGS | units served x infra cost + `cogs_pct` x revenue | `cogs_pct` x subscription + `services_cogs_pct` x services | (`payment_fee_pct` + `cogs_pct`) x revenue |
| S&M | `marketing_spend` + `sm_pct` x revenue | reps x `rep_cost` + `marketing_spend` + `sm_pct` x revenue | `marketing_spend` + `sm_pct` x revenue |

`sm_pct` here means S&M *beyond* customer acquisition (enablement, brand, tools).

## Driver units

* Flows (`marketing_spend`, `new_signups`) are **per period**: with quarterly periods, enter quarterly spend.
* B2B and PaaS churn, expansion and usage growth are **annual** rates; B2C churn is **monthly**.
* ARPU, platform fees and per-account usage are **monthly**; ACV, quota and rep cost are **annual**.
* The engine converts rates to the period length, so the same drivers give the same
  annual new ARR whether the model runs annually, quarterly or monthly.

### PaaS

`starting_accounts`, `new_signups`, `signup_conversion`, `annual_account_churn`,
`usage_per_account` (starting value; later periods grow by `usage_growth`), `free_units`,
`price_per_unit`, `discount_pct`, `platform_fee`, `free_accounts`, `free_account_usage`,
`infra_cost_per_unit`, `marketing_spend`.

### B2B SaaS

`starting_customers`, `starting_acv`, `sales_reps`, `quota_per_rep`, `quota_attainment`,
`new_acv`, `rep_cost`, `marketing_spend`, `annual_logo_churn`, `annual_expansion`,
`services_pct`, `services_cogs_pct`.

### B2C subscription

`starting_subscribers`, `marketing_spend`, `paid_cac`, `organic_ratio`, `monthly_churn`,
`monthly_arpu`, `payment_fee_pct`, `new_subscriber_churn_exposure` (share of a period that
subscribers added in it are exposed to churn; 0.5 by default).

## Unit Economics table

Every exporter (Excel sheet, CSV file, JSON `analysis`) and the API return a
`Unit Economics` table next to the statements: customer roll-forward, ARR / run-rate,
NRR and GRR, gross and operating margin, annualised revenue growth, rule of 40, blended
CAC (total S&M / new customers), revenue per customer per month, LTV on a gross-margin
basis, LTV / CAC and CAC payback in months, plus model-specific rows (magic number,
infrastructure margin, paid vs organic subscribers, app-store fees).
