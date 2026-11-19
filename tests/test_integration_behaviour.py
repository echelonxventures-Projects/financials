"""
End-to-end behaviour tests: statements tie, scenarios apply, YAML overlays defaults,
the LBO debt schedule works, and the CLI does what the README says.
"""

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from financial_model.cli.main import _load_model, _synthetic_to_config, app
from financial_model.core.line_item import Driver
from financial_model.core.period import PeriodIndex, PeriodType
from financial_model.data.loader import derive_drivers_from_financials
from financial_model.data.synthetic import generate_saas_company
from financial_model.models.budget import BudgetModel, department_key
from financial_model.models.dcf import DCFModel
from financial_model.models.lbo import TRANCHES, LBOModel
from financial_model.models.three_statement import ThreeStatementModel

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
runner = CliRunner()
cli = app


def _model(cls=ThreeStatementModel, years=5, **drivers):
    model = cls()
    model.set_periods(PeriodIndex.generate(2024, years))
    for name, value in drivers.items():
        model.assumptions.set_driver(Driver(name, value))
    return model


def _assert_ties(result):
    assert result.bs_balance_result.balanced, result.bs_balance_result.errors
    assert result.cf_balance_result.balanced, result.cf_balance_result.errors
    cf = result.cash_flow
    flows = cf.loc["Operating Cash Flow"] + cf.loc["Investing Cash Flow"] + cf.loc["Financing Cash Flow"]
    assert (flows - cf.loc["Net Change in Cash"]).abs().max() < 1e-6
    assert (cf.loc["Ending Cash Balance"] - result.balance_sheet.loc["Cash & Cash Equivalents"]).abs().max() < 1e-6


class TestThreeStatementIntegrity:
    def test_expenses_reduce_profit(self):
        is_df = _model().calculate().income_statement
        assert (is_df.loc["COGS"] < 0).all()
        assert (is_df.loc["Gross Profit"] < is_df.loc["Revenue"]).all()
        assert (is_df.loc["Operating Income"] < is_df.loc["Gross Profit"]).all()
        # default margins: 1 - 35% - 15% - 25% - 10% = 15% before D&A
        assert is_df.loc["Operating Income"].iloc[0] == pytest.approx(10_000_000 * 0.15 - 150_000)

    def test_negative_percentage_drivers_mean_the_same_thing(self):
        positive = _model(cogs_pct=0.35).calculate().income_statement
        negative = _model(cogs_pct=-0.35).calculate().income_statement
        pd.testing.assert_frame_equal(positive, negative)

    def test_cash_flow_foots_with_every_financing_and_investing_driver(self):
        model = _model(
            sbc=[100_000, 120_000],
            equity_issuance=[2_000_000, 0, -300_000],
            dividends=[0, 50_000],
            acquisitions=[0, 750_000, 0],
            long_term_debt=[1_000_000, 1_500_000, 1_200_000],
            current_debt=[100_000, 50_000],
            intangibles=[200_000, 350_000],
            dlt=[0, 40_000],
            interest_expense=80_000,
            inventory_days=30,
        )
        result = model.calculate()
        _assert_ties(result)
        bs = result.balance_sheet
        assert bs.loc["Goodwill"].iloc[1] == 750_000
        assert bs.loc["Additional Paid-In Capital"].iloc[0] == 5_000_000 + 100_000 + 2_000_000
        assert (bs.loc["Accounts Payable"] > 0).all()
        assert (
            bs.loc["Property Plant & Equipment, Net"]
            == bs.loc["Property Plant & Equipment, Gross"] + bs.loc["Accumulated Depreciation"]
        ).all()

    def test_revolver_funds_a_cash_burning_company(self):
        model = _model(sm_pct=0.60, beginning_cash=100_000, apic=1_101_000, min_cash=50_000)
        result = model.calculate()
        _assert_ties(result)
        bs = result.balance_sheet
        assert (bs.loc["Revolver"] > 0).any()
        assert (bs.loc["Cash & Cash Equivalents"] >= 50_000 - 1e-6).all()

    def test_scenario_changes_results(self):
        model = ThreeStatementModel.from_yaml(str(CONFIG_DIR / "three_statement_saas.yaml"))
        base = model.calculate()
        upside = model.calculate(scenario="upside")
        assert upside.income_statement.loc["Revenue"].iloc[-1] > base.income_statement.loc["Revenue"].iloc[-1]
        assert upside.metadata["scenario"] == "upside"
        _assert_ties(upside)
        with pytest.raises(ValueError, match="Unknown scenario"):
            model.calculate(scenario="no-such-scenario")

    def test_yaml_overlays_defaults_and_revenue_driver_is_used(self, tmp_path):
        cfg = {
            "name": "Overlay",
            "periods": {"start_year": 2025, "years": 2},
            "assumptions": {"drivers": {"revenue": {"value": 50_000_000}}},
        }
        path = tmp_path / "m.yaml"
        path.write_text(yaml.dump(cfg))
        model = ThreeStatementModel.from_yaml(str(path))
        assert "apic" in model.assumptions.drivers  # defaults kept
        result = model.calculate()
        assert result.income_statement.loc["Revenue"].iloc[0] == 50_000_000
        _assert_ties(result)

    def test_yaml_round_trip_preserves_results(self, tmp_path):
        model = _model(years=3, sbc=250_000)
        path = tmp_path / "roundtrip.yaml"
        model.to_yaml(str(path))
        loaded = _load_model(path)
        pd.testing.assert_frame_equal(model.calculate().balance_sheet, loaded.calculate().balance_sheet)

    def test_readme_custom_line_item(self, tmp_path):
        cfg = {
            "periods": {"start_year": 2024, "years": 2},
            "statements": [
                {
                    "type": "income_statement",
                    "line_items": [
                        {
                            "name": "Custom Metric",
                            "line_type": "calculated",
                            "statement_type": "income_statement",
                            "calculation_rule": "pct_of_revenue",
                            "calculation_params": {"driver": "custom_pct"},
                            "driver": {"name": "custom_pct", "value": 0.05, "unit": "%"},
                            "order": 95,
                        }
                    ],
                }
            ],
        }
        path = tmp_path / "custom.yaml"
        path.write_text(yaml.dump(cfg))
        result = ThreeStatementModel.from_yaml(str(path)).calculate()
        assert result.income_statement.loc["Custom Metric"].iloc[0] == pytest.approx(500_000)


class TestDCF:
    def test_wacc_survives_yaml_without_dcf_drivers(self, tmp_path):
        path = tmp_path / "plain.yaml"
        path.write_text(yaml.dump({"periods": {"start_year": 2024, "years": 5}}))
        model = DCFModel.from_yaml(str(path))
        dcf = model.calculate_dcf()
        assert dcf["wacc"] == pytest.approx(0.111 * 0.8 + 0.06 * 0.79 * 0.2)
        assert dcf["ev_gordon"] != dcf["ev_multiple"]

    def test_fcf_build_and_equity_bridge(self):
        model = DCFModel.from_yaml(str(CONFIG_DIR / "dcf_saas.yaml"))
        result = model.calculate()
        dcf = model.calculate_dcf(result)
        row = dcf["fcf_detail"][0]
        assert row["fcf"] == pytest.approx(row["nopat"] + row["dna"] - row["capex"] + row["nwc_change"])
        assert row["dna"] > 0 and row["capex"] > 0
        assert dcf["net_debt"] == pytest.approx(2_000_000 - 500_000)
        assert dcf["equity_gordon"] == pytest.approx(dcf["ev_gordon"] - dcf["net_debt"])
        bear = model.calculate_dcf(model.calculate(scenario="bear"))
        assert bear["ev_multiple"] < dcf["ev_multiple"]

    def test_sensitivity_is_monotonic(self):
        sens = _model(DCFModel).sensitivity_analysis()
        assert sens.shape == (5, 5)
        assert (sens.iloc[0].diff().dropna() < 0).all()  # higher WACC -> lower value
        assert (sens.iloc[:, 0].diff().dropna() > 0).all()  # higher growth -> higher value


class TestLBO:
    def setup_method(self):
        self.model = _model(LBOModel)
        self.result = self.model.calculate()

    def test_statements_tie_with_debt_schedule(self):
        _assert_ties(self.result)

    def test_sources_equal_uses(self):
        su = self.model.sources_uses(self.result)
        assert su["sources"]["Amount"].sum() == pytest.approx(su["uses"]["Amount"].sum())
        assert su["entry_ev"] == pytest.approx(10 * su["entry_ebitda"])
        assert su["equity_amt"] == pytest.approx(0.40 * su["total_uses"])

    def test_interest_and_paydown(self):
        is_df, bs = self.result.income_statement, self.result.balance_sheet
        deal = self.model.deal(self.result.drivers)
        assert is_df.loc["TLA Interest"].iloc[0] == pytest.approx(-deal["tranches"]["Term Loan A"] * 0.065)
        assert is_df.loc["Interest Expense"].iloc[0] < 0
        assert is_df.loc["Pre-Tax Income"].iloc[0] == pytest.approx(
            is_df.loc["Operating Income"].iloc[0] + is_df.loc["Interest Expense"].iloc[0]
        )
        assert bs.loc["Term Loan A"].diff().dropna().le(1e-9).all()
        assert bs.loc["Term Loan A"].iloc[-1] < deal["tranches"]["Term Loan A"]
        # bullets stay flat
        assert bs.loc["Mezzanine Debt"].nunique() == 1
        # year-2 interest is charged on the year-1 closing balance
        assert is_df.loc["TLB Interest"].iloc[1] == pytest.approx(-bs.loc["Term Loan B"].iloc[0] * 0.085)

    def test_opening_balance_sheet_is_recut(self):
        bs = self.result.balance_sheet
        deal = self.model.deal(self.result.drivers)
        assert bs.loc["Goodwill"].iloc[0] == pytest.approx(deal["goodwill"])
        assert bs.loc["Additional Paid-In Capital"].iloc[0] == pytest.approx(deal["equity"])
        assert bs.loc["Retained Earnings"].iloc[0] == pytest.approx(
            self.result.income_statement.loc["Net Income"].iloc[0]
        )

    def test_returns(self):
        r = self.model.calculate_returns(self.result)
        assert r["equity_value_at_exit"] == pytest.approx(r["exit_ev"] - r["net_debt_at_exit"])
        assert r["moic"] == pytest.approx(r["equity_value_at_exit"] / r["equity_invested"])
        assert (1 + r["irr"]) ** r["hold_years"] == pytest.approx(r["moic"])
        assert set(TRANCHES) <= set(self.model.debt_schedule(self.result).columns)

    def test_more_leverage_raises_returns_when_deal_works(self):
        low = _model(LBOModel, equity_contribution=0.60)
        assert self.model.calculate_returns()["irr"] > low.calculate_returns()["irr"]

    def test_unprofitable_target_is_rejected(self):
        with pytest.raises(ValueError, match="entry EBITDA"):
            _model(LBOModel, sm_pct=0.60).calculate()

    def test_example_config_scenarios(self):
        model = LBOModel.from_yaml(str(CONFIG_DIR / "lbo_saas.yaml"))
        base = model.calculate_returns(model.calculate())
        aggressive = model.calculate_returns(model.calculate(scenario="aggressive"))
        assert base["moic"] > 1
        assert aggressive["irr"] > base["irr"]
        _assert_ties(model.calculate(scenario="conservative"))


class TestBudget:
    def test_quarterly_config(self):
        model = _load_model(CONFIG_DIR / "budget_saas.yaml")
        assert isinstance(model, BudgetModel)
        assert model.periods.period_type == PeriodType.QUARTERLY
        assert model.periods.labels() == ["Q1 FY2024", "Q2 FY2024", "Q3 FY2024", "Q4 FY2024"]
        result = model.calculate()
        is_df = result.income_statement
        assert is_df.loc["G&A Headcount"].tolist() == [20, 22, 24, 26]
        assert is_df.loc["Engineering Expense"].iloc[0] == -50 * 45_000
        assert is_df.loc["Operating Income"].iloc[0] == pytest.approx(
            is_df.loc["Revenue"].iloc[0] + is_df.loc["Total Operating Expense"].iloc[0]
        )
        assert result.cf_balance_result.balanced
        freeze = model.calculate(scenario="hiring_freeze").income_statement
        assert freeze.loc["Net Income"].iloc[-1] > is_df.loc["Net Income"].iloc[-1]

    def test_department_keys(self):
        assert department_key("G&A") == "gna"
        assert department_key("R&D") == "rd"
        assert department_key("Customer Success") == "customer_success"

    def test_added_department_flows_to_opex(self):
        model = _model(BudgetModel, years=2)
        before = model.calculate().income_statement.loc["Total Operating Expense"].iloc[0]
        model.add_department("Operations", [5, 6], 100_000)
        after = model.calculate().income_statement.loc["Total Operating Expense"].iloc[0]
        assert after == pytest.approx(before - 500_000)


class TestDataDerivation:
    def test_synthetic_drivers_reproduce_margins(self):
        data = generate_saas_company(start_year=2020, years=5, gross_margin=0.78, arr_growth=0.35)
        drivers = derive_drivers_from_financials(data["income_statement"], data["balance_sheet"], data["cash_flow"])
        assert drivers["cogs_pct"]["value"] == pytest.approx(0.22, abs=1e-6)
        assert drivers["revenue"]["value"] == pytest.approx(data["income_statement"].loc["Revenue"].iloc[-1])
        assert drivers["revenue_growth"]["value"][0] == pytest.approx(0.35, abs=1e-3)
        config = _synthetic_to_config(data, "saas", 5, 2020)
        assert config["assumptions"]["drivers"]["revenue"]["value"] == drivers["revenue"]["value"]

    def test_newest_first_columns_are_handled(self):
        cols = pd.to_datetime(["2023-12-31", "2022-12-31", "2021-12-31"])  # yfinance order
        is_df = pd.DataFrame([[144.0, 120.0, 100.0], [-57.6, -48.0, -40.0]], index=["Revenue", "COGS"], columns=cols)
        drivers = derive_drivers_from_financials(is_df, None, None, projection_years=3)
        assert drivers["revenue"]["value"] == 144.0
        assert drivers["revenue_growth"]["value"][0] == pytest.approx(0.20)
        assert drivers["cogs_pct"]["value"] == pytest.approx(0.40)

    def test_missing_data_falls_back_to_defaults(self):
        drivers = derive_drivers_from_financials(None, {}, pd.DataFrame())
        assert drivers["tax_rate"]["value"] == 0.21
        assert drivers["ar_days"]["value"] == 45.0


class TestCLIEndToEnd:
    def test_output_format_follows_extension(self, tmp_path):
        cfg = tmp_path / "m.yaml"
        assert runner.invoke(cli, ["init", "-o", str(cfg), "--years", "3"]).exit_code == 0

        out_json = tmp_path / "out.json"
        assert runner.invoke(cli, ["run", str(cfg), "-o", str(out_json)]).exit_code == 0
        payload = json.loads(out_json.read_text(encoding="utf-8"))
        assert payload["periods"] == ["FY2024", "FY2025", "FY2026"]
        assert "FY2024" in payload["income_statement"]

        out_csv = tmp_path / "csvs"
        assert runner.invoke(cli, ["run", str(cfg), "-o", str(out_csv)]).exit_code == 0
        assert "FY2024" in (out_csv / "income_statement.csv").read_text(encoding="utf-8").splitlines()[0]

        out_xlsx = tmp_path / "out.xlsx"
        assert runner.invoke(cli, ["run", str(cfg), "-o", str(out_xlsx)]).exit_code == 0
        assert out_xlsx.read_bytes()[:2] == b"PK"

    @pytest.mark.parametrize("model_type", ["three-statement", "dcf", "lbo", "budget"])
    def test_every_template_validates(self, tmp_path, model_type):
        cfg = tmp_path / f"{model_type}.yaml"
        assert runner.invoke(cli, ["init", "--type", model_type, "-o", str(cfg)]).exit_code == 0
        result = runner.invoke(cli, ["validate", str(cfg)])
        assert result.exit_code == 0, result.output
        assert model_type in result.output

    @pytest.mark.parametrize("name", ["three_statement_saas", "dcf_saas", "lbo_saas", "budget_saas"])
    def test_example_configs_validate(self, name):
        result = runner.invoke(cli, ["validate", str(CONFIG_DIR / f"{name}.yaml")])
        assert result.exit_code == 0, result.output

    def test_scenario_flag_changes_output(self, tmp_path):
        cfg = str(CONFIG_DIR / "three_statement_saas.yaml")
        base = runner.invoke(cli, ["run", cfg]).output
        upside = runner.invoke(cli, ["run", cfg, "--scenario", "upside"]).output
        assert "Scenario: upside" in upside
        assert base != upside

    def test_lbo_and_dcf_commands_report_real_numbers(self):
        lbo = runner.invoke(cli, ["lbo", str(CONFIG_DIR / "lbo_saas.yaml")])
        assert lbo.exit_code == 0, lbo.output
        assert "Sources & Uses" in lbo.output and "Debt schedule" in lbo.output
        dcf = runner.invoke(cli, ["dcf", str(CONFIG_DIR / "dcf_saas.yaml")])
        assert dcf.exit_code == 0, dcf.output
        assert "WACC: 0.00%" not in dcf.output
