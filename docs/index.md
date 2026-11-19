# Financial Model Toolkit

Config-driven financial modeling in Python: an integrated three-statement model, DCF valuation,
LBO analysis, M&A accretion/dilution and a driver-based budget, all defined in YAML.

```bash
pip install -e ".[dev]"
financial-model init --type three-statement -o model.yaml
financial-model run model.yaml                      # print statements
financial-model run model.yaml -s upside -o out.xlsx
financial-model dcf config/dcf_saas.yaml
financial-model lbo config/lbo_saas.yaml
```

Start with [Modeling conventions](conventions.md): the sign rules and the opening balance sheet
explain every number the models produce.
