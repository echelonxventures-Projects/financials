"""
Tests for CLI to achieve 100% coverage - targeting uncovered lines.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from financial_model.cli.main import (
    _data_to_config,
    _export_csv,
    _export_json,
    _generate_config,
    _load_model,
    _print_summary,
    _synthetic_to_config,
)
from financial_model.core.balancer import BalanceResult
from financial_model.core.model import ModelResult
from financial_model.core.period import Period, PeriodIndex, PeriodType


class TestCLICoverage:
    """Test CLI helper functions for uncovered lines."""

    def test_generate_config_three_statement(self):
        """Test _generate_config for three-statement (line 172-205)."""
        config = _generate_config("three-statement", "saas", 5, 2024)
        assert config["name"] == "Three Statement Model"
        assert "assumptions" in config
        assert "drivers" in config["assumptions"]

    def test_generate_config_lbo(self):
        """Test _generate_config for LBO (lines 207-228)."""
        config = _generate_config("lbo", "saas", 5, 2024)
        assert config["name"] == "Lbo Model"
        assert "entry_multiple" in config["assumptions"]["drivers"]
        assert "tla_pct" in config["assumptions"]["drivers"]
        assert "tlb_pct" in config["assumptions"]["drivers"]

    def test_generate_config_other_industry(self):
        """Test _generate_config for non-saas industry (lines 196-205)."""
        config = _generate_config("three-statement", "manufacturing", 5, 2024)
        assert config["name"] == "Three Statement Model"
        assert "revenue" in config["assumptions"]["drivers"]

    def test_load_model(self):
        """Test _load_model (line 245-247)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            import yaml

            config = _generate_config("three-statement", "saas", 3, 2024)
            yaml.dump(config, f)
            f.flush()
            model = _load_model(Path(f.name))
            assert model is not None

    def test_data_to_config(self):
        """Test _data_to_config (lines 250-253)."""
        data = {"income_statement": pd.DataFrame(), "balance_sheet": pd.DataFrame(), "cash_flow": pd.DataFrame()}
        config = _data_to_config("TEST", data, 5)
        assert config["name"] == "TEST Three Statement Model"

    def test_synthetic_to_config(self):
        """Test _synthetic_to_config (lines 256-258)."""
        data = {
            "income_statement": pd.DataFrame(),
            "balance_sheet": pd.DataFrame(),
            "cash_flow": pd.DataFrame(),
            "periods": PeriodIndex([Period(year=2024, period_type=PeriodType.ANNUAL)]),
        }
        config = _synthetic_to_config(data, "saas", 3, 2024)
        assert config["name"] == "Three Statement Model"

    def test_print_summary(self):
        """Test _print_summary (lines 261-275)."""
        periods = PeriodIndex([Period(year=2024, period_type=PeriodType.ANNUAL)])
        is_df = pd.DataFrame({"Revenue": [10000000]}, index=pd.DatetimeIndex(["2024-12-31"]))
        bs_df = pd.DataFrame({"Cash": [1000000]}, index=pd.DatetimeIndex(["2024-12-31"]))
        cf_df = pd.DataFrame({"Net Income": [1000000]}, index=pd.DatetimeIndex(["2024-12-31"]))

        result = ModelResult(
            income_statement=is_df,
            balance_sheet=bs_df,
            cash_flow=cf_df,
            assumptions=None,
            periods=periods,
            bs_balance_result=BalanceResult(balanced=True, iterations=1, max_diff=0, plug_values={}, errors=[]),
            cf_balance_result=BalanceResult(balanced=True, iterations=1, max_diff=0, plug_values={}, errors=[]),
            metadata={"model_name": "Test"},
        )
        # Should not raise
        _print_summary(result)

    def test_export_csv(self):
        """Test _export_csv (lines 278-282)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            periods = PeriodIndex([Period(year=2024, period_type=PeriodType.ANNUAL)])
            is_df = pd.DataFrame({"Revenue": [10000000]}, index=pd.DatetimeIndex(["2024-12-31"]))
            bs_df = pd.DataFrame({"Cash": [1000000]}, index=pd.DatetimeIndex(["2024-12-31"]))
            cf_df = pd.DataFrame({"Net Income": [1000000]}, index=pd.DatetimeIndex(["2024-12-31"]))

            result = ModelResult(
                income_statement=is_df,
                balance_sheet=bs_df,
                cash_flow=cf_df,
                assumptions=None,
                periods=periods,
                bs_balance_result=BalanceResult(balanced=True, iterations=1, max_diff=0, plug_values={}, errors=[]),
                cf_balance_result=BalanceResult(balanced=True, iterations=1, max_diff=0, plug_values={}, errors=[]),
                metadata={"model_name": "Test"},
            )
            output = Path(tmpdir) / "output"
            _export_csv(result, output)
            assert (output / "income_statement.csv").exists()
            assert (output / "balance_sheet.csv").exists()
            assert (output / "cash_flow.csv").exists()

    def test_export_json(self):
        """Test _export_json (lines 285-308)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            periods = PeriodIndex([Period(year=2024, period_type=PeriodType.ANNUAL)])
            is_df = pd.DataFrame({"Revenue": [10000000]}, index=pd.DatetimeIndex(["2024-12-31"]))
            bs_df = pd.DataFrame({"Cash": [1000000]}, index=pd.DatetimeIndex(["2024-12-31"]))
            cf_df = pd.DataFrame({"Net Income": [1000000]}, index=pd.DatetimeIndex(["2024-12-31"]))

            result = ModelResult(
                income_statement=is_df,
                balance_sheet=bs_df,
                cash_flow=cf_df,
                assumptions=None,
                periods=periods,
                bs_balance_result=BalanceResult(balanced=True, iterations=1, max_diff=0, plug_values={}, errors=[]),
                cf_balance_result=BalanceResult(balanced=True, iterations=1, max_diff=0, plug_values={}, errors=[]),
                metadata={"model_name": "Test"},
            )
            output = Path(tmpdir) / "output.json"
            _export_json(result, output)
            assert output.exists()
