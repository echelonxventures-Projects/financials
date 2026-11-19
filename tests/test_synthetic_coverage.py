"""
Tests for synthetic data generator to achieve 100% coverage - targeting uncovered lines.
"""

from financial_model.data.synthetic import (
    generate_consumer_company,
    generate_manufacturing_company,
)


class TestSyntheticCoverage:
    """Test synthetic data generators for uncovered lines."""

    def test_generate_consumer_company(self):
        """Test generate_consumer_company (line 216)."""
        data = generate_consumer_company()
        assert "income_statement" in data
        assert "balance_sheet" in data
        assert "cash_flow" in data
        assert "periods" in data

    def test_generate_manufacturing_company(self):
        """Test generate_manufacturing_company (line 226)."""
        data = generate_manufacturing_company()
        assert "income_statement" in data
        assert "balance_sheet" in data
        assert "cash_flow" in data
        assert "periods" in data
