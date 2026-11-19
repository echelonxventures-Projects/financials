"""
Tests for data loader module.
"""

from unittest.mock import MagicMock, patch

import pandas as pd

from financial_model.data.loader import DataLoader, load_public_company_data


class TestDataLoader:
    def setup_method(self):
        self.mock_ticker = MagicMock()
        self.mock_company = MagicMock()

    @patch("financial_model.data.loader.yf.Ticker")
    def test_init(self, mock_yf_ticker):
        mock_yf_ticker.return_value = self.mock_company
        loader = DataLoader("AAPL")
        assert loader.ticker == "AAPL"
        mock_yf_ticker.assert_called_once_with("AAPL")

    @patch("financial_model.data.loader.yf.Ticker")
    def test_get_income_statement_empty(self, mock_yf_ticker):
        mock_company = MagicMock()
        mock_company.financials = pd.DataFrame()
        mock_yf_ticker.return_value = mock_company

        loader = DataLoader("AAPL")
        result = loader.get_income_statement(5)
        assert result.empty

    @patch("financial_model.data.loader.yf.Ticker")
    def test_get_income_statement_with_data(self, mock_yf_ticker):
        mock_company = MagicMock()
        # Create sample financials data
        data = pd.DataFrame(
            {
                pd.Timestamp("2023-12-31"): {
                    "Total Revenue": 100000000,
                    "Cost Of Revenue": 40000000,
                    "Gross Profit": 60000000,
                },
                pd.Timestamp("2022-12-31"): {
                    "Total Revenue": 90000000,
                    "Cost Of Revenue": 36000000,
                    "Gross Profit": 54000000,
                },
            }
        )
        mock_company.financials = data
        mock_yf_ticker.return_value = mock_company

        loader = DataLoader("AAPL")
        result = loader.get_income_statement(2)
        assert not result.empty
        assert "Revenue" in result.index
        assert "COGS" in result.index
        assert "Gross Profit" in result.index

    @patch("financial_model.data.loader.yf.Ticker")
    def test_get_balance_sheet_empty(self, mock_yf_ticker):
        mock_company = MagicMock()
        mock_company.balance_sheet = pd.DataFrame()
        mock_yf_ticker.return_value = mock_company

        loader = DataLoader("AAPL")
        result = loader.get_balance_sheet(5)
        assert result.empty

    @patch("financial_model.data.loader.yf.Ticker")
    def test_get_balance_sheet_with_data(self, mock_yf_ticker):
        mock_company = MagicMock()
        data = pd.DataFrame(
            {
                pd.Timestamp("2023-12-31"): {
                    "Cash And Cash Equivalents": 50000000,
                    "Total Assets": 200000000,
                    "Total Liabilities": 80000000,
                },
                pd.Timestamp("2022-12-31"): {
                    "Cash And Cash Equivalents": 40000000,
                    "Total Assets": 180000000,
                    "Total Liabilities": 70000000,
                },
            }
        )
        mock_company.balance_sheet = data
        mock_yf_ticker.return_value = mock_company

        loader = DataLoader("AAPL")
        result = loader.get_balance_sheet(2)
        assert not result.empty
        assert "Cash & Cash Equivalents" in result.index
        assert "Total Assets" in result.index
        assert "Total Liabilities" in result.index

    @patch("financial_model.data.loader.yf.Ticker")
    def test_get_cash_flow_empty(self, mock_yf_ticker):
        mock_company = MagicMock()
        mock_company.cashflow = pd.DataFrame()
        mock_yf_ticker.return_value = mock_company

        loader = DataLoader("AAPL")
        result = loader.get_cash_flow(5)
        assert result.empty

    @patch("financial_model.data.loader.yf.Ticker")
    def test_get_cash_flow_with_data(self, mock_yf_ticker):
        mock_company = MagicMock()
        data = pd.DataFrame(
            {
                pd.Timestamp("2023-12-31"): {
                    "Operating Cash Flow": 30000000,
                    "Capital Expenditure": -10000000,
                    "Free Cash Flow": 20000000,
                },
                pd.Timestamp("2022-12-31"): {
                    "Operating Cash Flow": 25000000,
                    "Capital Expenditure": -8000000,
                    "Free Cash Flow": 17000000,
                },
            }
        )
        mock_company.cashflow = data
        mock_yf_ticker.return_value = mock_company

        loader = DataLoader("AAPL")
        result = loader.get_cash_flow(2)
        assert not result.empty
        assert "Operating Cash Flow" in result.index
        assert "Capital Expenditures" in result.index
        assert "Free Cash Flow" in result.index

    @patch("financial_model.data.loader.yf.Ticker")
    def test_get_key_stats(self, mock_yf_ticker):
        mock_company = MagicMock()
        mock_company.info = {
            "marketCap": 3000000000000,
            "enterpriseValue": 3100000000000,
            "sharesOutstanding": 15000000000,
            "beta": 1.2,
            "trailingPE": 25.5,
            "forwardPE": 22.0,
            "priceToSalesTrailing12Months": 7.5,
            "enterpriseToRevenue": 7.8,
            "enterpriseToEbitda": 18.0,
            "profitMargins": 0.25,
            "operatingMargins": 0.30,
            "returnOnEquity": 0.55,
            "returnOnAssets": 0.20,
            "debtToEquity": 0.30,
            "currentRatio": 1.5,
            "quickRatio": 1.2,
            "revenueGrowth": 0.10,
            "earningsGrowth": 0.15,
        }
        mock_yf_ticker.return_value = mock_company

        loader = DataLoader("AAPL")
        stats = loader.get_key_stats()

        assert stats["market_cap"] == 3000000000000
        assert stats["beta"] == 1.2
        assert stats["trailing_pe"] == 25.5
        assert stats["profit_margins"] == 0.25

    @patch("financial_model.data.loader.yf.Ticker")
    def test_get_key_stats_missing_keys(self, mock_yf_ticker):
        mock_company = MagicMock()
        mock_company.info = {}  # Empty info
        mock_yf_ticker.return_value = mock_company

        loader = DataLoader("AAPL")
        stats = loader.get_key_stats()

        # All values should be None
        assert all(v is None for v in stats.values())

    @patch("financial_model.data.loader.yf.Ticker")
    def test_standardize_is(self, mock_yf_ticker):
        mock_yf_ticker.return_value = MagicMock()
        loader = DataLoader("AAPL")

        df = pd.DataFrame(
            {
                "2023": {"Total Revenue": 100, "Cost Of Revenue": 40, "Gross Profit": 60, "Research Development": 10},
            }
        )
        result = loader._standardize_is(df)

        assert "Revenue" in result.index
        assert "COGS" in result.index
        assert "Gross Profit" in result.index
        assert "R&D Expense" in result.index

    @patch("financial_model.data.loader.yf.Ticker")
    def test_standardize_bs(self, mock_yf_ticker):
        mock_yf_ticker.return_value = MagicMock()
        loader = DataLoader("AAPL")

        df = pd.DataFrame(
            {
                "2023": {
                    "Cash And Cash Equivalents": 50,
                    "Total Assets": 200,
                    "Total Liabilities": 80,
                    "Total Stockholder Equity": 120,
                },
            }
        )
        result = loader._standardize_bs(df)

        assert "Cash & Cash Equivalents" in result.index
        assert "Total Assets" in result.index
        assert "Total Liabilities" in result.index
        assert "Total Equity" in result.index

    @patch("financial_model.data.loader.yf.Ticker")
    def test_standardize_cf(self, mock_yf_ticker):
        mock_yf_ticker.return_value = MagicMock()
        loader = DataLoader("AAPL")

        df = pd.DataFrame(
            {
                "2023": {"Operating Cash Flow": 30, "Capital Expenditure": -10, "Depreciation Amortization": 5},
            }
        )
        result = loader._standardize_cf(df)

        assert "Operating Cash Flow" in result.index
        assert "Capital Expenditures" in result.index
        assert "Depreciation & Amortization" in result.index


class TestLoadPublicCompanyData:
    @patch("financial_model.data.loader.DataLoader")
    def test_load_public_company_data(self, mock_loader_class):
        mock_loader = MagicMock()
        mock_loader.get_income_statement.return_value = pd.DataFrame({"Revenue": [100]})
        mock_loader.get_balance_sheet.return_value = pd.DataFrame({"Cash": [50]})
        mock_loader.get_cash_flow.return_value = pd.DataFrame({"Operating Cash Flow": [30]})
        mock_loader.get_key_stats.return_value = {"market_cap": 1000}
        mock_loader_class.return_value = mock_loader

        result = load_public_company_data("AAPL", 3)

        assert "income_statement" in result
        assert "balance_sheet" in result
        assert "cash_flow" in result
        assert "key_stats" in result
        mock_loader.get_income_statement.assert_called_once_with(3)
        mock_loader.get_balance_sheet.assert_called_once_with(3)
        mock_loader.get_cash_flow.assert_called_once_with(3)
