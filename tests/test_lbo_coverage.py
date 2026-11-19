"""
Tests for LBO model to achieve 100% coverage - targeting uncovered lines.
"""
from __future__ import annotations

from financial_model.core.assumptions import Assumptions, Driver
from financial_model.core.line_item import CalculationRule, LineItem, LineItemType
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.models.lbo import LBOModel


class TestLBOCoverage:
    """Test LBOModel for uncovered lines."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(5)])
        self.assumptions = Assumptions()

    def test_lbo_init_adds_assumptions_and_line_items(self):
        """Test _add_lbo_assumptions and _add_lbo_line_items called during init (lines 51-71, 97-217)."""
        model = LBOModel(name="Test LBO", periods=self.periods)
        # Check LBO-specific drivers were added
        assert "entry_multiple" in model.assumptions.drivers
        assert "entry_ebitda" in model.assumptions.drivers
        assert "equity_contribution" in model.assumptions.drivers
        assert "tla_pct" in model.assumptions.drivers
        assert "tla_rate" in model.assumptions.drivers
        assert "tla_amort" in model.assumptions.drivers
        assert "tlb_pct" in model.assumptions.drivers
        assert "tlb_rate" in model.assumptions.drivers
        assert "tlb_amort" in model.assumptions.drivers
        assert "revolver_pct" in model.assumptions.drivers
        assert "revolver_rate" in model.assumptions.drivers
        assert "sub_pct" in model.assumptions.drivers
        assert "sub_rate" in model.assumptions.drivers
        assert "mezz_pct" in model.assumptions.drivers
        assert "mezz_rate" in model.assumptions.drivers
        assert "sweep_pct" in model.assumptions.drivers
        assert "exit_multiple" in model.assumptions.drivers
        assert "hold_years" in model.assumptions.drivers

        # Check debt tranches were added to balance sheet
        assert "Term Loan A" in model.balance_sheet.line_items
        assert "Term Loan B" in model.balance_sheet.line_items
        assert "Subordinated Debt" in model.balance_sheet.line_items
        assert "Mezzanine Debt" in model.balance_sheet.line_items

        # Check interest expense breakdown was added to income statement
        assert "TLA Interest" in model.income_statement.line_items
        assert "TLB Interest" in model.income_statement.line_items
        assert "Revolver Interest" in model.income_statement.line_items
        assert "Sub Interest" in model.income_statement.line_items
        assert "Mezz Interest" in model.income_statement.line_items

    def test_sources_uses(self):
        """Test sources_uses method (lines 219-246)."""
        model = LBOModel(name="Test LBO", periods=self.periods)
        su = model.sources_uses()
        assert "sources" in su
        assert "uses" in su
        assert "entry_ev" in su
        assert "equity_amt" in su
        assert len(su["sources"]) == 6  # 5 debt + equity
        assert len(su["uses"]) == 3

    def test_calculate_returns_with_result(self):
        """Test calculate_returns with passed result (lines 248-297)."""
        model = LBOModel(name="Test LBO", periods=self.periods)
        # Add minimal line items for calculation
        model.add_income_statement_item(
            LineItem(
                "Revenue", LineItemType.INPUT, "income_statement", driver=Driver("revenue", [10000000] * 5, "Rev", "$")
            )
        )
        model.add_income_statement_item(
            LineItem(
                "Operating Income",
                LineItemType.CALCULATED,
                "income_statement",
                calculation_rule=CalculationRule.PCT_OF_REVENUE,
                calculation_params={"driver": "op_margin"},
            )
        )
        model.assumptions.set_driver(Driver("op_margin", [0.2] * 5, "Op Margin", "%"))
        model.add_income_statement_item(
            LineItem(
                "Depreciation & Amortization",
                LineItemType.INPUT,
                "income_statement",
                driver=Driver("dna", [500000] * 5, "D&A", "$"),
            )
        )
        model.add_balance_sheet_item(
            LineItem(
                "Cash & Cash Equivalents",
                LineItemType.INPUT,
                "balance_sheet",
                driver=Driver("cash", [1000000] * 5, "Cash", "$"),
            )
        )

        result = model.calculate()
        lbo_result = model.calculate_returns(result)
        assert "entry_ev" in lbo_result
        assert "equity_invested" in lbo_result
        assert "exit_ebitda" in lbo_result
        assert "exit_ev" in lbo_result
        assert "net_debt_at_exit" in lbo_result
        assert "equity_value_at_exit" in lbo_result
        assert "moic" in lbo_result
        assert "irr" in lbo_result
        assert "hold_years" in lbo_result
