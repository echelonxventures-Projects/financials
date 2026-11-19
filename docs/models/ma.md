# M&A model

`MergerModel(acquirer, target, ...)` combines two three-statement models.

- Purchase equity value = `offer_price_per_share` x `target_shares`
- Cash portion is debt-funded at `financing_rate`; stock portion issues acquirer shares at
  `acquirer_share_price` (default: year-1 EPS x `acquirer_pe`)
- Pro forma net income = both standalone net incomes + after-tax synergies - after-tax interest
- Synergies phase in by `synergy_phase_in`

`calculate_accretion_dilution()` returns year-1 figures plus `by_period` for every year.
`contribution_analysis()` shows each side's share of revenue, EBITDA and net income.

## Config

`financial-model ma config/ma_saas.yaml` (or `run`, `chart`, `validate`, `POST /ma`):

```yaml
model_type: ma
acquirer: acquirer.yaml     # path relative to this file, or an inline model config
target:                     # inline config (any three-statement / dcf / lbo model)
  model_type: three-statement
  periods: {start_year: 2024, years: 5}
  assumptions: {drivers: {revenue: {value: 8000000}}}
deal:
  offer_price_per_share: 18.0
  target_shares: 2000000
  cash_consideration_pct: 0.5   # stock_consideration_pct defaults to the remainder
  financing_rate: 0.065
  synergies: 1500000
  synergy_phase_in: [0.3, 0.6, 0.8, 1.0, 1.0]
  acquirer_shares: 10000000
  acquirer_pe: 25.0
```

Unknown deal terms are rejected. `--scenario NAME` is applied to each side that defines
that scenario. The API accepts inline acquirer/target configs only.
