"""The statement layouts themselves are data: structure, opening balances, templates and layouts."""

from __future__ import annotations

import pandas as pd
import pytest
import yaml

from financial_model import config_loader
from financial_model.core.definitions import load_definition
from financial_model.core.expressions import Context, FormulaError, evaluate
from financial_model.core.line_item import Driver
from financial_model.core.period import PeriodIndex
from financial_model.models.budget import BudgetModel, department_key
from financial_model.models.lbo import LBOModel
from financial_model.models.registry import reload_definitions
from financial_model.models.three_statement import ThreeStatementModel


@pytest.fixture
def definitions_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FIN_MODELS_DEFINITION_PATHS", str(tmp_path))
    config_loader._reset_cache()
    reload_definitions()
    yield tmp_path
    monkeypatch.delenv("FIN_MODELS_DEFINITION_PATHS")
    config_loader._reset_cache()
    reload_definitions()


def _write(directory, key, doc):
    (directory / f"{key}.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    reload_definitions()


def _ties(result):
    assert result.bs_balance_result.balanced, result.bs_balance_result.errors
    assert result.cf_balance_result.balanced, result.cf_balance_result.errors


def _ctx(drivers, n=3, expressions=None):
    return Context(pd.DataFrame(), 0, drivers, None, n, expressions=expressions)


class TestStructureIsData:
    def test_every_line_comes_from_the_definition(self):
        model = ThreeStatementModel()
        structure = load_definition("three-statement")["structure"]
        for stmt in (model.income_statement, model.balance_sheet, model.cash_flow):
            assert [i.name for i in stmt.ordered_items()] == list(structure[stmt.statement_type.value])
        assert all(i.custom_formula is None for i in model.balance_sheet.ordered_items())

    def test_opening_balances_and_retained_earnings_are_data(self):
        model = ThreeStatementModel()
        drivers = model.assumptions.effective_drivers()
        assert model.opening_lines() == load_definition("three-statement")["opening_balances"]
        assert model.opening_balance("Goodwill", drivers) == drivers["goodwill"].get_value(0)
        assert model.opening_balance("Accounts Receivable", drivers) == 0.0
        ob = {line: model.opening_balance(line, drivers) for line in model.opening_lines()}
        expected = (
            ob["Cash & Cash Equivalents"] + ob["Property Plant & Equipment, Gross"]
            + ob["Intangible Assets"] + ob["Goodwill"]
            - ob["Current Portion of Long-Term Debt"] - ob["Long-Term Debt"] - ob["Deferred Tax Liability"]
            - ob["Common Stock"] - ob["Additional Paid-In Capital"]
        )  # fmt: skip
        assert model.opening_retained_earnings(drivers) == pytest.approx(expected)

    def test_operator_changes_the_base_structure(self, definitions_dir):
        base = load_definition("three-statement")
        base["structure"]["income_statement"]["Lease Expense"] = {
            "formula": "-abs(lease_cost)", "order": 46, "description": "Operating leases",
        }  # fmt: skip
        base["structure"]["income_statement"]["Total Operating Expenses"]["children"].append("Lease Expense")
        base["drivers"]["lease_cost"] = {"value": 250000, "unit": "$"}
        base.pop("_source"), base.pop("_chain")
        _write(definitions_dir, "three-statement", base)
        model = ThreeStatementModel()
        model.set_periods(PeriodIndex.generate(2024, 3))
        result = model.calculate()
        _ties(result)
        assert result.income_statement.loc["Lease Expense"].tolist() == [-250000.0] * 3
        # models built on the same engine inherit the changed layout
        lbo = LBOModel()
        assert lbo.income_statement.get_line_item("Lease Expense") is not None

    def test_extending_definition_changes_structure_via_line_items(self, definitions_dir):
        _write(definitions_dir, "lean", {
            "model_type": "lean", "extends": "three-statement",
            "line_items": {"income_statement": {"R&D Expense": None}},
            "opening_balances": {"Goodwill": None},
        })  # fmt: skip
        from financial_model.models.registry import build_model

        model = build_model({"model_type": "lean", "periods": {"start_year": 2024, "years": 3}})
        assert model.income_statement.get_line_item("R&D Expense") is None
        assert "Goodwill" not in model.opening_lines()
        _ties(model.calculate())


class TestFormulaAdditions:
    def test_running_sums_an_expression_with_a_start(self):
        drivers = {"capex": Driver("capex", [-10.0, 20.0, -30.0])}
        ctx = _ctx(drivers)
        assert evaluate("running(abs(capex), 5)", ctx) == 15.0
        ctx.idx = 2
        assert evaluate("running(abs(capex), 5)", ctx) == 65.0
        assert evaluate("running(t)", ctx) == 3.0

    def test_running_arguments(self):
        with pytest.raises(FormulaError, match="running"):
            evaluate("running()", _ctx({}))

    def test_named_expressions_and_cycles(self):
        drivers = {"rate": Driver("rate", 0.1)}
        expr = {"double": "rate * 2", "quad": "double * 2", "loop": "loop + 1"}
        assert evaluate("quad", _ctx(drivers, expressions=expr)) == pytest.approx(0.4)
        with pytest.raises(FormulaError, match="nested"):
            evaluate("loop", _ctx(drivers, expressions=expr))
        # a driver of the same name wins
        assert evaluate("rate", _ctx(drivers, expressions={"rate": "99"})) == pytest.approx(0.1)


class TestBudgetTemplate:
    def test_departments_follow_the_template(self):
        model = BudgetModel(departments=["Customer Success", "O'Brien Lab"])
        model.set_periods(PeriodIndex.generate(2024, 2))
        names = [i.name for i in model.income_statement.ordered_items()]
        for dept in ("Customer Success", "O'Brien Lab"):
            for suffix in ("Headcount", "Cost per Head", "Expense"):
                assert f"{dept} {suffix}" in names
        total = model.income_statement.get_line_item("Total Operating Expense")
        assert total.children == ["Customer Success Expense", "O'Brien Lab Expense"]
        result = model.calculate()
        _ties(result)
        assert result.income_statement.loc["O'Brien Lab Expense"].iloc[0] == pytest.approx(-10 * 150000)

    def test_add_department_uses_template_and_keys(self):
        model = BudgetModel()
        model.add_department("Ops", [2, 3], 100000)
        assert model.income_statement.get_line_item("Ops Expense") is not None
        assert "Ops Expense" in model.income_statement.get_line_item("Total Operating Expense").children
        assert model.assumptions.drivers["ops_headcount"].value == [2, 3]
        assert department_key("G&A") == "gna"

    def test_template_and_keys_are_overridable(self, definitions_dir):
        doc = {k: v for k, v in load_definition("budget").items() if not k.startswith("_")}
        doc["department_keys"]["customer success"] = "cs"
        doc["department_template"]["lines"]["{dept} Software"] = {
            "formula": "-line('{dept} Headcount') * 1000", "offset": 3,
        }  # fmt: skip
        _write(definitions_dir, "budget", doc)
        model = BudgetModel(departments=["Customer Success"])
        model.set_periods(PeriodIndex.generate(2024, 2))
        assert model.department_key("Customer Success") == "cs"
        assert "cs_headcount" in [i.driver.name for i in model.income_statement.ordered_items() if i.driver]
        assert model.income_statement.get_line_item("Customer Success Software").order == 23
        _ties(model.calculate())


class TestLBOLayout:
    def test_layout_names_orders_and_totals(self):
        model = LBOModel()
        layout = load_definition("lbo")["layout"]
        sweep = model.cash_flow.get_line_item(layout["cash_sweep"]["name"])
        assert sweep.order == layout["cash_sweep"]["order"]
        assert sweep.name in model.cash_flow.get_line_item("Financing Cash Flow").children
        for name in model.tranches:
            assert name in model.balance_sheet.get_line_item("Total Liabilities").children

    def test_renamed_lines(self, definitions_dir):
        _write(definitions_dir, "lbo", {
            "model_type": "lbo", "extends": "three-statement",
            **{k: v for k, v in load_definition("lbo").items()
               if k not in ("model_type", "extends", "_source", "_chain", *load_definition("three-statement"))},
        })  # fmt: skip
        doc = yaml.safe_load((definitions_dir / "lbo.yaml").read_text())
        doc["layout"]["cash_sweep"]["name"] = "Excess Cash Paydown"
        _write(definitions_dir, "lbo", doc)
        model = LBOModel()
        model.set_periods(PeriodIndex.generate(2024, 5))
        result = model.calculate()
        _ties(result)
        assert "Excess Cash Paydown" in result.cash_flow.index
        assert "Excess Cash Paydown" in model.debt_schedule(result).columns
