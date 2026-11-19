"""
API endpoint tests.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from financial_model.api.main import _config_to_dcf_model, _config_to_lbo_model, _config_to_model, app

client = TestClient(app)


class TestHealthEndpoint:
    def test_health(self):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        assert "version" in response.json()


class TestCalculateEndpoint:
    def test_calculate_valid_request(self):
        request = {
            "config": {
                "name": "Test Model",
                "periods": {"start_year": 2024, "years": 3, "type": "annual"},
                "assumptions": {
                    "drivers": {
                        "revenue": {"value": [10000000, 12000000, 14400000], "description": "Revenue", "unit": "$"},
                        "revenue_growth": {"value": 0.20, "description": "Growth", "unit": "%"},
                        "cogs_pct": {"value": -0.35, "description": "COGS %", "unit": "%"},
                        "rd_pct": {"value": -0.15, "description": "R&D %", "unit": "%"},
                        "sm_pct": {"value": -0.15, "description": "S&M %", "unit": "%"},
                        "gna_pct": {"value": -0.10, "description": "G&A %", "unit": "%"},
                        "tax_rate": {"value": 0.21, "description": "Tax Rate", "unit": "%"},
                        "ar_days": {"value": 45, "description": "AR Days", "unit": "days"},
                        "ap_days": {"value": 60, "description": "AP Days", "unit": "days"},
                        "deferred_rev_pct": {"value": 0.10, "description": "Deferred Rev %", "unit": "%"},
                        "capex": {"value": -500000, "description": "CapEx", "unit": "$"},
                        "depreciation_rate": {"value": 0.10, "description": "Dep Rate", "unit": "%"},
                    }
                },
                "statements": [],
            }
        }
        response = client.post("/calculate", json=request)
        assert response.status_code == 200
        data = response.json()
        assert "income_statement" in data
        assert "balance_sheet" in data
        assert "cash_flow" in data
        assert data["bs_balanced"] is True
        assert data["cf_balanced"] is True

    def test_calculate_with_scenario(self):
        request = {
            "config": {
                "name": "Test Model",
                "periods": {"start_year": 2024, "years": 3, "type": "annual"},
                "assumptions": {
                    "drivers": {
                        "revenue": {"value": [10000000, 12000000, 14400000], "description": "Revenue", "unit": "$"},
                        "revenue_growth": {"value": 0.20, "description": "Growth", "unit": "%"},
                        "cogs_pct": {"value": -0.35, "description": "COGS %", "unit": "%"},
                    }
                },
                "statements": [],
            },
            "scenario": "upside",
        }
        response = client.post("/calculate", json=request)
        assert response.status_code == 200


class TestDCFEndpoint:
    def test_dcf_valid_request(self):
        request = {
            "config": {
                "name": "DCF Test",
                "periods": {"start_year": 2024, "years": 5, "type": "annual"},
                "assumptions": {
                    "drivers": {
                        "revenue": {
                            "value": [10000000, 12000000, 14400000, 17280000, 20736000],
                            "description": "Revenue",
                            "unit": "$",
                        },
                        "revenue_growth": {"value": 0.20, "description": "Growth", "unit": "%"},
                        "cogs_pct": {"value": -0.35, "description": "COGS %", "unit": "%"},
                        "rd_pct": {"value": -0.15, "description": "R&D %", "unit": "%"},
                        "sm_pct": {"value": -0.15, "description": "S&M %", "unit": "%"},
                        "gna_pct": {"value": -0.10, "description": "G&A %", "unit": "%"},
                        "tax_rate": {"value": 0.21, "description": "Tax Rate", "unit": "%"},
                        "ar_days": {"value": 45, "description": "AR Days", "unit": "days"},
                        "ap_days": {"value": 60, "description": "AP Days", "unit": "days"},
                        "deferred_rev_pct": {"value": 0.10, "description": "Deferred Rev %", "unit": "%"},
                        "capex": {"value": -500000, "description": "CapEx", "unit": "$"},
                        "depreciation_rate": {"value": 0.10, "description": "Dep Rate", "unit": "%"},
                    }
                },
                "statements": [],
            },
            "wacc_range": [0.08, 0.12],
            "growth_range": [0.02, 0.05],
        }
        response = client.post("/dcf", json=request)
        assert response.status_code == 200
        data = response.json()
        assert "model_result" in data
        assert "dcf" in data
        assert "sensitivity" in data
        assert "wacc" in data["dcf"]
        assert data["dcf"]["wacc"] > 0


class TestLBOEndpoint:
    def test_lbo_valid_request(self):
        request = {
            "config": {
                "name": "LBO Test",
                "periods": {"start_year": 2024, "years": 5, "type": "annual"},
                "assumptions": {
                    "drivers": {
                        "revenue": {
                            "value": [10000000, 12000000, 14400000, 17280000, 20736000],
                            "description": "Revenue",
                            "unit": "$",
                        },
                        "revenue_growth": {"value": 0.20, "description": "Growth", "unit": "%"},
                        "cogs_pct": {"value": -0.35, "description": "COGS %", "unit": "%"},
                        "rd_pct": {"value": -0.15, "description": "R&D %", "unit": "%"},
                        "sm_pct": {"value": -0.15, "description": "S&M %", "unit": "%"},
                        "gna_pct": {"value": -0.10, "description": "G&A %", "unit": "%"},
                        "tax_rate": {"value": 0.21, "description": "Tax Rate", "unit": "%"},
                        "ar_days": {"value": 45, "description": "AR Days", "unit": "days"},
                        "ap_days": {"value": 60, "description": "AP Days", "unit": "days"},
                        "deferred_rev_pct": {"value": 0.10, "description": "Deferred Rev %", "unit": "%"},
                        "capex": {"value": -500000, "description": "CapEx", "unit": "$"},
                        "depreciation_rate": {"value": 0.10, "description": "Dep Rate", "unit": "%"},
                    }
                },
                "statements": [],
            }
        }
        response = client.post("/lbo", json=request)
        assert response.status_code == 200
        data = response.json()
        assert "model_result" in data
        assert "lbo" in data
        assert "moic" in data["lbo"]
        assert "irr" in data["lbo"]


class TestFetchEndpoint:
    @patch("financial_model.api.main.fetch_statements")
    def test_fetch_valid_request(self, mock_load):
        mock_load.return_value = {
            "income_statement": MagicMock(to_dict=lambda: {"Revenue": {}}),
            "balance_sheet": MagicMock(to_dict=lambda: {"Cash": {}}),
            "cash_flow": MagicMock(to_dict=lambda: {"Operating Cash Flow": {}}),
            "key_stats": {"market_cap": 1000000000},
        }
        request = {"ticker": "AAPL", "years": 3}
        response = client.post("/fetch", json=request)
        assert response.status_code == 200
        data = response.json()
        assert data["ticker"] == "AAPL"


class TestSyntheticEndpoint:
    def test_synthetic_saas(self):
        request = {"industry": "saas", "years": 3, "start_year": 2020, "base_arr": 10000000, "arr_growth": 0.35}
        response = client.post("/synthetic", json=request)
        assert response.status_code == 200
        data = response.json()
        assert data["industry"] == "saas"
        assert "income_statement" in data
        assert "balance_sheet" in data
        assert "cash_flow" in data
        assert len(data["periods"]) == 3

    def test_synthetic_invalid_industry(self):
        request = {"industry": "invalid", "years": 3}
        response = client.post("/synthetic", json=request)
        assert response.status_code == 400


class TestExportExcelEndpoint:
    def test_export_excel(self):
        request = {
            "config": {
                "name": "Excel Test",
                "periods": {"start_year": 2024, "years": 3, "type": "annual"},
                "assumptions": {
                    "drivers": {
                        "revenue": {"value": [10000000, 12000000, 14400000], "description": "Revenue", "unit": "$"},
                        "revenue_growth": {"value": 0.20, "description": "Growth", "unit": "%"},
                        "cogs_pct": {"value": -0.35, "description": "COGS %", "unit": "%"},
                        "rd_pct": {"value": -0.15, "description": "R&D %", "unit": "%"},
                        "sm_pct": {"value": -0.15, "description": "S&M %", "unit": "%"},
                        "gna_pct": {"value": -0.10, "description": "G&A %", "unit": "%"},
                        "tax_rate": {"value": 0.21, "description": "Tax Rate", "unit": "%"},
                        "ar_days": {"value": 45, "description": "AR Days", "unit": "days"},
                        "ap_days": {"value": 60, "description": "AP Days", "unit": "days"},
                        "deferred_rev_pct": {"value": 0.10, "description": "Deferred Rev %", "unit": "%"},
                        "capex": {"value": -500000, "description": "CapEx", "unit": "$"},
                        "depreciation_rate": {"value": 0.10, "description": "Dep Rate", "unit": "%"},
                    }
                },
                "statements": [],
            }
        }
        response = client.post("/export/excel", json=request)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class TestConfigConversion:
    def test_config_to_model(self):
        from financial_model.api.main import AssumptionsConfig, DriverConfig, ModelConfig, PeriodConfig

        config = ModelConfig(
            name="Test",
            periods=PeriodConfig(start_year=2024, years=3),
            assumptions=AssumptionsConfig(
                drivers={"revenue": DriverConfig(value=[10000000], description="Revenue", unit="$")}
            ),
            statements=[],
        )
        model = _config_to_model(config)
        assert model.name == "Test"
        assert len(model.periods) == 3
        assert "revenue" in model.assumptions.drivers

    def test_config_to_dcf_model(self):
        from financial_model.api.main import AssumptionsConfig, DriverConfig, ModelConfig, PeriodConfig

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
        from financial_model.api.main import AssumptionsConfig, DriverConfig, ModelConfig, PeriodConfig

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
