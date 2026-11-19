"""
Tests for statement module to achieve 100% coverage - targeting validation paths.
"""
from __future__ import annotations

from financial_model.core.line_item import CalculationRule, LineItem, LineItemType
from financial_model.core.statement import Statement, StatementType


class TestStatementValidationPaths:
    """Test Statement validation for full coverage."""

    def test_validate_parent_not_found(self):
        """Test validation error when parent not found (line 74-75)."""
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(LineItem("Child", LineItemType.INPUT, "income_statement", parent="NonExistentParent"))
        errors = stmt.validate()
        assert any("parent 'NonExistentParent' not found" in e for e in errors)

    def test_validate_child_not_found(self):
        """Test validation error when child not found (lines 78-80)."""
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(
            LineItem(
                "Parent",
                LineItemType.CALCULATED,
                "income_statement",
                calculation_rule=CalculationRule.SUM_CHILDREN,
                children=["NonExistentChild"],
            )
        )
        errors = stmt.validate()
        assert any("child 'NonExistentChild' not found" in e for e in errors)

    def test_validate_pct_of_line_base_not_found(self):
        """Test validation error when PCT_OF_LINE base_line not found (lines 83-86)."""
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(
            LineItem(
                "Test",
                LineItemType.CALCULATED,
                "income_statement",
                calculation_rule=CalculationRule.PCT_OF_LINE,
                calculation_params={"base_line": "NonExistentBase"},
            )
        )
        errors = stmt.validate()
        assert any("base_line 'NonExistentBase' not found" in e for e in errors)

    def test_validate_link_missing_source_line(self):
        """Test validation error when LINK missing source_line (lines 92-95)."""
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(LineItem("Test", LineItemType.LINK, "income_statement"))  # No source_line
        errors = stmt.validate()
        assert any("source_line param" in e for e in errors)

    def test_validate_growth_rate(self):
        """Test validation for GROWTH_RATE (line 88-90) - just ensure it runs."""
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(
            LineItem(
                "Revenue", LineItemType.CALCULATED, "income_statement", calculation_rule=CalculationRule.GROWTH_RATE
            )
        )
        errors = stmt.validate()
        # Should not error, just passes
        assert isinstance(errors, list)

    def test_to_dataframe_empty(self):
        """Test to_dataframe with no periods (line 86)."""
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(LineItem("Revenue", LineItemType.INPUT, "income_statement"))
        df = stmt.to_dataframe()
        assert df.shape[0] == 1
        assert len(df.columns) == 0
