# DCF model

`DCFModel` adds WACC (CAPM), unlevered free cash flow and two terminal values.

- Cost of equity = `risk_free_rate` + `beta` x `equity_risk_premium`
- WACC = cost of equity x (1 - `target_debt_ratio`) + after-tax `cost_of_debt` x `target_debt_ratio`
- FCF = EBIT x (1 - tax) + D&A - capex + change in working capital. SBC is treated as a real expense.
- Terminal value: Gordon growth (`terminal_growth`) and exit multiple (`exit_multiple` x final-year EBITDA)
- Equity value = EV - net debt at the valuation date (opening debt less `beginning_cash`)
- Price per share uses `shares_outstanding`

`sensitivity_analysis()` returns equity value across a WACC x terminal-growth grid.
