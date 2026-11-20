"""Engines read statement lines through the definition's roles (`lines`), never by a name in code."""

from __future__ import annotations

import pytest
import yaml

from financial_model import config_loader
from financial_model.core.definitions import DefinitionError, load_definition
from financial_model.core.period import PeriodIndex
from financial_model.models.dcf import DCFModel
from financial_model.models.lbo import LBOModel
from financial_model.models.ma import MergerModel
from financial_model.models.registry import MODEL_CLASSES, reload_definitions
from financial_model.models.three_statement import ThreeStatementModel

OPTIONAL_ROLES = {"dividends"}  # read only when the line exists


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


def _model(key):
    model = MODEL_CLASSES[key]()
    model.set_periods(PeriodIndex.generate(2024, 5))
    return model


def _ties(result):
    assert result.bs_balance_result.balanced, result.bs_balance_result.errors
    assert result.cf_balance_result.balanced, result.cf_balance_result.errors


def _renamed_revolver(key: str) -> dict:
    """A definition extending ``key`` whose revolver line is called "Credit Facility"."""
    structure = load_definition("three-statement")["structure"]
    bs, cf = structure["balance_sheet"], structure["cash_flow"]
    engine_total = MODEL_CLASSES[key]().balance_sheet.get_line_item("Total Liabilities")  # incl. engine lines
    liabilities = [("Credit Facility" if c == "Revolver" else c) for c in engine_total.children]
    return {
        "model_type": f"{key}-cf",
        "extends": key,
        "lines": {"revolver": "Credit Facility"},
        "line_items": {
            "balance_sheet": {
                "Revolver": None,
                "Credit Facility": bs["Revolver"],
                "Total Liabilities": {"children": liabilities},
            },
            "cash_flow": {
                "Revolver Draw (Repayment)": {**cf["Revolver Draw (Repayment)"], "formula": "delta('Credit Facility')"}
            },
        },
    }


class TestRoles:
    def test_every_role_names_a_line_of_the_statements(self):
        model = ThreeStatementModel()
        names = {
            i.name for s in (model.income_statement, model.balance_sheet, model.cash_flow) for i in s.ordered_items()
        }
        missing = {r: n for r, n in model.lines.items() if n not in names and r not in OPTIONAL_ROLES}
        assert not missing

    def test_definitions_without_extends_inherit_the_roles(self):
        budget = _model("budget")
        assert budget.line_name("cash") == load_definition("three-statement")["lines"]["cash"]

    def test_missing_role_is_a_clear_error(self):
        with pytest.raises(DefinitionError, match="no line for role 'nope'"):
            ThreeStatementModel().line_name("nope")

    @pytest.mark.parametrize("key", ["three-statement", "dcf", "lbo"])
    def test_renamed_plug_line_still_balances(self, definitions_dir, key):
        _write(definitions_dir, f"{key}-cf", _renamed_revolver(key))
        model = _model(f"{key}-cf")
        assert model.bs_balancer.revolver_line == "Credit Facility"
        result = model.calculate()
        _ties(result)
        assert "Credit Facility" in result.balance_sheet.index and "Revolver" not in result.balance_sheet.index
        if isinstance(model, LBOModel):
            assert "Credit Facility" in model.debt_schedule(result).columns
        if isinstance(model, DCFModel):
            assert model.calculate_dcf(result)["ev_gordon"] != 0

    def test_balancing_limits_are_data(self, definitions_dir):
        _write(
            definitions_dir,
            "loose",
            {"model_type": "loose", "extends": "three-statement", "balancing": {"tolerance": 5.0}},
        )
        model = _model("loose")
        assert model.bs_balancer.tolerance == 5.0 and model.cf_balancer.tolerance == 5.0
        assert model.bs_balancer.max_iterations == load_definition("three-statement")["balancing"]["max_iterations"]


class TestEngineSectionsAreData:
    def test_contribution_rows_come_from_ma_yaml(self, definitions_dir):
        doc = {"model_type": "ma", **{k: v for k, v in load_definition("ma").items() if not k.startswith("_")}}
        doc["contribution"] = [{"label": "Gross Profit", "value": "gross_profit"}, *doc["contribution"][:1]]
        _write(definitions_dir, "ma", doc)
        merger = MergerModel(acquirer=_model("three-statement"), target=_model("three-statement"))
        table = merger.contribution_analysis()
        assert table["Metric"].tolist() == ["Gross Profit", "Revenue"]

    def test_lbo_labels_and_table_names(self, definitions_dir):
        doc = {
            "model_type": "lbo-fr",
            "extends": "lbo",
            "labels": {"source": "Source des fonds"},
            "tables": {"uses": "Emplois"},
        }
        _write(definitions_dir, "lbo-fr", doc)
        model = _model("lbo-fr")
        tables = model.analysis_tables(model.calculate())
        assert (
            "Emplois" in tables and tables[load_definition("lbo")["tables"]["sources"]].index.name == "Source des fonds"
        )
