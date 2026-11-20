"""PaaS, B2B SaaS and B2C subscription models: hand-checked engines, ties, scenarios, templates."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from financial_model.cli.main import app
from financial_model.core.line_item import Driver
from financial_model.core.period import PeriodIndex, PeriodType
from financial_model.models.business import (
    BUSINESS_MODELS,
    B2BSaaSModel,
    B2CSubscriptionModel,
    PaaSModel,
    business_template,
    per_period_rate,
)
from financial_model.models.registry import build_model, normalize_model_type

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
EXAMPLES = {"paas": "paas.yaml", "b2b-saas": "b2b_saas.yaml", "b2c-subscription": "b2c_subscription.yaml"}


def _ties(result):
    assert result.bs_balance_result.balanced, result.bs_balance_result.errors
    assert result.cf_balance_result.balanced, result.cf_balance_result.errors
    cf = result.cash_flow
    flows = cf.loc["Operating Cash Flow"] + cf.loc["Investing Cash Flow"] + cf.loc["Financing Cash Flow"]
    assert (flows - cf.loc["Net Change in Cash"]).abs().max() < 1e-6
    assert (cf.loc["Ending Cash Balance"] - result.balance_sheet.loc["Cash & Cash Equivalents"]).abs().max() < 1e-6


def _model(cls, period_type=PeriodType.ANNUAL, years=5, **drivers):
    model = cls()
    model.set_periods(PeriodIndex.generate(2025, years, period_type=period_type))
    for name, value in drivers.items():
        model.assumptions.set_driver(Driver(name, value))
    return model


@pytest.mark.parametrize("cls", list(BUSINESS_MODELS.values()))
@pytest.mark.parametrize("period_type", list(PeriodType))
def test_statements_tie(cls, period_type):
    _ties(_model(cls, period_type, years=2).calculate())


@pytest.mark.parametrize("key", list(BUSINESS_MODELS))
def test_example_configs_run_every_scenario(key):
    model = build_model(yaml.safe_load((CONFIG_DIR / EXAMPLES[key]).read_text(encoding="utf-8")))
    assert model.model_type == key
    revenues = {}
    for scenario in ("base", "upside", "downside"):
        result = model.calculate(scenario)
        _ties(result)
        revenues[scenario] = result.income_statement.loc["Revenue"].iloc[-1]
        assert "Unit Economics" in model.analysis_tables(result)
    assert revenues["downside"] < revenues["base"] < revenues["upside"]


def test_aliases():
    assert normalize_model_type("B2B") == "b2b-saas"
    assert normalize_model_type("b2c") == "b2c-subscription"
    assert normalize_model_type("usage_based") == "paas"


def test_b2b_engine_by_hand():
    m = _model(B2BSaaSModel, sales_reps=10, annual_expansion=0.18, annual_logo_churn=0.10)
    result = m.calculate()
    ue = m.unit_economics(result)
    # 200 customers x $50k = $10m ARR; 10 reps x $700k x 70% = $4.9m new ARR
    assert ue.loc["New ARR", "FY2025"] == pytest.approx(4_900_000)
    assert ue.loc["Expansion ARR", "FY2025"] == pytest.approx(1_800_000)
    assert ue.loc["Churned ARR", "FY2025"] == pytest.approx(1_000_000)
    assert ue.loc["Ending ARR", "FY2025"] == pytest.approx(15_700_000)
    assert ue.loc["New Logos", "FY2025"] == pytest.approx(4_900_000 / 55_000)
    # Revenue = average ARR + 5% services; COGS = 20% subscription + 80% services
    sub = (10_000_000 + 15_700_000) / 2
    is_df = result.income_statement
    assert is_df.loc["Revenue"].iloc[0] == pytest.approx(sub * 1.05)
    assert is_df.loc["COGS"].iloc[0] == pytest.approx(-(sub * 0.20 + sub * 0.05 * 0.80))
    # S&M = reps x $250k + $1.5m demand gen + 5% of revenue
    assert is_df.loc["Sales & Marketing Expense"].iloc[0] == pytest.approx(-(2_500_000 + 1_500_000 + 0.05 * sub * 1.05))
    assert ue.loc["Net Revenue Retention (annualised)", "FY2025"] == pytest.approx(0.9 * 1.18)


def test_b2b_quarterly_books_the_same_new_arr_as_annual():
    annual = _model(B2BSaaSModel, years=1, sales_reps=10)
    quarterly = _model(B2BSaaSModel, PeriodType.QUARTERLY, years=1, sales_reps=10)
    a = annual.unit_economics(annual.calculate())
    q = quarterly.unit_economics(quarterly.calculate())
    assert q.loc["New ARR"].sum() == pytest.approx(a.loc["New ARR"].iloc[0])
    # Churn compounds within the year to the same annual rate on a closed book
    assert per_period_rate(0.10, 4) == pytest.approx(1 - 0.9**0.25)


def test_b2c_engine_by_hand():
    m = _model(B2CSubscriptionModel, PeriodType.MONTHLY, years=1)
    result = m.calculate()
    ue = m.unit_economics(result)
    first = ue.columns[0]
    paid = 4_000_000 / 40
    new = paid * 1.6
    churned = (100_000 + new / 2) * 0.05
    end = 100_000 + new - churned
    assert ue.loc["New Subscribers - Paid", first] == pytest.approx(paid)
    assert ue.loc["Subscribers (end of period)", first] == pytest.approx(end)
    revenue = (100_000 + end) / 2 * 9.99
    assert result.income_statement.loc["Revenue"].iloc[0] == pytest.approx(revenue)
    assert result.income_statement.loc["COGS"].iloc[0] == pytest.approx(-revenue * 0.25)
    # LTV = ARPU x gross margin / monthly churn
    assert ue.loc["Lifetime Value (gross-margin basis)", first] == pytest.approx(9.99 * 0.75 / 0.05)


def test_paas_engine_by_hand():
    m = _model(PaaSModel, years=2)
    result = m.calculate()
    ue = m.unit_economics(result)
    accounts_end = 1_000 + 20_000 * 0.03 - 1_000 * 0.15
    avg = (1_000 + accounts_end) / 2
    usage_rev = avg * (5_000 - 500) * 0.10 * 0.95 * 12
    platform = avg * 100 * 12
    assert ue.loc["Paying Accounts (end of period)", "FY2025"] == pytest.approx(accounts_end)
    assert result.income_statement.loc["Revenue"].iloc[0] == pytest.approx(usage_rev + platform)
    infra = (avg * 5_000 + 10_000 * 300) * 12 * 0.03
    assert ue.loc["Infrastructure Cost", "FY2025"] == pytest.approx(infra)
    assert result.income_statement.loc["COGS"].iloc[0] == pytest.approx(-(infra + 0.05 * (usage_rev + platform)))
    # usage per surviving account compounds at 30% a year
    assert ue.loc["Usage per Account (units / month)", "FY2026"] == pytest.approx(6_500)


def test_yaml_overlays_business_defaults():
    config = business_template("b2c-subscription", years=3)
    config["assumptions"]["drivers"]["monthly_arpu"] = {"value": 14.99}
    model = build_model(config)
    ue = model.unit_economics(model.calculate())
    assert ue.loc["ARPU (monthly)"].iloc[0] == pytest.approx(14.99)


def test_more_marketing_buys_more_customers():
    lo = _model(B2CSubscriptionModel, marketing_spend=1_000_000)
    hi = _model(B2CSubscriptionModel, marketing_spend=8_000_000)
    lo_subs = lo.unit_economics(lo.calculate()).loc["Subscribers (end of period)"].iloc[-1]
    hi_subs = hi.unit_economics(hi.calculate()).loc["Subscribers (end of period)"].iloc[-1]
    assert hi_subs > lo_subs


@pytest.mark.parametrize("key", list(BUSINESS_MODELS))
def test_cli_init_and_run(key, tmp_path):
    runner = CliRunner()
    cfg = tmp_path / "m.yaml"
    out = runner.invoke(app, ["init", "--type", key, "-o", str(cfg), "--years", "3"])
    assert out.exit_code == 0, out.output
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["model_type"] == key
    xlsx = tmp_path / "m.xlsx"
    out = runner.invoke(app, ["run", str(cfg), "-o", str(xlsx)])
    assert out.exit_code == 0, out.output
    from openpyxl import load_workbook

    assert "Unit Economics" in load_workbook(xlsx).sheetnames


def test_defaults_come_from_definition_files(tmp_path, monkeypatch):
    from financial_model import config_loader
    from financial_model.core.definitions import BUILTIN_DIR, clear_cache, load_definition

    for key in BUSINESS_MODELS:
        assert (BUILTIN_DIR / f"{key}.yaml").is_file()
        assert load_definition(key)["_chain"] == [key, "business", "three-statement"]

    # An operator's copy replaces the built-in file; `extends` keeps everything else.
    (tmp_path / "b2c-subscription.yaml").write_text(
        yaml.safe_dump(
            {
                "model_type": "b2c-subscription",
                "extends": "business",
                "name": "My Consumer App",
                "drivers": {**load_definition("b2c-subscription")["drivers"], "monthly_arpu": {"value": 4.99}},
                "kpi_rows": load_definition("b2c-subscription")["kpi_rows"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FIN_MODELS_DEFINITION_PATHS", str(tmp_path))
    config_loader._reset_cache()
    clear_cache()
    try:
        model = B2CSubscriptionModel()
        assert model.name == "My Consumer App"
        assert model.assumptions.drivers["monthly_arpu"].get_value(0) == pytest.approx(4.99)
        assert model.assumptions.drivers["tax_rate"].get_value(0) == pytest.approx(0.21)  # inherited
        model.set_periods(PeriodIndex.generate(2025, 3))
        _ties(model.calculate())
    finally:
        monkeypatch.delenv("FIN_MODELS_DEFINITION_PATHS")
        config_loader._reset_cache()
        clear_cache()
