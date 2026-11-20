"""Model definitions as data: defaults, inheritance, YAML-only model types and the formula language."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from financial_model import config_loader
from financial_model.core.definitions import BUILTIN_DIR, DefinitionError, clear_cache, load_definition
from financial_model.core.expressions import Context, FormulaError, compile_formula, evaluate
from financial_model.core.line_item import Driver, LineItem
from financial_model.core.period import PeriodIndex
from financial_model.core.starters import starter_config
from financial_model.models.dcf import DCFModel
from financial_model.models.lbo import LBOModel
from financial_model.models.registry import MODEL_CLASSES, build_model, normalize_model_type, reload_definitions
from financial_model.models.three_statement import ThreeStatementModel

EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "config" / "definitions"


def _ties(result):
    assert result.bs_balance_result.balanced, result.bs_balance_result.errors
    assert result.cf_balance_result.balanced, result.cf_balance_result.errors


@pytest.fixture
def definitions_dir(tmp_path, monkeypatch):
    """Point models.definition_paths at a temp dir; restore the registry afterwards."""
    monkeypatch.setenv("FIN_MODELS_DEFINITION_PATHS", str(tmp_path))
    config_loader._reset_cache()
    reload_definitions()
    yield tmp_path
    monkeypatch.delenv("FIN_MODELS_DEFINITION_PATHS")
    config_loader._reset_cache()
    reload_definitions()


def _write(directory, key, doc):
    (directory / f"{key}.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    reload_definitions()


class TestDefaultsAreData:
    def test_every_builtin_model_has_a_definition(self):
        for key in ("three-statement", "dcf", "lbo", "budget", "ma", "paas", "b2b-saas", "b2c-subscription"):
            assert (BUILTIN_DIR / f"{key}.yaml").is_file()
            assert load_definition(key)["model_type"] == key

    def test_model_defaults_equal_the_definition(self):
        model = ThreeStatementModel()
        for name, spec in load_definition("three-statement")["drivers"].items():
            assert model.assumptions.drivers[name].value == spec["value"]
        assert model.name == load_definition("three-statement")["name"]

    def test_extends_merges_and_overrides(self):
        dcf = load_definition("dcf")
        assert dcf["_chain"] == ["dcf", "three-statement"]
        assert "revenue" in dcf["drivers"] and "risk_free_rate" in dcf["drivers"]
        assert "aliases" not in dcf  # aliases are per file

    def test_keyword_overrides_and_attribute_access(self):
        model = DCFModel(beta=1.4)
        assert model.beta == 1.4 and model.assumptions.drivers["beta"].description
        with pytest.raises(TypeError):
            DCFModel(not_a_driver=1)

    def test_operator_copy_replaces_a_default(self, definitions_dir):
        _write(
            definitions_dir,
            "dcf",
            {
                "model_type": "dcf",
                "extends": "three-statement",
                "drivers": {**load_definition("dcf")["drivers"], "terminal_growth": {"value": 0.04}},
                "sensitivity": load_definition("dcf")["sensitivity"],
            },
        )
        assert DCFModel().terminal_growth == 0.04

    def test_lbo_tranches_are_configurable(self, definitions_dir):
        base = load_definition("lbo")
        doc = {k: v for k, v in base.items() if not k.startswith("_") and k not in ("aliases",)}
        doc["extends"] = "three-statement"
        doc["tranches"] = [
            *base["tranches"],
            {"name": "Seller Note", "key": "seller", "amortizing": False, "interest_line": "Seller Note Interest"},
        ]
        doc["drivers"] = {**base["drivers"], "seller_pct": {"value": 0.05}, "seller_rate": {"value": 0.08}}
        _write(definitions_dir, "lbo", doc)
        model = LBOModel()
        model.set_periods(PeriodIndex.generate(2025, 5))
        result = model.calculate()
        _ties(result)
        assert "Seller Note" in result.balance_sheet.index and "Seller Note Interest" in result.income_statement.index
        assert "Seller Note" in model.sources_uses(result)["sources"]["Source"].tolist()

    def test_starters_come_from_definitions(self):
        config = starter_config("lbo", years=3, start_year=2030, variant="saas")
        assert config["periods"] == {"start_year": 2030, "years": 3, "type": "annual"}
        assert config["assumptions"]["drivers"]["hold_years"]["value"] == 3  # capped at years
        ma = starter_config("ma", years=2)
        assert ma["acquirer"]["periods"]["years"] == 2 and len(ma["deal"]["synergy_phase_in"]) == 2
        assert starter_config("three-statement", variant="unknown-industry") == starter_config(
            "three-statement", variant="other"
        )

    def test_aliases_come_from_definitions(self, definitions_dir):
        _write(
            definitions_dir,
            "three-statement",
            {
                **{k: v for k, v in load_definition("three-statement").items() if not k.startswith("_")},
                "aliases": ["three", "3s", "core"],
            },
        )
        assert normalize_model_type("core") == "three-statement"


class TestYamlOnlyModels:
    def test_example_marketplace(self, monkeypatch):
        monkeypatch.setenv("FIN_MODELS_DEFINITION_PATHS", str(EXAMPLE_DIR))
        config_loader._reset_cache()
        reload_definitions()
        try:
            assert normalize_model_type("two-sided") == "marketplace"
            model = build_model({"model_type": "marketplace", "periods": {"start_year": 2025, "years": 3}})
            assert isinstance(model, ThreeStatementModel)  # runs on the nearest engine in `extends`
            result = model.calculate()
            _ties(result)
            is_df = result.income_statement
            assert is_df.loc["GMV"].iloc[1] == pytest.approx(50_000_000 * 1.45)
            assert is_df.loc["Revenue"].iloc[0] == pytest.approx(50_000_000 * 0.12)
            assert "Revenue Growth" not in is_df.index
            kpis = model.all_tables(result)["Marketplace KPIs"]
            assert kpis.loc["Take Rate"].tolist() == pytest.approx([0.12] * 3)
            starter = starter_config("marketplace", years=2)  # no starter section: its own drivers
            assert starter["model_type"] == "marketplace" and "take_rate" in starter["assumptions"]["drivers"]
            _ties(build_model(starter).calculate())
        finally:
            monkeypatch.delenv("FIN_MODELS_DEFINITION_PATHS")
            config_loader._reset_cache()
            reload_definitions()

    def test_new_line_added_to_a_total(self, definitions_dir):
        _write(
            definitions_dir,
            "with-royalty",
            {
                "model_type": "with-royalty",
                "extends": "three-statement",
                "drivers": {"royalty_pct": {"value": 0.03, "description": "Royalty", "unit": "%"}},
                "line_items": {
                    "income_statement": {
                        "Royalty Expense": {
                            "formula": "-line('Revenue') * royalty_pct",
                            "order": 44,
                            "add_to": ["Total Operating Expenses"],
                        }
                    }
                },
            },
        )
        assert "with-royalty" in MODEL_CLASSES
        base = ThreeStatementModel()
        base.set_periods(PeriodIndex.generate(2025, 2))
        model = MODEL_CLASSES["with-royalty"]()
        model.set_periods(PeriodIndex.generate(2025, 2))
        r0, r1 = base.calculate(), model.calculate()
        _ties(r1)
        royalty = r1.income_statement.loc["Royalty Expense"]
        assert royalty.iloc[0] == pytest.approx(-0.03 * r1.income_statement.loc["Revenue"].iloc[0])
        diff = r1.income_statement.loc["Operating Income"] - r0.income_statement.loc["Operating Income"]
        assert diff.tolist() == pytest.approx(royalty.tolist())

    def test_definition_on_definition(self, definitions_dir):
        _write(
            definitions_dir,
            "a-base",
            {"model_type": "a-base", "extends": "three-statement", "abstract": True, "drivers": {"x": {"value": 2}}},
        )
        _write(
            definitions_dir,
            "a-child",
            {
                "model_type": "a-child",
                "extends": "a-base",
                "line_items": {"income_statement": {"X": {"formula": "x * 10", "order": 1}}},
            },
        )
        assert "a-base" not in MODEL_CLASSES  # abstract
        model = MODEL_CLASSES["a-child"]()
        model.set_periods(PeriodIndex.generate(2025, 1))
        assert model.calculate().income_statement.loc["X"].iloc[0] == 20

    def test_bad_definitions_fail_loudly(self, definitions_dir):
        (definitions_dir / "loop-a.yaml").write_text("model_type: loop-a\nextends: loop-b\n", encoding="utf-8")
        (definitions_dir / "loop-b.yaml").write_text("model_type: loop-b\nextends: loop-a\n", encoding="utf-8")
        clear_cache()
        with pytest.raises(DefinitionError, match="extends itself"):
            load_definition("loop-a")
        (definitions_dir / "loop-a.yaml").unlink()
        (definitions_dir / "loop-b.yaml").unlink()
        with pytest.raises(DefinitionError):
            load_definition("does-not-exist")

    def test_formula_lines_in_a_model_config(self):
        config = {
            "model_type": "three-statement",
            "periods": {"start_year": 2025, "years": 2},
            "assumptions": {"drivers": {"bonus_pct": {"value": 0.01}}},
            "statements": [
                {
                    "type": "income_statement",
                    "line_items": [
                        {
                            "name": "Bonus Pool",
                            "line_type": "calculated",
                            "statement_type": "income_statement",
                            "formula": "-line('Revenue') * bonus_pct",
                            "order": 46,
                        }
                    ],
                }
            ],
        }
        result = build_model(config).calculate()
        assert result.income_statement.loc["Bonus Pool"].iloc[0] == pytest.approx(
            -0.01 * result.income_statement.loc["Revenue"].iloc[0]
        )


class TestFormulaLanguage:
    def _ctx(self, idx=1):
        index = pd.MultiIndex.from_tuples([("income_statement", "Revenue"), ("income_statement", "Cost")])
        data = pd.DataFrame([[100.0, 120.0, 150.0], [-40.0, -50.0, -60.0]], index=index)
        drivers = {"g": Driver("g", [0.1, 0.2, 0.3]), "fee": Driver("fee", 5)}
        return Context(data, idx, drivers, "income_statement", 3, 4, 2026, opening=lambda name: 90.0)

    @pytest.mark.parametrize(
        ("src", "expected"),
        [
            ("line('Revenue')", 120.0),
            ("prev('Revenue')", 100.0),
            ("line('Revenue', -1) * (1 + g)", 120.0),
            ("delta('Revenue')", 20.0),
            ("cumulative('Revenue')", 220.0),
            ("total('Revenue', 'Cost')", 70.0),
            ("safe_div(line('Cost'), 0, -1)", -1.0),
            ("clamp(fee * 100, 0, 250)", 250.0),
            ("1 if t > 0 and periods_per_year == 4 else 0", 1.0),
            ("driver('g', 1)", 0.3),
            ("max(fee, 7) + min(1, 2) - abs(-3) + round(2.567, 1)", 7.6),
            ("year + n_periods", 2029.0),
            ("opening('Revenue')", 90.0),
            ("10 / 0", 0.0),
        ],
    )
    def test_evaluates(self, src, expected):
        assert evaluate(src, self._ctx()) == pytest.approx(expected)

    def test_prev_default_in_first_period(self):
        assert evaluate("prev('Revenue', 42)", self._ctx(idx=0)) == 42

    @pytest.mark.parametrize(
        "src",
        [
            "__import__('os').system('echo x')",
            "line.__class__",
            "[1, 2][0]",
            "(lambda: 1)()",
            "open('x')",
            "[x for x in range(3)]",
            "line(name='Revenue')",
            "a = 1",
            "",
            "1" * 5000,
        ],
    )
    def test_rejects_unsafe_or_invalid(self, src):
        with pytest.raises(FormulaError):
            compile_formula(src)

    def test_runtime_errors(self):
        with pytest.raises(FormulaError, match="unknown driver"):
            evaluate("missing_driver * 2", self._ctx())
        with pytest.raises(FormulaError, match="overflow"):
            evaluate("10.0 ** 10000", self._ctx())
        with pytest.raises(FormulaError):
            LineItem("Bad", "calculated", "income_statement", formula="import os")
