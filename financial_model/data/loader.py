"""
Data loader for public company financials via yfinance.
"""

import yfinance as yf
import pandas as pd
from typing import Any
from datetime import date


class DataLoader:
    """Load and standardize financial data from various sources."""

    def __init__(self, ticker: str):
        self.ticker = ticker.upper()
        self.company = yf.Ticker(self.ticker)

    def get_income_statement(self, years: int = 5) -> pd.DataFrame:
        """Get annual income statement."""
        is_df = self.company.financials
        if is_df.empty:
            return pd.DataFrame()
        # Keep last N years
        is_df = is_df.iloc[:, :years]
        return self._standardize_is(is_df)

    def get_balance_sheet(self, years: int = 5) -> pd.DataFrame:
        """Get annual balance sheet."""
        bs_df = self.company.balance_sheet
        if bs_df.empty:
            return pd.DataFrame()
        bs_df = bs_df.iloc[:, :years]
        return self._standardize_bs(bs_df)

    def get_cash_flow(self, years: int = 5) -> pd.DataFrame:
        """Get annual cash flow statement."""
        cf_df = self.company.cashflow
        if cf_df.empty:
            return pd.DataFrame()
        cf_df = cf_df.iloc[:, :years]
        return self._standardize_cf(cf_df)

    def get_key_stats(self) -> dict[str, Any]:
        """Get key valuation and operational metrics."""
        info = self.company.info
        return {
            "market_cap": info.get("marketCap"),
            "enterprise_value": info.get("enterpriseValue"),
            "shares_outstanding": info.get("sharesOutstanding"),
            "beta": info.get("beta"),
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "price_to_sales": info.get("priceToSalesTrailing12Months"),
            "ev_revenue": info.get("enterpriseToRevenue"),
            "ev_ebitda": info.get("enterpriseToEbitda"),
            "profit_margins": info.get("profitMargins"),
            "operating_margins": info.get("operatingMargins"),
            "return_on_equity": info.get("returnOnEquity"),
            "return_on_assets": info.get("returnOnAssets"),
            "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "quick_ratio": info.get("quickRatio"),
            "revenue_growth": info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
        }

    def _standardize_is(self, df: pd.DataFrame) -> pd.DataFrame:
        """Map yfinance line items to standard names."""
        mapping = {
            "Total Revenue": "Revenue",
            "Cost Of Revenue": "COGS",
            "Gross Profit": "Gross Profit",
            "Research Development": "R&D Expense",
            "Selling General Administrative": "SG&A Expense",
            "Operating Expense": "Operating Expenses",
            "Operating Income": "Operating Income",
            "Interest Expense": "Interest Expense",
            "Pretax Income": "Pre-Tax Income",
            "Tax Provision": "Income Tax Expense",
            "Net Income": "Net Income",
            "Diluted EPS": "Diluted EPS",
        }
        df = df.rename(index=mapping)
        return df

    def _standardize_bs(self, df: pd.DataFrame) -> pd.DataFrame:
        mapping = {
            "Cash And Cash Equivalents": "Cash & Cash Equivalents",
            "Accounts Receivable": "Accounts Receivable",
            "Inventory": "Inventory",
            "Total Current Assets": "Total Current Assets",
            "Property Plant Equipment Net": "Property Plant & Equipment, Net",
            "Goodwill": "Goodwill",
            "Intangible Assets": "Intangible Assets",
            "Total Assets": "Total Assets",
            "Accounts Payable": "Accounts Payable",
            "Total Current Liabilities": "Total Current Liabilities",
            "Long Term Debt": "Long-Term Debt",
            "Total Liabilities": "Total Liabilities",
            "Common Stock": "Common Stock",
            "Retained Earnings": "Retained Earnings",
            "Total Stockholder Equity": "Total Equity",
        }
        df = df.rename(index=mapping)
        return df

    def _standardize_cf(self, df: pd.DataFrame) -> pd.DataFrame:
        mapping = {
            "Operating Cash Flow": "Operating Cash Flow",
            "Capital Expenditure": "Capital Expenditures",
            "Free Cash Flow": "Free Cash Flow",
            "Net Income": "Net Income",
            "Depreciation Amortization": "Depreciation & Amortization",
            "Change In Working Capital": "Change in Working Capital",
            "Stock Based Compensation": "Stock-Based Compensation",
            "Investing Cash Flow": "Investing Cash Flow",
            "Financing Cash Flow": "Financing Cash Flow",
            "Net Change In Cash": "Net Change in Cash",
        }
        df = df.rename(index=mapping)
        return df


def load_public_company_data(ticker: str, years: int = 5) -> dict[str, pd.DataFrame]:
    """Convenience function to load all three statements."""
    loader = DataLoader(ticker)
    return {
        "income_statement": loader.get_income_statement(years),
        "balance_sheet": loader.get_balance_sheet(years),
        "cash_flow": loader.get_cash_flow(years),
        "key_stats": loader.get_key_stats(),
    }