"""
Tests for assumptions module to achieve 100% coverage - targeting uncovered lines.
"""

import tempfile

import yaml

from financial_model.core.assumptions import Assumptions, Driver


class TestAssumptionsUncoveredLines:
    """Test Assumptions edge cases for uncovered lines."""

    def test_driver_initialization_from_dict(self):
        """Test __post_init__ converts dict to Driver (lines 40-42)."""
        # Pass a dict instead of Driver object
        assumptions = Assumptions(drivers={"test": {"name": "test", "value": 100, "description": "Test", "unit": "$"}})
        assert isinstance(assumptions.drivers["test"], Driver)
        assert assumptions.drivers["test"].get_value(0) == 100

    def test_driver_initialization_from_driver_object(self):
        """Test __post_init__ keeps Driver object as-is (line 42)."""
        driver = Driver("test", 100, "Test", "$")
        assumptions = Assumptions(drivers={"test": driver})
        assert assumptions.drivers["test"] is driver

    def test_copy_method(self):
        """Test copy method creates independent copy (lines 95-100)."""
        assumptions = Assumptions(
            drivers={"test": Driver("test", [10, 20, 30], "Test", "$")}, scenarios={"upside": {"test": 50}}
        )
        copied = assumptions.copy()
        assert "test" in copied.drivers
        assert copied.drivers["test"].get_value(0) == 10
        # Verify independence
        copied.drivers["test"].value = 999
        assert assumptions.drivers["test"].get_value(0) == 10

    def test_from_yaml_with_driver_config(self):
        """Test from_yaml with DriverConfig objects (line 83-84)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_file = f"{tmpdir}/assumptions.yaml"
            data = {
                "drivers": {
                    "revenue": {"name": "revenue", "value": [10000000], "description": "Revenue", "unit": "$"},
                    "growth": {"name": "growth", "value": [0.2], "description": "Growth", "unit": "%"},
                },
                "scenarios": {"upside": {"revenue": 20000000}},
            }
            with open(yaml_file, "w") as f:
                yaml.dump(data, f)
            assumptions = Assumptions.from_yaml(yaml_file)
            assert "revenue" in assumptions.drivers
            assert "growth" in assumptions.drivers
            assert "upside" in assumptions.scenarios
            assert assumptions.get_value("revenue", 0, "upside") == 20000000
