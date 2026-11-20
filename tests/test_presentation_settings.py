"""Workbook and chart styling come from the ``presentation`` settings."""

from __future__ import annotations

import openpyxl
import pytest

from financial_model import config_loader
from financial_model.core.period import PeriodIndex
from financial_model.exporters.excel_exporter import ExcelExporter, excel_style
from financial_model.models.three_statement import ThreeStatementModel
from financial_model.visualization import ChartBuilder, ChartConfig
from financial_model.visualization.chart_builder import chart_style


@pytest.fixture
def model_result():
    model = ThreeStatementModel()
    model.set_periods(PeriodIndex.generate(2024, 3))
    return model, model.calculate()


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    def write(text: str):
        path = tmp_path / "financial.yaml"
        path.write_text(text, encoding="utf-8")
        monkeypatch.setenv("FIN_CONFIG_PATH", str(path))
        config_loader._reset_cache()
        return path

    yield write
    monkeypatch.delenv("FIN_CONFIG_PATH", raising=False)
    config_loader._reset_cache()


def test_builtin_styles_are_settings():
    assert excel_style()["number_formats"]["percent"] == "0.0%"
    assert chart_style()["palette"][0].startswith("#")
    assert ChartConfig().height == chart_style()["height"]


def test_excel_style_override(tmp_path, model_result, settings_file):
    settings_file(
        "presentation:\n  excel:\n    header: {fill: 112233}\n    sheets: {income_statement: P&L}\n"
        "    columns: {line_item: Posten}\n    number_formats: {number: '#,##0.0'}\n"
    )
    model, result = model_result
    wb = openpyxl.load_workbook(ExcelExporter().export_result(result, tmp_path / "out.xlsx", model=model))
    ws = wb["P&L"]
    assert ws.cell(row=2, column=1).value == "Posten"
    assert ws.cell(row=2, column=1).fill.fgColor.rgb.endswith("112233")
    assert ws.cell(row=3, column=2).number_format == "#,##0.0"
    assert "Balance Sheet" in wb.sheetnames  # untouched keys keep their built-in value


def test_explicit_style_dict(tmp_path, model_result):
    model, result = model_result
    style = {**excel_style(), "percent_units": [], "freeze": {}}
    path = ExcelExporter(style=style).export_result(result, tmp_path / "plain.xlsx", model=model)
    ws = openpyxl.load_workbook(path)["Income Statement"]
    assert ws.freeze_panes is None
    growth_row = next(r for r in range(3, 30) if ws.cell(row=r, column=1).value == "Revenue Growth")
    assert ws.cell(row=growth_row, column=2).number_format == excel_style()["number_formats"]["number"]


def test_chart_style_override(model_result, settings_file):
    settings_file("presentation:\n  charts:\n    palette: ['#000001', '#000002']\n    height: 300\n    theme: dark\n")
    model, result = model_result
    charts = ChartBuilder().model_charts(model, result)
    fig = charts["revenue"] if "revenue" in charts else next(iter(charts.values()))
    assert fig.layout.height == 300
    assert fig.data[0].marker.color == "#000001"
    assert ChartConfig().template == "plotly_dark"


def test_dashboard_page_colours(tmp_path, model_result):
    model, result = model_result
    builder = ChartBuilder(theme="dark")
    out = builder.export_dashboard(builder.model_charts(model, result), tmp_path / "d.html")
    assert chart_style()["page"]["dark"]["background"] in out.read_text(encoding="utf-8")
