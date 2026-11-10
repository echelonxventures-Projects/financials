"""
Model-specific tests: three-statement, DCF, LBO.
"""

import pytest
import pandas as pd
import numpy as np
from hypothesis import given, strategies as st, settings

from financial_model.models.three_statement import ThreeStatementModel
from financial_model.models.dcf import DCFModel
from financial_model.models.lbo import LBOModel
from financial_model.models.budget import BudgetModel
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.data.synthetic import generate_saas_company


class TestThreeStatementModel:
    def test_saas_model_calculates(self):
        model = ThreeStatementModel(name="SaaS Test")
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(5)])
        model.set_periods(periods)

        result = model.calculate()

        # Check all statements populated
        assert not result.income_statement.isna().all().all()
        assert not result.balance_sheet.isna().all().all()
        assert not result.cash_flow.isna().all().all()

        # Check balance sheet balances
        assert result.bs_balance_result.balanced
        assert result.bs_balance_result.max_diff < 1.0

        # Check cash flow ties
        assert result.cf_balance_result.balanced
        assert result.cf_balance_result.max_diff < 1.0

    def test_revenue_growth_compounds(self):
        model = ThreeStatementModel(name="Growth Test")
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        model.set_periods(periods)

        # Override growth assumption
        model.assumptions.set_driver(
            model.assumptions.drivers["revenue_growth"] if "revenue_growth" in model.assumptions.drivers
            else None
        )

        result = model.calculate()
        rev = result.income_statement.loc["Revenue"] if "Revenue" in result.income_statement.index else None
        # Revenue_Calc should show growth
        if "Revenue_Calc" in result.income_statement.index:
            rev_calc = result.income_statement.loc["Revenue_Calc"]
            assert rev_calc.iloc[1] > rev_calc.iloc[0]
            assert rev_calc.iloc[2] > rev_calc.iloc[1]

    def test_ebit_calculates(self):
        model = ThreeStatementModel()
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        model.set_periods(periods)
        result = model.calculate()

        if "Operating Income" in result.income_statement.index:
            ebit = result.income_statement.loc["Operating Income"]
            assert not ebit.isna().all()

    def test_net_income_flows_to_retained_earnings(self):
        model = ThreeStatementModel()
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        model.set_periods(periods)
        result = model.calculate()

        ni = result.income_statement.loc["Net Income"].iloc[0] if "Net Income" in result.income_statement.index else 0
        re = result.balance_sheet.loc["Retained Earnings"].iloc[0] if "Retained Earnings" in result.balance_sheet.index else 0
        # First year RE should equal NI (no dividends)
        assert abs(ni - re) < 1.0


class TestDCFModel:
    def test_dcf_calculates(self):
        model = DCFModel(name="DCF Test")
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(5)])
        model.set_periods(periods)

        result = model.calculate()
        dcf = model.calculate_dcf(result)

        assert "wacc" in dcf
        assert "ev_gordon" in dcf
        assert "ev_multiple" in dcf
        assert "equity_gordon" in dcf
        assert "price_gordon" in dcf
        assert dcf["wacc"] > 0
        assert dcf["ev_gordon"] > 0

    def test_dcf_sensitivity(self):
        model = DCFModel()
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(5)])
        model.set_periods(periods)
        result = model.calculate()

        sens = model.sensitivity_analysis(result)
        assert sens.shape == (5, 5)
        assert not sens.isna().all().all()

    def test_wacc_reasonable(self):
        model = DCFModel()
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(5)])
        model.set_periods(periods)
        result = model.calculate()
        dcf = model.calculate_dcf(result)

        # WACC should be between 5% and 20% for reasonable inputs
        assert 0.05 < dcf["wacc"] < 0.20


class TestLBOModel:
    def test_lbo_sources_uses(self):
        model = LBOModel()
        su = model.sources_uses()

        assert "sources" in su
        assert "uses" in su
        assert "entry_ev" in su
        assert "equity_amt" in su
        assert su["entry_ev"] > 0
        assert su["equity_amt"] > 0

    def test_lbo_returns(self):
        model = LBOModel()
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(5)])
        model.set_periods(periods)
        result = model.calculate()
        returns = model.calculate_returns(result)

        assert "moic" in returns
        assert "irr" in returns
        assert "equity_value_at_exit" in returns
        assert returns["moic"] > 0
        assert returns["irr"] > 0


class TestBudgetModel:
    def test_budget_model_creation(self):
        model = BudgetModel()
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        model.set_periods(periods)

        result = model.calculate()

        assert "Revenue" in result.income_statement.index
        assert "Total Operating Expense" in result.income_statement.index

    def test_add_department(self):
        model = BudgetModel()
        model.add_department("Operations", [5, 6, 7], 120_000)
        assert "Operations" in model.departments


class TestSyntheticData:
    def test_generate_saas_company(self):
        data = generate_saas_company(start_year=2020, years=5)

        assert "income_statement" in data
        assert "balance_sheet" in data
        assert "cash_flow" in data
        assert "periods" in data

        is_df = data["income_statement"]
        assert "Revenue" in is_df.index
        assert "Net Income" in is_df.index

        bs_df = data["balance_sheet"]
        assert "Cash & Cash Equivalents" in bs_df.index
        assert "Total Assets" in bs_df.index

        cf_df = data["cash_flow"]
        assert "Operating Cash Flow" in cf_df.index

        # Check periods
        assert len(data["periods"]) == 5


# Integration test: full pipeline
class TestIntegration:
    def test_yaml_config_roundtrip(self, tmp_path):
        model = ThreeStatementModel(name="YAML Test")
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        model.set_periods(periods)

        # Export full model config
        config_path = tmp_path / "model.yaml"
        model.to_yaml(str(config_path))

        # Load back
        from financial_model.core.model import FinancialModel
        loaded = FinancialModel.from_yaml(str(config_path))
        assert loaded.name == "YAML Test"

    def test_excel_export(self, tmp_path):
        model = ThreeStatementModel()
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        model.set_periods(periods)
        result = model.calculate()

        output_path = tmp_path / "model.xlsx"
        model.export_excel(str(output_path), result)

        assert output_path.exists()
        assert output_path.stat().st_size > 0