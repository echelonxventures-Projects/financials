"""
Tests for ThreeStatementModel coverage - YAML loading and from_yaml edge cases.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from financial_model.models.three_statement import ThreeStatementModel


class TestThreeStatementYAMLLoading:
    """Test ThreeStatementModel.from_yaml edge cases."""

    def test_from_yaml_with_string_assumptions_path(self):
        """Test loading from YAML where assumptions is a path to another YAML file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create assumptions file
            assumptions_file = tmpdir / "assumptions.yaml"
            assumptions_data = {
                "drivers": {
                    "revenue": {"name": "revenue", "value": [10000000], "description": "Revenue", "unit": "$"},
                    "revenue_growth": {"name": "revenue_growth", "value": [0.2], "description": "Growth", "unit": "%"},
                },
                "scenarios": {"base": {}, "upside": {"revenue_growth": 0.3}},
            }
            with open(assumptions_file, "w") as f:
                yaml.dump(assumptions_data, f)

            # Create main config referencing assumptions file
            config_file = tmpdir / "model.yaml"
            config = {
                "name": "Test Model",
                "periods": {"start_year": 2024, "years": 3, "type": "annual"},
                "assumptions": str(assumptions_file),  # String path
                "statements": [],
            }
            with open(config_file, "w") as f:
                yaml.dump(config, f)

            model = ThreeStatementModel.from_yaml(str(config_file))
            assert model.name == "Test Model"
            assert "revenue" in model.assumptions.drivers

    def test_from_yaml_with_inline_assumptions_drivers_format(self):
        """Test loading from YAML with inline assumptions in drivers format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "model.yaml"
            config = {
                "name": "Test Model",
                "periods": {"start_year": 2024, "years": 3, "type": "annual"},
                "assumptions": {
                    "drivers": {
                        "revenue": {"value": [10000000], "description": "Revenue", "unit": "$"},
                        "cogs_pct": {"value": [0.35], "description": "COGS %", "unit": "%"},
                    },
                    "scenarios": {"base": {}, "downside": {"revenue_growth": 0.1}},
                },
                "statements": [],
            }
            with open(config_file, "w") as f:
                yaml.dump(config, f)

            model = ThreeStatementModel.from_yaml(str(config_file))
            assert "revenue" in model.assumptions.drivers
            assert "cogs_pct" in model.assumptions.drivers
            assert "base" in model.assumptions.scenarios
            assert "downside" in model.assumptions.scenarios

    def test_from_yaml_with_inline_assumptions_flat_format(self):
        """Test loading from YAML with inline assumptions in flat format (no drivers key)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "model.yaml"
            config = {
                "name": "Test Model",
                "periods": {"start_year": 2024, "years": 3, "type": "annual"},
                "assumptions": {
                    "revenue": {"value": [10000000], "description": "Revenue", "unit": "$"},
                    "tax_rate": {"value": [0.21], "description": "Tax rate", "unit": "%"},
                },
                "statements": [],
            }
            with open(config_file, "w") as f:
                yaml.dump(config, f)

            model = ThreeStatementModel.from_yaml(str(config_file))
            assert "revenue" in model.assumptions.drivers
            assert "tax_rate" in model.assumptions.drivers

    def test_from_yaml_with_line_items_override(self):
        """Test loading from YAML with custom line items."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "model.yaml"
            config = {
                "name": "Test Model",
                "periods": {"start_year": 2024, "years": 3, "type": "annual"},
                "assumptions": {"drivers": {"revenue": {"value": [10000000], "description": "Revenue", "unit": "$"}}},
                "statements": [
                    {
                        "type": "income_statement",
                        "line_items": [
                            {
                                "name": "Custom Revenue",
                                "line_type": "input",
                                "statement_type": "income_statement",
                                "driver": {"value": [5000000], "description": "Custom", "unit": "$"},
                            }
                        ],
                    }
                ],
            }
            with open(config_file, "w") as f:
                yaml.dump(config, f)

            model = ThreeStatementModel.from_yaml(str(config_file))
            assert "Custom Revenue" in model.income_statement.line_items


class TestThreeStatementDefaultStructure:
    """Test default structure setup."""

    def test_setup_default_assumptions(self):
        """Test that all default drivers are set."""
        model = ThreeStatementModel()
        # Check key drivers exist
        assert "revenue" in model.assumptions.drivers
        assert "revenue_growth" in model.assumptions.drivers
        assert "cogs_pct" in model.assumptions.drivers
        assert "tax_rate" in model.assumptions.drivers
        assert "ar_days" in model.assumptions.drivers
        assert "ap_days" in model.assumptions.drivers
        assert "ppe_gross" in model.assumptions.drivers
        assert "capex" in model.assumptions.drivers
        assert "common_stock" in model.assumptions.drivers
        assert "apic" in model.assumptions.drivers
        assert "beginning_cash" in model.assumptions.drivers
