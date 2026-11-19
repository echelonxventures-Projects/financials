# Budget model

`BudgetModel` is a bottom-up operating budget, usually quarterly.

- ARR roll-forward: starting + new + expansion + churned (negative) = ending
- Revenue = average of starting and ending ARR
- Department expense = headcount x cost per head (negative)
- Net income = operating income x (1 - `tax_rate`)

Driver names use a department key: `engineering_headcount`, `gna_cost_per_head` (G&A),
`rd_headcount` (R&D). Cost per head is per period, so use a quarterly cost for quarterly periods.
`add_department()` adds a department; `variance_analysis()` compares actuals with budget.
