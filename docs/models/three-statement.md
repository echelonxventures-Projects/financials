# Three-statement model

`ThreeStatementModel` builds an integrated income statement, balance sheet and cash flow from
about thirty drivers. See `config/three_statement_saas.yaml` for a complete example.

```python
from financial_model.models.three_statement import ThreeStatementModel

model = ThreeStatementModel.from_yaml("config/three_statement_saas.yaml")
result = model.calculate(scenario="upside")
print(result.labelled(result.income_statement))
assert result.bs_balance_result.balanced and result.cf_balance_result.balanced
model.export_excel("model.xlsx", result)
```

YAML drivers overlay the defaults, so a config only lists what it changes. Custom line items go
under `statements`; a line with the same name as a default replaces it.
