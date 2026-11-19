"""
Tests for assumptions module - targeting set_scenario and scenario_names.
"""
from __future__ import annotations

from financial_model.core.assumptions import Assumptions


class TestAssumptionsRemaining:
    """Test remaining uncovered lines in assumptions."""

    def test_set_scenario(self):
        """Test set_scenario method (lines 72-73)."""
        assumptions = Assumptions()
        assumptions.set_scenario("upside", {"revenue": 20000000})
        assert "upside" in assumptions.scenarios
        assert assumptions.scenarios["upside"]["revenue"] == 20000000

    def test_scenario_names(self):
        """Test scenario_names method (line 76)."""
        assumptions = Assumptions(scenarios={"base": {}, "upside": {"revenue": 20000000}})
        names = assumptions.scenario_names()
        assert "base" in names
        assert "upside" in names
        assert len(names) == 2
