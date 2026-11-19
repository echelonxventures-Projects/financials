"""
Tests for API module to achieve 100% coverage - targeting exception handlers and config conversion.
"""

from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from financial_model.api.main import (
    AssumptionsConfig,
    DriverConfig,
    LineItemConfig,
    ModelConfig,
    PeriodConfig,
    StatementConfig,
    _config_to_dcf_model,
    _config_to_lbo_model,
    _config_to_model,
    _result_to_dict,
    app,
)
from financial_model.core.balancer import BalanceResult
from financial_model.core.model import ModelResult
from financial_model.core.period import Period, PeriodIndex, PeriodType

client = TestClient(app)


class TestAPIExceptionHandlersFinal:
    """Test API exception handling paths for full coverage."""

    def test_calculate_exception_handler(self):
        """Test /calculate exception handling (lines 110-111)."""
        request = {
            "config": {
                "name": "Test",
                "periods": {"start_year": 2024, "years": 3, "type": "annual"},
                "assumptions": {"drivers": {}},
                "statements": [],
            }
        }
        # Force an exception by passing invalid config that causes error in _config_to_model
        with patch("financial_model.api.main._config_to_model") as mock_config:
            mock_config.side_effect = Exception("Config error")
            response = client.post("/calculate", json=request)
            assert response.status_code == 400
            assert "Config error" in response.json()["detail"]

    def test_dcf_exception_handler(self):
        """Test /dcf exception handling (lines 136-137)."""
        request = {
            "config": {
                "name": "Test",
                "periods": {"start_year": 2024, "years": 5, "type": "annual"},
                "assumptions": {"drivers": {}},
                "statements": [],
            },
            "wacc_range": [0.08, 0.12],
            "growth_range": [0.02, 0.05],
        }
        with patch("financial_model.api.main._config_to_dcf_model") as mock_config:
            mock_config.side_effect = Exception("DCF config error")
            response = client.post("/dcf", json=request)
            assert response.status_code == 400
            assert "DCF config error" in response.json()["detail"]

    def test_lbo_exception_handler(self):
        """Test /lbo exception handling (lines 152-153)."""
        request = {
            "config": {
                "name": "Test",
                "periods": {"start_year": 2024, "years": 5, "type": "annual"},
                "assumptions": {"drivers": {}},
                "statements": [],
            }
        }
        with patch("financial_model.api.main._config_to_lbo_model") as mock_config:
            mock_config.side_effect = Exception("LBO config error")
            response = client.post("/lbo", json=request)
            assert response.status_code == 400
            assert "LBO config error" in response.json()["detail"]

    def test_export_excel_exception_handler(self):
        """Test /export/excel exception handling (lines 211-212)."""
        request = {
            "config": {
                "name": "Test",
                "periods": {"start_year": 2024, "years": 3, "type": "annual"},
                "assumptions": {"drivers": {}},
                "statements": [],
            }
        }
        with patch("financial_model.api.main._config_to_model") as mock_config:
            mock_config.side_effect = Exception("Export error")
            response = client.post("/export/excel", json=request)
            assert response.status_code == 400
            assert "Export error" in response.json()["detail"]


class TestAPIConfigConversionCoverage:
    """Test _config_to_model, _config_to_dcf_model, _config_to_lbo_model for coverage."""

    def test_config_to_model_with_line_items_and_driver(self):
        """Test _config_to_model with line items that have drivers (lines 260-263)."""
        config = ModelConfig(
            name="Test Model",
            periods=PeriodConfig(start_year=2024, years=3),
            assumptions=AssumptionsConfig(
                drivers={"revenue": DriverConfig(value=[10000000], description="Revenue", unit="$")}
            ),
            statements=[
                StatementConfig(
                    type="income_statement",
                    line_items=[
                        LineItemConfig(
                            name="Custom Revenue",
                            line_type="input",
                            statement_type="income_statement",
                            driver=DriverConfig(value=[5000000], description="Custom", unit="$"),
                        )
                    ],
                )
            ],
        )
        model = _config_to_model(config)
        assert model.name == "Test Model"
        assert "Custom Revenue" in model.income_statement.line_items
        # Verify driver was set
        item = model.income_statement.line_items["Custom Revenue"]
        assert item.driver is not None
        assert item.driver.get_value(0) == 5000000

    def test_config_to_dcf_model_full(self):
        """Test _config_to_dcf_model copies all statements (line 269-279)."""
        config = ModelConfig(
            name="DCF Test",
            periods=PeriodConfig(start_year=2024, years=5),
            assumptions=AssumptionsConfig(
                drivers={"revenue": DriverConfig(value=[10000000], description="Revenue", unit="$")}
            ),
            statements=[
                StatementConfig(
                    type="income_statement",
                    line_items=[
                        LineItemConfig(
                            name="Revenue",
                            line_type="input",
                            statement_type="income_statement",
                            driver=DriverConfig(value=[10000000], description="Rev", unit="$"),
                        )
                    ],
                )
            ],
        )
        model = _config_to_dcf_model(config)
        assert model.name == "DCF Test"
        assert hasattr(model, "risk_free_rate")
        assert "Revenue" in model.income_statement.line_items

    def test_config_to_lbo_model_full(self):
        """Test _config_to_lbo_model copies all statements (line 282-292)."""
        config = ModelConfig(
            name="LBO Test",
            periods=PeriodConfig(start_year=2024, years=5),
            assumptions=AssumptionsConfig(
                drivers={"revenue": DriverConfig(value=[10000000], description="Revenue", unit="$")}
            ),
            statements=[
                StatementConfig(
                    type="income_statement",
                    line_items=[
                        LineItemConfig(
                            name="Revenue",
                            line_type="input",
                            statement_type="income_statement",
                            driver=DriverConfig(value=[10000000], description="Rev", unit="$"),
                        )
                    ],
                )
            ],
        )
        model = _config_to_lbo_model(config)
        assert model.name == "LBO Test"
        assert hasattr(model, "entry_multiple")
        assert "Revenue" in model.income_statement.line_items


class TestAPIResultConversionCoverage:
    """Test _result_to_dict for full coverage."""

    def test_result_to_dict_full(self):
        """Test _result_to_dict with all fields (lines 295-306)."""
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
            metadata={"model_name": "Test", "extra": "data"},
        )

        d = _result_to_dict(result)
        assert "income_statement" in d
        assert "balance_sheet" in d
        assert "cash_flow" in d
        assert "periods" in d
        assert d["bs_balanced"] is True
        assert d["cf_balanced"] is True
        assert d["metadata"]["model_name"] == "Test"
        assert d["metadata"]["extra"] == "data"
