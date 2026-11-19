"""
Historical data for deriving model drivers.

Sources are pluggable (see ``financial_model.data.providers``): ``file`` reads your own
CSV / Excel / JSON statements offline, ``yfinance`` pulls public filings (optional extra),
and third-party providers register via the ``financial_model.data_providers`` entry point.
"""

from financial_model.data.loader import DataLoader, derive_drivers_from_financials, load_public_company_data
from financial_model.data.providers import (
    DataProvider,
    FileProvider,
    YFinanceProvider,
    available_providers,
    fetch_statements,
    get_provider,
    register_provider,
    statements_from_mapping,
)
from financial_model.data.synthetic import (
    generate_consumer_company,
    generate_manufacturing_company,
    generate_saas_company,
)

__all__ = [
    "DataLoader",
    "DataProvider",
    "FileProvider",
    "YFinanceProvider",
    "available_providers",
    "derive_drivers_from_financials",
    "fetch_statements",
    "get_provider",
    "load_public_company_data",
    "register_provider",
    "statements_from_mapping",
    "generate_saas_company",
    "generate_consumer_company",
    "generate_manufacturing_company",
]
