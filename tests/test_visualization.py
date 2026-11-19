"""Tests for ChartBuilder: charts plot the model's own numbers, for every model type."""
from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
import pytest
import yaml

from financial_model.models import build_model
from financial_model.models.lbo import TRANCHES
from financial_model.visualization import ChartBuilder, ChartConfig

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load(name: str):
    return build_model(yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8")), base_dir=CONFIG_DIR)


def _trace(fig: go.Figure, name: str):
    matches = [t for t in fig.data if t.name == name]
    assert matches, f"no trace {name!r} in {[t.name for t in fig.data]}"
    return matches[0]


@pytest.fixture(scope="module")
def builder():
    return ChartBuilder()


class TestConfig:
    def test_theme_maps_to_template(self):
        assert ChartBuilder().theme == "light"
        assert ChartBuilder(theme="dark").config.template == "plotly_dark"
        assert ChartConfig(theme="seaborn").template == "seaborn"

    def test_layout_applied(self, builder):
        fig = builder.bar_chart(["a"], {"s": [1.0]}, "Title", "$")
        assert fig.layout.title.text == "Title"
        assert fig.layout.yaxis.title.text == "$"


class TestStatementCharts:
    @pytest.fixture(scope="class")
    def charts(self, builder):
        model = _load("three_statement_saas.yaml")
        result = model.calculate()
        return result, builder.model_charts(model, result)

    def test_chart_set(self, charts):
        _, figs = charts
        assert list(figs) == ["profitability", "margins", "cash_flow", "balance_sheet"]

    def test_profitability_plots_model_values(self, charts):
        result, figs = charts
        rev = _trace(figs["profitability"], "Revenue")
        assert list(rev.x) == result.labels
        assert list(rev.y) == pytest.approx(result.income_statement.loc["Revenue"].tolist())

    def test_margins_are_ratios(self, charts):
        result, figs = charts
        gm = _trace(figs["margins"], "Gross Margin")
        is_ = result.income_statement
        expected = (is_.loc["Gross Profit"] / is_.loc["Revenue"]).tolist()
        assert list(gm.y) == pytest.approx(expected)
        assert figs["margins"].layout.yaxis.tickformat == ".0%"

    def test_cash_flow_includes_ending_cash(self, charts):
        result, figs = charts
        cash = _trace(figs["cash_flow"], "Ending Cash")
        assert list(cash.y) == pytest.approx(result.balance_sheet.loc["Cash & Cash Equivalents"].tolist())
        assert figs["cash_flow"].layout.barmode == "relative"


class TestDCFCharts:
    def test_bridge_foots_to_equity_value(self, builder):
        model = _load("dcf_saas.yaml")
        result = model.calculate()
        figs = builder.model_charts(model, result)
        assert {"dcf_fcf", "dcf_bridge", "dcf_sensitivity"} <= set(figs)
        dcf = model.calculate_dcf(result)
        bridge = figs["dcf_bridge"].data[0]
        assert len(bridge.y) == len(bridge.measure)
        relative = [v for v, m in zip(bridge.y, bridge.measure) if m == "relative"]
        assert sum(relative) == pytest.approx(dcf["equity_gordon"])
        assert bridge.y[-1] == pytest.approx(dcf["equity_gordon"])

    def test_sensitivity_heatmap_matches_table(self, builder):
        model = _load("dcf_saas.yaml")
        result = model.calculate()
        heat = builder.dcf_charts(model, result)["dcf_sensitivity"].data[0]
        sens = model.sensitivity_analysis(result)
        assert heat.z[0][0] == pytest.approx(sens.iloc[0, 0])
        assert list(heat.x) == list(sens.columns)


class TestLBOCharts:
    def test_debt_stack_and_returns(self, builder):
        model = _load("lbo_saas.yaml")
        result = model.calculate()
        figs = builder.model_charts(model, result)
        assert {"lbo_debt", "lbo_sources", "lbo_returns"} <= set(figs)
        schedule = model.debt_schedule(result)
        tla = _trace(figs["lbo_debt"], next(iter(TRANCHES)))
        assert list(tla.y) == pytest.approx(schedule[next(iter(TRANCHES))].tolist())
        ret = model.calculate_returns(result)
        assert figs["lbo_returns"].data[0].y[-1] == pytest.approx(ret["equity_value_at_exit"])
        assert f"{ret['moic']:.2f}x" in figs["lbo_returns"].layout.title.text

    def test_sources_pie_excludes_zero_amounts(self, builder):
        model = _load("lbo_saas.yaml")
        pie = builder.model_charts(model)["lbo_sources"].data[0]
        assert all(v > 0 for v in pie.values)
        assert "Sponsor Equity" in list(pie.labels)


class TestBudgetCharts:
    def test_budget_views(self, builder):
        model = _load("budget_saas.yaml")
        result = model.calculate()
        figs = builder.model_charts(model, result)
        assert list(figs) == ["arr", "opex", "headcount", "revenue"]
        eng = _trace(figs["headcount"], "Engineering")
        assert list(eng.y) == pytest.approx(result.income_statement.loc["Engineering Headcount"].tolist())
        opex = _trace(figs["opex"], "Engineering Expense")
        assert all(v >= 0 for v in opex.y)


class TestMergerCharts:
    def test_accretion_bars_and_scenarios(self, builder):
        model = _load("ma_saas.yaml")
        figs = builder.model_charts(model)
        assert list(figs) == ["ma_accretion", "ma_eps", "ma_contribution"]
        expected = model.analysis_tables()["Accretion Dilution"]["accretion_dilution_pct"].tolist()
        assert list(figs["ma_accretion"].data[0].y) == pytest.approx(expected)
        downside = builder.model_charts(model, scenario="downside")["ma_accretion"].data[0].y
        assert downside[-1] < expected[-1]


class TestPrimitivesAndOutput:
    def test_empty_series_yield_no_chart(self, builder):
        assert builder.bar_chart(["a"], {"x": None, "y": []}, "t") is None
        assert builder.line_chart(["a"], {}, "t") is None

    def test_unsupported_model(self, builder):
        with pytest.raises(TypeError):
            builder.model_charts(object())

    def test_dashboard_is_self_contained_by_default(self, builder, tmp_path):
        figs = builder.model_charts(_load("three_statement_saas.yaml"))
        path = builder.export_dashboard(figs, tmp_path / "d.html", title="T <&>")
        page = path.read_text(encoding="utf-8")
        assert "T &lt;&amp;&gt;" in page
        assert page.count("plotly.js v") == 1  # bundle embedded exactly once
        assert 'src="https://cdn.plot.ly' not in page
        assert page.count('class="chart"') == len(figs)

    def test_dashboard_cdn_mode_is_small(self, builder, tmp_path):
        figs = builder.model_charts(_load("three_statement_saas.yaml"))
        path = builder.export_dashboard(figs, tmp_path / "d.html", offline=False)
        assert 'src="https://cdn.plot.ly' in path.read_text(encoding="utf-8")
        assert path.stat().st_size < 200_000

    def test_dashboard_requires_charts(self, builder, tmp_path):
        with pytest.raises(ValueError):
            builder.export_dashboard({}, tmp_path / "x.html")

    def test_export_all_writes_one_file_per_chart(self, builder, tmp_path):
        saved = builder.export_all(_load("budget_saas.yaml"), tmp_path / "charts", offline=False)
        assert sorted(p.name for p in saved.values()) == sorted(f"{k}.html" for k in saved)
        assert all(p.exists() for p in saved.values())

    def test_to_html_returns_markup(self, builder):
        fig = builder.bar_chart(["a"], {"s": [1.0]}, "t")
        assert "<html>" in ChartBuilder.to_html(fig, offline=False)
