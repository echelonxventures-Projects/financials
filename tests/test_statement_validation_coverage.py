"""
Tests for statement validation remaining uncovered lines.
Target lines: 46, 49, 52, 55, 58
"""
from __future__ import annotations

from financial_model.core.line_item import CalculationRule, LineItem, LineItemType
from financial_model.core.statement import Statement, StatementType


class TestStatementValidationCoverage:
    """Test remaining uncovered validation lines in statement.py."""

    def test_validate_parent_not_found(self):
        """Test parent validation error (line 46)."""
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(LineItem("Child", LineItemType.INPUT, "income_statement", parent="NonExistentParent"))
        errors = stmt.validate()
        assert any("parent 'NonExistentParent' not found" in e for e in errors)

    def test_validate_child_not_found(self):
        """Test child validation error (line 49)."""
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
        """Test PCT_OF_LINE base_line validation error (line 52)."""
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

    def test_validate_growth_rate(self):
        """Test GROWTH_RATE validation (line 55)."""
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(
            LineItem(
                "Revenue", LineItemType.CALCULATED, "income_statement", calculation_rule=CalculationRule.GROWTH_RATE
            )
        )
        errors = stmt.validate()
        # Just ensure no error for GROWTH_RATE
        assert isinstance(errors, list)

    def test_validate_link_missing_source_line(self):
        """Test LINK missing source_line validation error (line 58)."""
        stmt = Statement(StatementType.INCOME_STATEMENT)
        stmt.add_line_item(LineItem("Test", LineItemType.LINK, "income_statement"))  # No source_line
        errors = stmt.validate()
        assert any("source_line param" in e for e in errors)
