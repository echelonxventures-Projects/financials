# LBO model

`LBOModel` layers a buyout on the three-statement model.

**At close**

- EV = `entry_multiple` x `entry_ebitda`. With `entry_ebitda: 0` the model's first-year EBITDA is used.
- Uses = EV + fees (`fees_pct`) + refinancing of existing `long_term_debt` / `current_debt`.
- Sources = sponsor equity (`equity_contribution` of uses) + funded debt, allocated to Term Loan A,
  Term Loan B, subordinated and mezzanine in proportion to their `*_pct` drivers.
- The revolver is an undrawn commitment (`revolver_pct`), drawn only by the balance-sheet plug.
- The opening balance sheet is re-cut: old debt and paid-in capital removed, sponsor equity in
  APIC, retained earnings reset to zero, goodwill as the plug.

**Each year**

- Interest = rate x opening balance, per tranche.
- Term Loans A and B amortise by `tla_amort` / `tlb_amort` of original principal.
- `sweep_pct` of cash above `min_cash` prepays Term Loan A, then B.

**Exit**

After `hold_years` (capped at the projection length): equity = `exit_multiple` x EBITDA - net debt.
`calculate_returns()` gives MOIC and IRR; `debt_schedule()` and `sources_uses()` give the tables.
The model raises an error if entry EBITDA is not positive.
