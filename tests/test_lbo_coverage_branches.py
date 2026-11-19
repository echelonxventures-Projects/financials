"""
Tests for LBO model remaining uncovered lines.
Target lines: 114, 125-129, 139, 143-146, 251
"""

from financial_model.core.assumptions import Assumptions, Driver
from financial_model.core.line_item import CalculationRule, LineItem, LineItemType
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.models.lbo import LBOModel


class TestLBOCoverageBranches:
    """Test LBO remaining uncovered lines."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(5)])
        self.assumptions = Assumptions()

    def test_lbo_debt_tranche_initialization(self):
        """Test LBO debt tranche initialization logic (lines 114, 125-129)."""
        model = LBOModel(name="Test LBO", periods=self.periods)
        # Check that debt tranches were initialized with correct initial values
        assert "Term Loan A" in model.balance_sheet.line_items
        assert "Term Loan B" in model.balance_sheet.line_items
        assert "Subordinated Debt" in model.balance_sheet.line_items
        assert "Mezzanine Debt" in model.balance_sheet.line_items

        # Verify they have custom formulas (line 125-129 area)
        tla = model.balance_sheet.line_items["Term Loan A"]
        assert tla.custom_formula is not None
        assert tla.calculation_rule == CalculationRule.CUSTOM

        tlb = model.balance_sheet.line_items["Term Loan B"]
        assert tlb.custom_formula is not None

        sub = model.balance_sheet.line_items["Subordinated Debt"]
        assert sub.custom_formula is not None

        mezz = model.balance_sheet.line_items["Mezzanine Debt"]
        assert mezz.custom_formula is not None

    def test_lbo_interest_expense_custom_formulas(self):
        """Test LBO interest expense custom formulas (lines 139, 143-146)."""
        model = LBOModel(name="Test LBO", periods=self.periods)
        # Check interest expense items exist with custom formulas
        assert "TLA Interest" in model.income_statement.line_items
        assert "TLB Interest" in model.income_statement.line_items
        assert "Revolver Interest" in model.income_statement.line_items
        assert "Sub Interest" in model.income_statement.line_items
        assert "Mezz Interest" in model.income_statement.line_items

        # Verify they have custom_formula (line 139, 143-146)
        tla_int = model.income_statement.line_items["TLA Interest"]
        assert tla_int.custom_formula is not None
        tlb_int = model.income_statement.line_items["TLB Interest"]
        assert tlb_int.custom_formula is not None
        rev_int = model.income_statement.line_items["Revolver Interest"]
        assert rev_int.custom_formula is not None
        sub_int = model.income_statement.line_items["Sub Interest"]
        assert sub_int.custom_formula is not None
        mezz_int = model.income_statement.line_items["Mezz Interest"]
        assert mezz_int.custom_formula is not None

    def test_lbo_calculate_returns_hold_years(self):
        """Test calculate_returns hold_years (line 251)."""
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
        assert "hold_years" in lbo_result
        assert lbo_result["hold_years"] == 5
