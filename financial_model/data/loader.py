"""
Statement standardisation, the yfinance provider, and the driver-derivation helper that
turns historical statements (from any provider) into model assumptions.

yfinance is optional (``pip install 'financial-model[yfinance]'``); the rest of this
module works without it.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

try:  # optional: only the yfinance provider needs it
    import yfinance as yf
except ImportError:  # pragma: no cover - exercised by the minimal-install tests
    yf = None

from financial_model.utils.optional import MissingDependencyError
from financial_model.utils.validation import TickerValidator


def normalize_ticker(ticker: str) -> str:
    """Upper-case and validate a ticker symbol; raises ``ValidationError`` if malformed."""
    result = TickerValidator.validate(ticker)
    result.raise_if_invalid()
    return ticker.strip().upper()


IS_MAPPING = {
    "Total Revenue": "Revenue",
    "Cost Of Revenue": "COGS",
    "Reconciled Cost Of Revenue": "COGS",
    "Gross Profit": "Gross Profit",
    "Research Development": "R&D Expense",
    "Research And Development": "R&D Expense",
    "Selling General Administrative": "SG&A Expense",
    "Selling General And Administration": "SG&A Expense",
    "Operating Expense": "Operating Expenses",
    "Operating Income": "Operating Income",
    "Interest Expense": "Interest Expense",
    "Pretax Income": "Pre-Tax Income",
    "Tax Provision": "Income Tax Expense",
    "Net Income": "Net Income",
    "Diluted EPS": "Diluted EPS",
    "Reconciled Depreciation": "Depreciation & Amortization",
    "Diluted Average Shares": "Diluted Shares",
}
BS_MAPPING = {
    "Cash And Cash Equivalents": "Cash & Cash Equivalents",
    "Accounts Receivable": "Accounts Receivable",
    "Inventory": "Inventory",
    "Prepaid Assets": "Prepaid Expenses",
    "Total Current Assets": "Total Current Assets",
    "Property Plant Equipment Net": "Property Plant & Equipment, Net",
    "Net PPE": "Property Plant & Equipment, Net",
    "Gross PPE": "Property Plant & Equipment, Gross",
    "Goodwill": "Goodwill",
    "Intangible Assets": "Intangible Assets",
    "Other Intangible Assets": "Intangible Assets",
    "Total Assets": "Total Assets",
    "Accounts Payable": "Accounts Payable",
    "Current Accrued Expenses": "Accrued Expenses",
    "Current Deferred Revenue": "Deferred Revenue",
    "Current Debt": "Current Portion of Long-Term Debt",
    "Total Current Liabilities": "Total Current Liabilities",
    "Long Term Debt": "Long-Term Debt",
    "Total Liabilities": "Total Liabilities",
    "Total Liabilities Net Minority Interest": "Total Liabilities",
    "Common Stock": "Common Stock",
    "Additional Paid In Capital": "Additional Paid-In Capital",
    "Retained Earnings": "Retained Earnings",
    "Total Stockholder Equity": "Total Equity",
    "Stockholders Equity": "Total Equity",
    "Non Current Deferred Taxes Liabilities": "Deferred Tax Liability",
}
CF_MAPPING = {
    "Operating Cash Flow": "Operating Cash Flow",
    "Capital Expenditure": "Capital Expenditures",
    "Free Cash Flow": "Free Cash Flow",
    "Net Income": "Net Income",
    "Depreciation Amortization": "Depreciation & Amortization",
    "Depreciation And Amortization": "Depreciation & Amortization",
    "Change In Working Capital": "Change in Working Capital",
    "Stock Based Compensation": "Stock-Based Compensation",
    "Investing Cash Flow": "Investing Cash Flow",
    "Financing Cash Flow": "Financing Cash Flow",
    "Net Change In Cash": "Net Change in Cash",
    "Changes In Cash": "Net Change in Cash",
    "Common Stock Dividend Paid": "Dividends Paid",
    "Cash Dividends Paid": "Dividends Paid",
}


class DataLoader:
    """Load and standardize financial data from yfinance."""

    def __init__(self, ticker: str):
        self.ticker = normalize_ticker(ticker)
        if yf is None:
            raise MissingDependencyError("yfinance", "The yfinance data provider")
        self.company = yf.Ticker(self.ticker)

    def get_income_statement(self, years: int = 5) -> pd.DataFrame:
        is_df = self.company.financials
        if is_df is None or is_df.empty:
            return pd.DataFrame()
        return self._standardize_is(is_df.iloc[:, :years])

    def get_balance_sheet(self, years: int = 5) -> pd.DataFrame:
        bs_df = self.company.balance_sheet
        if bs_df is None or bs_df.empty:
            return pd.DataFrame()
        return self._standardize_bs(bs_df.iloc[:, :years])

    def get_cash_flow(self, years: int = 5) -> pd.DataFrame:
        cf_df = self.company.cashflow
        if cf_df is None or cf_df.empty:
            return pd.DataFrame()
        return self._standardize_cf(cf_df.iloc[:, :years])

    def get_key_stats(self) -> dict[str, Any]:
        """Key valuation and operational metrics."""
        try:
            info = self.company.info or {}
        except Exception:  # pragma: no cover - network / provider errors
            info = {}
        keys = {
            "market_cap": "marketCap",
            "enterprise_value": "enterpriseValue",
            "shares_outstanding": "sharesOutstanding",
            "beta": "beta",
            "trailing_pe": "trailingPE",
            "forward_pe": "forwardPE",
            "price_to_sales": "priceToSalesTrailing12Months",
            "ev_revenue": "enterpriseToRevenue",
            "ev_ebitda": "enterpriseToEbitda",
            "profit_margins": "profitMargins",
            "operating_margins": "operatingMargins",
            "return_on_equity": "returnOnEquity",
            "return_on_assets": "returnOnAssets",
            "debt_to_equity": "debtToEquity",
            "current_ratio": "currentRatio",
            "quick_ratio": "quickRatio",
            "revenue_growth": "revenueGrowth",
            "earnings_growth": "earningsGrowth",
        }
        return {name: info.get(key) for name, key in keys.items()}

    @staticmethod
    def _rename(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
        renamed = df.rename(index=mapping)
        # Keep the first occurrence when several source lines map to one name
        return renamed[~renamed.index.duplicated(keep="first")]

    def _standardize_is(self, df: pd.DataFrame) -> pd.DataFrame:
        return self._rename(df, IS_MAPPING)

    def _standardize_bs(self, df: pd.DataFrame) -> pd.DataFrame:
        return self._rename(df, BS_MAPPING)

    def _standardize_cf(self, df: pd.DataFrame) -> pd.DataFrame:
        return self._rename(df, CF_MAPPING)


STATEMENT_MAPPINGS = {"income_statement": IS_MAPPING, "balance_sheet": BS_MAPPING, "cash_flow": CF_MAPPING}


def standardize_statement(df: pd.DataFrame, statement: str) -> pd.DataFrame:
    """Rename provider line names (e.g. 'Total Revenue') to the model's ('Revenue'); unknown lines are kept."""
    return DataLoader._rename(df, STATEMENT_MAPPINGS[statement])


def load_public_company_data(ticker: str, years: int = 5) -> dict[str, Any]:
    """Statements and key stats for ``ticker`` from Yahoo Finance.

    Shortcut for ``fetch_statements(ticker, years, provider="yfinance")``: the ticker is
    validated before any network call, fetches are rate limited / circuit-broken, and
    provider failures raise ``DataFetchError``. Use ``fetch_statements`` for other sources.
    """
    from financial_model.data.providers import fetch_statements

    return fetch_statements(ticker, years, provider="yfinance")


# ---------------------------------------------------------------------- #
# Driver derivation
# ---------------------------------------------------------------------- #
def _chronological(df: pd.DataFrame | None) -> pd.DataFrame:
    """Return a copy with columns ordered oldest -> newest (yfinance returns newest first)."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    out = df.copy()
    try:
        cols = pd.to_datetime(out.columns)
        out.columns = cols
        out = out.sort_index(axis=1)
    except (TypeError, ValueError):
        pass
    return out


def _latest(df: pd.DataFrame, line: str, default: float = 0.0) -> float:
    if df.empty or line not in df.index:
        return default
    series = pd.to_numeric(df.loc[line], errors="coerce").dropna()
    return float(series.iloc[-1]) if len(series) else default


def _series(df: pd.DataFrame, line: str) -> pd.Series:
    if df.empty or line not in df.index:
        return pd.Series(dtype=float)
    return pd.to_numeric(df.loc[line], errors="coerce").dropna().astype(float)


def _ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator == 0 or np.isnan(denominator) or np.isnan(numerator):
        return default
    return float(numerator / denominator)


def derive_drivers_from_financials(
    income_statement: pd.DataFrame | None,
    balance_sheet: pd.DataFrame | None,
    cash_flow: pd.DataFrame | None,
    key_stats: dict[str, Any] | None = None,
    projection_years: int = 5,
) -> dict[str, dict[str, Any]]:
    """
    Turn historical statements (standardised line names) into model drivers.

    Growth uses the historical CAGR (fading by 10% a year towards 3%), margins and
    working-capital days use the latest year, and balance sheet opening values come
    from the latest period. Missing lines fall back to model defaults.
    """
    is_df = _chronological(income_statement)
    bs_df = _chronological(balance_sheet)
    cf_df = _chronological(cash_flow)
    key_stats = key_stats or {}

    revenue_series = _series(is_df, "Revenue")
    revenue = float(revenue_series.iloc[-1]) if len(revenue_series) else 10_000_000.0
    if len(revenue_series) >= 2 and revenue_series.iloc[0] > 0:
        years = len(revenue_series) - 1
        cagr = (revenue_series.iloc[-1] / revenue_series.iloc[0]) ** (1 / years) - 1
    else:
        cagr = float(key_stats.get("revenue_growth") or 0.10)
    cagr = float(np.clip(cagr, -0.5, 1.0))
    growth = [round(0.03 + (cagr - 0.03) * (0.9**i), 4) for i in range(projection_years)]

    cogs = abs(_latest(is_df, "COGS"))
    if cogs == 0 and "Gross Profit" in is_df.index:
        cogs = max(0.0, revenue - _latest(is_df, "Gross Profit"))
    rd = abs(_latest(is_df, "R&D Expense"))
    sga = abs(_latest(is_df, "SG&A Expense"))
    pretax = _latest(is_df, "Pre-Tax Income")
    tax = abs(_latest(is_df, "Income Tax Expense"))
    tax_rate = float(np.clip(_ratio(tax, pretax, 0.21), 0.0, 0.5)) if pretax > 0 else 0.21
    dna = abs(_latest(is_df, "Depreciation & Amortization")) or abs(_latest(cf_df, "Depreciation & Amortization"))
    sbc = abs(_latest(cf_df, "Stock-Based Compensation"))
    interest = abs(_latest(is_df, "Interest Expense"))

    ar = _latest(bs_df, "Accounts Receivable")
    ap = _latest(bs_df, "Accounts Payable")
    inventory = _latest(bs_df, "Inventory")
    prepaid = _latest(bs_df, "Prepaid Expenses")
    accrued = _latest(bs_df, "Accrued Expenses")
    deferred = _latest(bs_df, "Deferred Revenue")
    ppe_gross = _latest(bs_df, "Property Plant & Equipment, Gross") or _latest(bs_df, "Property Plant & Equipment, Net")
    capex = abs(_latest(cf_df, "Capital Expenditures"))
    cash = _latest(bs_df, "Cash & Cash Equivalents")
    ltd = _latest(bs_df, "Long-Term Debt")
    current_debt = _latest(bs_df, "Current Portion of Long-Term Debt")
    dtl = _latest(bs_df, "Deferred Tax Liability")
    goodwill = _latest(bs_df, "Goodwill")
    intangibles = _latest(bs_df, "Intangible Assets")
    common = _latest(bs_df, "Common Stock")
    apic = _latest(bs_df, "Additional Paid-In Capital")
    dividends = abs(_latest(cf_df, "Dividends Paid"))
    shares = float(key_stats.get("shares_outstanding") or _latest(is_df, "Diluted Shares") or 1_000_000)

    def driver(value: Any, description: str, unit: str) -> dict[str, Any]:
        if isinstance(value, float):
            value = round(value, 6)
        return {"value": value, "description": description, "unit": unit}

    return {
        "revenue": driver(revenue, "Latest fiscal year revenue", "$"),
        "revenue_growth": driver(growth, f"Historical CAGR {cagr:.1%} fading towards 3%", "%"),
        "cogs_pct": driver(_ratio(cogs, revenue, 0.35), "COGS % of revenue (latest year)", "%"),
        "rd_pct": driver(_ratio(rd, revenue, 0.0), "R&D % of revenue", "%"),
        "sm_pct": driver(_ratio(sga, revenue, 0.0), "SG&A % of revenue (reported combined; mapped to S&M)", "%"),
        "gna_pct": driver(0.0, "G&A % of revenue (included in SG&A)", "%"),
        "sbc": driver(sbc, "Stock-based compensation (latest year)", "$"),
        "tax_rate": driver(tax_rate, "Effective tax rate", "%"),
        "interest_expense": driver(interest, "Interest expense (latest year)", "$"),
        "ar_days": driver(_ratio(ar * 365, revenue, 45.0) if ar else 45.0, "Receivable days", "days"),
        "inventory_days": driver(_ratio(inventory * 365, cogs, 0.0), "Inventory days", "days"),
        "prepaid_pct": driver(_ratio(prepaid, revenue, 0.0), "Prepaid % of revenue", "%"),
        "ap_days": driver(_ratio(ap * 365, cogs, 60.0) if ap else 60.0, "Payable days", "days"),
        "accrued_pct": driver(_ratio(accrued, revenue, 0.0), "Accrued % of revenue", "%"),
        "deferred_rev_pct": driver(_ratio(deferred, revenue, 0.0), "Deferred revenue % of revenue", "%"),
        "ppe_gross": driver(ppe_gross, "Opening gross PP&E", "$"),
        "capex": driver(-capex, "Annual capex (latest year)", "$"),
        "depreciation_rate": driver(
            _ratio(dna, ppe_gross + capex, 0.10) if ppe_gross else 0.10, "D&A / gross PP&E", "%"
        ),
        "intangibles": driver(intangibles, "Intangible assets", "$"),
        "goodwill": driver(goodwill, "Opening goodwill", "$"),
        "current_debt": driver(current_debt, "Current portion of debt", "$"),
        "long_term_debt": driver(ltd, "Long-term debt", "$"),
        "dlt": driver(dtl, "Deferred tax liability", "$"),
        "common_stock": driver(common, "Common stock", "$"),
        "apic": driver(apic, "Additional paid-in capital", "$"),
        "dividends": driver(dividends, "Dividends paid (latest year)", "$"),
        "beginning_cash": driver(cash, "Opening cash", "$"),
        "shares_outstanding": driver(shares, "Shares outstanding", "#"),
    }


def next_projection_year(income_statement: pd.DataFrame | None, default: int | None = None) -> int:
    """First projection year = the year after the latest historical period (else ``default`` / this year)."""
    hist = _chronological(income_statement)
    if not hist.empty:
        last = hist.columns[-1]
        year = getattr(last, "year", None)
        if year is None:
            from financial_model.data.providers import _period_key

            key = _period_key(last)
            year = key[0] if key else None
        if year is not None:
            return int(year) + 1
    return default if default is not None else pd.Timestamp.today().year


def config_from_statements(
    data: dict[str, Any],
    name: str,
    years: int = 5,
    metadata: dict[str, Any] | None = None,
    base_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Three-statement model config whose drivers are derived from historical statements.

    Works for any provider's output. ``base_config`` (e.g. an ``init`` template) supplies
    drivers the history cannot; derived drivers override it. Projections start the year
    after the last historical period, with +/-5pt growth scenarios around the derived path.
    """
    derived = derive_drivers_from_financials(
        data.get("income_statement"),
        data.get("balance_sheet"),
        data.get("cash_flow"),
        data.get("key_stats"),
        projection_years=years,
    )
    config: dict[str, Any] = dict(base_config or {})
    config.setdefault("assumptions", {})
    config["assumptions"].setdefault("drivers", {})
    config["name"] = name
    config["model_type"] = "three-statement"
    config["periods"] = {
        "start_year": next_projection_year(data.get("income_statement")),
        "years": years,
        "type": "annual",
    }
    config["assumptions"]["drivers"].update(derived)
    config["assumptions"]["metadata"] = dict(metadata or {})
    base_growth = derived.get("revenue_growth", {}).get("value")
    if isinstance(base_growth, list):
        config["assumptions"]["scenarios"] = {
            "base": {},
            "upside": {"revenue_growth": [round(g + 0.05, 4) for g in base_growth]},
            "downside": {"revenue_growth": [round(g - 0.05, 4) for g in base_growth]},
        }
    config.setdefault("statements", [])
    return config
