"""
Tests for ThreeStatementModel YAML loading edge cases.
Target lines: 399, 406, 413-422
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.models.three_statement import ThreeStatementModel


class TestThreeStatementYAMLLoadingCoverage:
    """Test ThreeStatementModel YAML loading edge cases."""

    def setup_method(self):
        self.periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])

    def test_from_yaml_with_string_assumptions_path(self):
        """Test from_yaml with string assumptions path (line 399)."""
        # Create a YAML with inline assumptions
        config = {
            "name": "Test Model",
            "periods": {"start_year": 2024, "years": 3, "type": "annual"},
            "assumptions": {
                "drivers": {
                    "revenue": {"value": 10000000, "description": "Revenue", "unit": "$"},
                    "revenue_growth": {"value": 0.2, "description": "Growth", "unit": "%"},
                },
                "scenarios": {"base": {}, "upside": {"revenue_growth": 0.3}},
            },
            "statements": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            with open(config_path, "w") as f:
                yaml.dump(config, f)

            model = ThreeStatementModel.from_yaml(str(config_path))
            assert model is not None
            assert model.name == "Test Model"

    def test_from_yaml_with_inline_assumptions_drivers_format(self):
        """Test from_yaml with inline assumptions drivers format (line 406)."""
        config = {
            "name": "Test Model",
            "periods": {"start_year": 2024, "years": 3, "type": "annual"},
            "assumptions": {
                "drivers": {
                    "revenue": {"value": 10000000, "description": "Revenue", "unit": "$"},
                },
                "scenarios": {},
            },
            "statements": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            with open(config_path, "w") as f:
                yaml.dump(config, f)

            model = ThreeStatementModel.from_yaml(str(config_path))
            assert model.assumptions.get("revenue") is not None

    def test_from_yaml_with_inline_assumptions_flat_format(self):
        """Test from_yaml with inline assumptions flat format (lines 413-422)."""
        # Test with driver config that has name key
        config = {
            "name": "Test Model",
            "periods": {"start_year": 2024, "years": 3, "type": "annual"},
            "assumptions": {
                "drivers": {
                    "revenue": {"name": "revenue", "value": 10000000, "description": "Revenue", "unit": "$"},
                },
                "scenarios": {},
            },
            "statements": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "model.yaml"
            with open(config_path, "w") as f:
                yaml.dump(config, f)

            model = ThreeStatementModel.from_yaml(str(config_path))
            assert model.assumptions.get("revenue") is not None
            assert model.assumptions.get("revenue").value == 10000000
