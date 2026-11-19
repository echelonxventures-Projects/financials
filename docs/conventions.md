# Modeling conventions

## Signs

| Statement | Rule |
|---|---|
| Income statement | Revenue positive, every expense negative, subtotals are plain sums |
| Balance sheet | Assets, liabilities, equity positive; accumulated depreciation negative |
| Cash flow | Inflows positive, outflows negative |

Percentage and magnitude drivers (`cogs_pct`, `capex`, `sbc`, `dividends`, `acquisitions`,
`interest_expense`) can be entered with either sign. The model applies the convention.
`equity_issuance` and `other_income` are signed: positive is an inflow.

## Opening balance sheet

Period-0 values of these drivers form the opening balance sheet:
`beginning_cash`, `ppe_gross`, `intangibles`, `goodwill`, `long_term_debt`, `current_debt`,
`dlt`, `common_stock`, `apic`. Working capital starts at zero. Opening retained earnings is
the plug that balances it, so it can be negative (an accumulated deficit).

## Roll-forwards

- Gross PP&E = opening + cumulative capex. D&A = `depreciation_rate` x gross PP&E.
- Goodwill = opening + cumulative acquisitions.
- APIC = opening + cumulative SBC + equity issuance.
- Retained earnings = prior + net income - dividends.

## Balancing

Each period, implied cash = liabilities (ex-revolver) + equity - non-cash assets. If that is at
least `min_cash`, the revolver is zero. Otherwise cash is held at `min_cash` and the revolver is
drawn for the shortfall. The cash flow statement is reported as tied only when operating +
investing + financing flows equal the balance-sheet cash movement.

## Calculation passes

Statements are calculated IS, BS, CF, then balanced, and the pass repeats until values stop
changing. This lets interest depend on last year's debt balance without circular references.

## Scenarios

`calculate(scenario="upside")` or `--scenario upside` replaces the named drivers with the
scenario's values. Unknown scenario names raise an error.

## Periods

`periods.type` is `annual`, `quarterly` or `monthly`; `years` is the number of years and
`count` optionally sets the exact number of periods. Days-outstanding drivers assume annual
flows, so use annual periods for the three-statement family.
