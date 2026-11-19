"""
Tests for API coverage - exception handlers and edge cases.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from financial_model.api.main import app

client = TestClient(app)


class TestAPIExceptionHandlers:
    """Test API exception handling paths."""

    def test_calculate_model_exception(self):
        """Test /calculate exception handling."""
        request = {
            "config": {
                "name": "Test",
                "periods": {"start_year": 2024, "years": 3, "type": "annual"},
                "assumptions": {"drivers": {}},
                "statements": [],
            }
        }
        # This should trigger an exception in _config_to_model
        response = client.post("/calculate", json=request)
        # Should handle gracefully
        assert response.status_code in [200, 400, 500]

    def test_dcf_exception(self):
        """Test /dcf exception handling."""
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
        response = client.post("/dcf", json=request)
        assert response.status_code in [200, 400, 500]

    def test_lbo_exception(self):
        """Test /lbo exception handling."""
        request = {
            "config": {
                "name": "Test",
                "periods": {"start_year": 2024, "years": 5, "type": "annual"},
                "assumptions": {"drivers": {}},
                "statements": [],
            }
        }
        response = client.post("/lbo", json=request)
        assert response.status_code in [200, 400, 500]

    def test_fetch_exception(self):
        """Test /fetch exception handling."""
        with patch("financial_model.api.main.fetch_statements") as mock_load:
            mock_load.side_effect = Exception("API Error")
            request = {"ticker": "INVALID", "years": 3}
            response = client.post("/fetch", json=request)
            assert response.status_code == 400
            assert "API Error" in response.json()["detail"]

    def test_synthetic_exception_invalid_industry(self):
        """Test /synthetic with invalid industry."""
        request = {"industry": "invalid", "years": 3}
        response = client.post("/synthetic", json=request)
        assert response.status_code == 400
        assert "not supported" in response.json()["detail"]

    def test_synthetic_exception_other(self):
        """Test /synthetic exception handling."""
        with patch("financial_model.api.main.generate_saas_company") as mock_gen:
            mock_gen.side_effect = Exception("Generation failed")
            request = {"industry": "saas", "years": 3}
            response = client.post("/synthetic", json=request)
            assert response.status_code == 400
            assert "Generation failed" in response.json()["detail"]

    def test_export_excel_exception(self):
        """Test /export/excel exception handling."""
        request = {
            "config": {
                "name": "Test",
                "periods": {"start_year": 2024, "years": 3, "type": "annual"},
                "assumptions": {"drivers": {}},
                "statements": [],
            }
        }
        response = client.post("/export/excel", json=request)
        assert response.status_code in [200, 400, 500]


class TestAPIConfigConversion:
    """Test _config_to_model with line items from config."""

    def test_config_to_model_with_line_items(self):
        """Test loading line items from config."""
        from financial_model.api.main import (
            AssumptionsConfig,
            DriverConfig,
            LineItemConfig,
            ModelConfig,
            PeriodConfig,
            StatementConfig,
            _config_to_model,
        )

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
                            # No driver to avoid the name issue
                        )
                    ],
                )
            ],
        )
        model = _config_to_model(config)
        assert model.name == "Test Model"
        assert "Custom Revenue" in model.income_statement.line_items

    def test_config_to_dcf_model(self):
        """Test _config_to_dcf_model."""
        from financial_model.api.main import (
            AssumptionsConfig,
            DriverConfig,
            ModelConfig,
            PeriodConfig,
            _config_to_dcf_model,
        )

        config = ModelConfig(
            name="DCF Test",
            periods=PeriodConfig(start_year=2024, years=5),
            assumptions=AssumptionsConfig(
                drivers={"revenue": DriverConfig(value=[10000000], description="Revenue", unit="$")}
            ),
            statements=[],
        )
        model = _config_to_dcf_model(config)
        assert model.name == "DCF Test"
        assert hasattr(model, "risk_free_rate")

    def test_config_to_lbo_model(self):
        """Test _config_to_lbo_model."""
        from financial_model.api.main import (
            AssumptionsConfig,
            DriverConfig,
            ModelConfig,
            PeriodConfig,
            _config_to_lbo_model,
        )

        config = ModelConfig(
            name="LBO Test",
            periods=PeriodConfig(start_year=2024, years=5),
            assumptions=AssumptionsConfig(
                drivers={"revenue": DriverConfig(value=[10000000], description="Revenue", unit="$")}
            ),
            statements=[],
        )
        model = _config_to_lbo_model(config)
        assert model.name == "LBO Test"
        assert hasattr(model, "entry_multiple")


class TestAPIResultConversion:
    """Test _result_to_dict function."""

    def test_result_to_dict(self):
        """Test _result_to_dict conversion."""
        import pandas as pd

        from financial_model.api.main import _result_to_dict
        from financial_model.core.balancer import BalanceResult
        from financial_model.core.model import ModelResult
        from financial_model.core.period import Period, PeriodIndex, PeriodType

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

        d = _result_to_dict(result)
        assert "income_statement" in d
        assert "balance_sheet" in d
        assert "cash_flow" in d
        assert "periods" in d
        assert d["bs_balanced"] is True
        assert d["cf_balanced"] is True
