"""Charts and formula limits are data: chart specs in definitions, limits in the settings."""

from __future__ import annotations

import pytest
import yaml

from financial_model import config_loader
from financial_model.core.expressions import FormulaError, compile_formula, evaluate_values, limits
from financial_model.core.period import PeriodIndex
from financial_model.models.registry import MODEL_CLASSES, reload_definitions
from financial_model.models.three_statement import ThreeStatementModel
from financial_model.visualization import ChartBuilder
from financial_model.visualization.chart_builder import CHART_SOURCES, ChartSpecError, register_chart_source


@pytest.fixture
def definitions_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FIN_MODELS_DEFINITION_PATHS", str(tmp_path))
    config_loader._reset_cache()
    reload_definitions()
    yield tmp_path
    monkeypatch.delenv("FIN_MODELS_DEFINITION_PATHS")
    config_loader._reset_cache()
    reload_definitions()


def _model(key, **kwargs):
    model = MODEL_CLASSES[key](**kwargs)
    model.set_periods(PeriodIndex.generate(2024, 3))
    return model


def _write(directory, key, doc):
    (directory / f"{key}.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    reload_definitions()


class TestChartsFromDefinitions:
    def test_charts_follow_the_definition_order(self):
        names = list(ChartBuilder().model_charts(_model("dcf")))
        assert names == [
            "profitability",
            "margins",
            "cash_flow",
            "balance_sheet",
            "dcf_fcf",
            "dcf_bridge",
            "dcf_sensitivity",
        ]

    def test_a_definition_adds_changes_and_removes_charts(self, definitions_dir):
        _write(
            definitions_dir,
            "charted",
            {
                "model_type": "charted",
                "extends": "three-statement",
                "charts": {
                    "balance_sheet": None,
                    "margins": {"title": "Margin Profile"},
                    "net_income_bridge": {
                        "kind": "waterfall",
                        "source": "income_statement",
                        "title": "Net income",
                        "steps": [
                            {"label": "Year 1", "value": "1", "measure": "absolute"},
                            {"label": "Total", "value": "2", "measure": "total"},
                        ],
                    },
                    "opex": {
                        "kind": "bar",
                        "source": "income_statement",
                        "title": "Costs",
                        "series": [{"match": {"prefix": "Total Operating"}, "abs": True}],
                    },
                },
            },
        )
        model = _model("charted")
        charts = ChartBuilder().model_charts(model)
        assert "balance_sheet" not in charts
        assert charts["margins"].layout.title.text == "Margin Profile"
        assert list(charts["net_income_bridge"].data[0].y) == [1.0, 2.0]
        assert min(charts["opex"].data[0].y) >= 0

    def test_requires_skips_a_chart_without_its_line(self, definitions_dir):
        _write(
            definitions_dir,
            "norev",
            {
                "model_type": "norev",
                "extends": "three-statement",
                "charts": {"margins": {"requires": ["No Such Line"]}},
            },
        )
        assert "margins" not in ChartBuilder().model_charts(_model("norev"))

    def test_registered_source(self, definitions_dir):
        register_chart_source("kpis", lambda model, result, scenario: {"x": ["a", "b"], "k": [1.0, 3.0], "n": 7})
        try:
            _write(
                definitions_dir,
                "kpi",
                {
                    "model_type": "kpi",
                    "extends": "three-statement",
                    "charts": {
                        "kpi": {
                            "kind": "line",
                            "source": "kpis",
                            "x": "x",
                            "title": "KPIs ({n})",
                            "series": [{"key": "k", "name": "K"}],
                        }
                    },
                },
            )
            fig = ChartBuilder().model_charts(_model("kpi"))["kpi"]
            assert fig.layout.title.text == "KPIs (7)"
            assert list(fig.data[0].x) == ["a", "b"] and list(fig.data[0].y) == [1.0, 3.0]
        finally:
            CHART_SOURCES.pop("kpis", None)

    @pytest.mark.parametrize(
        ("spec", "message"),
        [
            ({"kind": "bar", "source": "nowhere"}, "unknown chart source"),
            ({"kind": "radar", "source": "income_statement"}, "unknown chart kind"),
            (
                {"kind": "bar", "source": "income_statement", "title": "{t.__class__}", "series": ["Revenue"]},
                "chart text",
            ),
        ],
    )
    def test_invalid_specs_are_errors(self, spec, message):
        with pytest.raises(ChartSpecError, match=message):
            ChartBuilder().charts_for(ThreeStatementModel(), specs={"bad": spec})

    def test_lbo_tranche_group_follows_the_model(self):
        model = _model("lbo")
        names = [t.name for t in ChartBuilder().model_charts(model)["lbo_debt"].data]
        assert names[: len(model.tranches)] == list(model.tranches)
        assert names[-1] == "Cash"


class TestValueExpressions:
    def test_lists_and_numbers(self):
        assert evaluate_values("sum(a) - b", {"a": [1, 2, 3], "b": 1}) == 5.0
        assert evaluate_values("max(a) / len(a)", {"a": [2, 8]}) == 4.0
        assert evaluate_values("x / 0", {"x": 3}) == 0.0

    @pytest.mark.parametrize("source", ["a.real", "a[0]", "__import__('os')", "line('Revenue')"])
    def test_unsafe_or_period_constructs_are_rejected(self, source):
        with pytest.raises(FormulaError):
            evaluate_values(source, {"a": [1]})

    def test_unknown_name(self):
        with pytest.raises(FormulaError, match="unknown name"):
            evaluate_values("missing + 1", {"a": 1})


class TestFormulaLimitsAreSettings:
    def test_limits_come_from_settings(self, monkeypatch):
        assert limits() == {"max_length": 4000, "max_depth": 32}
        monkeypatch.setenv("FIN_FORMULAS_MAX_LENGTH", "10")
        config_loader._reset_cache()
        try:
            with pytest.raises(FormulaError, match="longer than 10"):
                compile_formula("revenue_growth + 1")
        finally:
            monkeypatch.delenv("FIN_FORMULAS_MAX_LENGTH")
            config_loader._reset_cache()
        compile_formula("revenue_growth + 1")
