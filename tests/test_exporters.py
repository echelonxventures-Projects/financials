"""Tests for the Excel / CSV / JSON exporters against real model results."""

import csv
import json
import math
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
import pytest

from financial_model.exporters import CSVExporter, ExcelExporter, JSONExporter, result_to_dict, to_jsonable
from financial_model.exporters.csv_exporter import slugify
from financial_model.models import build_model
from financial_model.models.three_statement import ThreeStatementModel

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load(name: str):
    import yaml

    return build_model(yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8")), base_dir=CONFIG_DIR)


@pytest.fixture(scope="module")
def three():
    m = _load("three_statement_saas.yaml")
    return m, m.calculate()


@pytest.fixture(scope="module")
def dcf():
    m = _load("dcf_saas.yaml")
    return m, m.calculate()


@pytest.fixture(scope="module")
def lbo():
    m = _load("lbo_saas.yaml")
    return m, m.calculate()


def _sheet_rows(ws) -> dict[str, list]:
    """{column-A label: [values...]} for a statement sheet."""
    return {
        row[0]: list(row[1:]) for row in ws.iter_rows(min_row=3, values_only=True) if row and row[0] is not None
    }


# --------------------------------------------------------------------------- #
# to_jsonable / result_to_dict
# --------------------------------------------------------------------------- #


class TestJsonable:
    def test_numpy_and_nan(self):
        out = to_jsonable({"a": np.float64(1.5), "b": np.int64(2), "c": float("nan"), "d": [np.nan, float("inf")]})
        assert out == {"a": 1.5, "b": 2, "c": None, "d": [None, None]}
        assert isinstance(out["b"], int)

    def test_non_string_keys_and_timestamps(self):
        out = to_jsonable({pd.Timestamp("2024-12-31"): pd.Timestamp("2025-01-01"), 1: "x"})
        assert out == {"2024-12-31 00:00:00": "2025-01-01 00:00:00", "1": "x"}

    def test_dataframe(self):
        df = pd.DataFrame({"FY2024": [1.0, np.nan]}, index=["Revenue", "COGS"])
        assert to_jsonable(df) == {"FY2024": {"Revenue": 1.0, "COGS": None}}

    def test_result_to_dict_uses_period_labels_and_balance_flags(self, three):
        model, result = three
        payload = result_to_dict(result)
        assert payload["periods"] == result.labels
        assert set(payload["income_statement"]) == set(result.labels)
        assert payload["income_statement"]["FY2024"]["Revenue"] == pytest.approx(10_000_000)
        assert payload["bs_balanced"] is True and payload["cf_balanced"] is True
        json.dumps(payload)  # must be serialisable

    def test_result_to_dict_includes_analysis(self, dcf):
        model, result = dcf
        payload = result_to_dict(result, model.analysis_tables(result))
        assert set(payload["analysis"]) == {"DCF Valuation", "Free Cash Flow", "Sensitivity"}
        assert payload["analysis"]["Free Cash Flow"]["FY2024"]["fcf"] == pytest.approx(
            model.calculate_dcf(result)["fcf"][0]
        )
        json.dumps(payload)


# --------------------------------------------------------------------------- #
# Excel
# --------------------------------------------------------------------------- #


class TestExcelExport:
    def test_statement_values_match_result(self, three, tmp_path):
        model, result = three
        path = model.export_excel(tmp_path / "m.xlsx", result)
        wb = openpyxl.load_workbook(path)
        assert wb.sheetnames == ["Income Statement", "Balance Sheet", "Cash Flow", "Assumptions"]

        ws = wb["Income Statement"]
        header = [c.value for c in ws[2]]
        assert header == ["Line Item", *result.labels]
        rows = _sheet_rows(ws)
        for line in ("Revenue", "Net Income"):
            assert rows[line] == pytest.approx(result.income_statement.loc[line].tolist())

        bs_rows = _sheet_rows(wb["Balance Sheet"])
        assert bs_rows["Total Assets"] == pytest.approx(result.balance_sheet.loc["Total Assets"].tolist())

    def test_formats_totals_and_percentages(self, three, tmp_path):
        model, result = three
        wb = openpyxl.load_workbook(model.export_excel(tmp_path / "m.xlsx", result))
        ws = wb["Income Statement"]
        by_label = {row[0].value: row for row in ws.iter_rows(min_row=3)}
        assert by_label["Revenue Growth"][1].number_format == "0.0%"
        assert by_label["Revenue"][1].number_format == "#,##0"
        assert by_label["Net Income"][0].font.bold  # is_total line

    def test_assumptions_sheet_lists_effective_drivers(self, three, tmp_path):
        model, result = three
        wb = openpyxl.load_workbook(model.export_excel(tmp_path / "m.xlsx", result))
        ws = wb["Assumptions"]
        drivers = {row[0]: row for row in ws.iter_rows(min_row=3, values_only=True)}
        assert drivers["revenue_growth"][3:8] == pytest.approx([0.35, 0.30, 0.25, 0.20, 0.15])

    def test_dcf_workbook_has_valuation_sheets(self, dcf, tmp_path):
        model, result = dcf
        wb = openpyxl.load_workbook(model.export_excel(tmp_path / "dcf.xlsx", result))
        assert {"DCF Valuation", "Free Cash Flow", "Sensitivity"} <= set(wb.sheetnames)
        metrics = {r[0]: r[1] for r in wb["DCF Valuation"].iter_rows(min_row=4, values_only=True)}
        valuation = model.calculate_dcf(result)
        assert metrics["ev_gordon"] == pytest.approx(valuation["ev_gordon"])
        assert metrics["price_gordon"] == pytest.approx(valuation["price_gordon"])
        # percentages are formatted as such
        cells = {r[0].value: r[1] for r in wb["DCF Valuation"].iter_rows(min_row=4)}
        assert cells["wacc"].number_format == "0.0%"

    def test_lbo_workbook_has_deal_sheets(self, lbo, tmp_path):
        model, result = lbo
        wb = openpyxl.load_workbook(model.export_excel(tmp_path / "lbo.xlsx", result))
        assert {"LBO Returns", "Sources", "Uses", "Debt Schedule"} <= set(wb.sheetnames)
        metrics = {r[0]: r[1] for r in wb["LBO Returns"].iter_rows(min_row=4, values_only=True)}
        assert metrics["moic"] == pytest.approx(model.calculate_returns(result)["moic"])

    def test_appends_extension_and_creates_dirs(self, three, tmp_path):
        model, result = three
        path = ExcelExporter().export_result(result, tmp_path / "nested" / "out", model=model)
        assert path.suffix == ".xlsx" and path.exists()

    def test_relative_paths_resolve_against_output_dir(self, three, tmp_path):
        _, result = three
        path = ExcelExporter(output_dir=tmp_path).export_result(result, "rel.xlsx")
        assert path == (tmp_path / "rel.xlsx").resolve()

    def test_nan_values_written_as_zero(self, tmp_path):
        from financial_model.core.period import PeriodIndex

        model = ThreeStatementModel()
        model.set_periods(PeriodIndex.generate(start_year=2024, years=2))
        result = model.calculate()
        result.income_statement.iloc[0, 0] = np.nan
        wb = openpyxl.load_workbook(ExcelExporter().export_result(result, tmp_path / "n.xlsx", model=model))
        assert wb["Income Statement"].cell(row=3, column=2).value == 0

    def test_export_tables_and_sheet_name_sanitising(self, tmp_path):
        tables = {
            "A/B: [bad]*?": {"x": 1.0, "nested": {"ignored": 1}},
            "A/B: [bad]*?2": pd.DataFrame({"v": [1, 2]}, index=["r1", "r2"]),
            "Records": [{"a": 1, "b": 2}],
        }
        wb = openpyxl.load_workbook(ExcelExporter().export_tables(tables, tmp_path / "t.xlsx"))
        assert wb.sheetnames[0] == "AB bad"
        assert all(len(n) <= 31 for n in wb.sheetnames)
        assert len(set(wb.sheetnames)) == 3

    def test_duplicate_truncated_names_get_suffixes(self, tmp_path):
        long = "X" * 40
        wb = openpyxl.Workbook()
        ex = ExcelExporter()
        ex.write_table(wb, long, {"a": 1})
        ex.write_table(wb, long, {"a": 2})
        assert wb.sheetnames[-2:] == ["X" * 31, "X" * 27 + " (2)"]

    def test_export_statements(self, three, tmp_path):
        _, result = three
        path = ExcelExporter().export_statements({"IS": result.income_statement}, str(tmp_path / "s.xlsx"))
        ws = openpyxl.load_workbook(path)["IS"]
        assert ws.cell(row=4, column=1).value == "Revenue"

    def test_sensitivity_from_dict_and_dataframe(self, dcf, tmp_path):
        model, result = dcf
        sens = model.sensitivity_analysis(result)
        p1 = ExcelExporter().export_sensitivity_table(sens, str(tmp_path / "a.xlsx"))
        p2 = ExcelExporter().export_sensitivity_table(
            {"rows": ["g1", "g2"], "cols": ["w1", "w2"], "matrix": [[1, 2], [3, 4]], "title": "Grid"},
            str(tmp_path / "b.xlsx"),
        )
        assert openpyxl.load_workbook(p1).worksheets[0]["B4"].value == pytest.approx(sens.iloc[0, 0])
        ws = openpyxl.load_workbook(p2)["Grid"]
        assert [c.value for c in ws[4]] == ["g1", 1, 2]

    def test_sensitivity_rejects_ragged_matrix(self, tmp_path):
        with pytest.raises(ValueError):
            ExcelExporter().export_sensitivity_table(
                {"rows": ["a"], "cols": ["x", "y"], "matrix": [[1]]}, str(tmp_path / "x.xlsx")
            )

    def test_add_worksheet_keeps_header(self):
        ws = ExcelExporter().add_worksheet("Data", {"a": [1, 2], "b": [3]})
        assert [c.value for c in ws[1]] == ["a", "b"]
        assert [c.value for c in ws[2]] == [1, 3]
        assert [c.value for c in ws[3]] == [2, None]

    def test_export_model_back_compat(self, three, tmp_path):
        _, result = three
        path = ExcelExporter(output_dir=tmp_path).export_model(result, "legacy")
        assert Path(path).name == "legacy.xlsx"


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #


class TestCSVExport:
    def test_statements_and_analysis_tables(self, lbo, tmp_path):
        model, result = lbo
        written = model.export_csv(tmp_path / "out", result)
        names = sorted(p.name for p in written.values())
        assert names == [
            "balance_sheet.csv",
            "cash_flow.csv",
            "debt_schedule.csv",
            "income_statement.csv",
            "lbo_returns.csv",
            "sources.csv",
            "uses.csv",
        ]
        is_ = pd.read_csv(tmp_path / "out" / "income_statement.csv", index_col=0)
        assert list(is_.columns) == result.labels
        assert is_.loc["Revenue"].tolist() == pytest.approx(result.income_statement.loc["Revenue"].tolist())
        with (tmp_path / "out" / "lbo_returns.csv").open() as f:
            rows = {r["metric"]: r["value"] for r in csv.DictReader(f)}
        assert float(rows["moic"]) == pytest.approx(model.calculate_returns(result)["moic"])

    def test_export_data(self, tmp_path):
        path = CSVExporter(tmp_path).export_data({"a": [1, 2], "b": ["x", "y"]}, "data")
        with open(path) as f:
            assert list(csv.reader(f)) == [["a", "b"], ["1", "x"], ["2", "y"]]

    def test_export_data_validation(self, tmp_path):
        with pytest.raises(ValueError, match="empty"):
            CSVExporter(tmp_path).export_data({}, "x")
        with pytest.raises(ValueError, match="same length"):
            CSVExporter(tmp_path).export_data({"a": [1], "b": [1, 2]}, "x")

    def test_export_table(self, tmp_path):
        path = CSVExporter(tmp_path / "new").export_table([[1, 2]], header=["a", "b"], filename="t.csv")
        with open(path) as f:
            assert list(csv.reader(f)) == [["a", "b"], ["1", "2"]]

    @pytest.mark.parametrize("name,slug", [("Debt Schedule", "debt_schedule"), ("A/B & C", "a_b_c"), ("***", "table")])
    def test_slugify(self, name, slug):
        assert slugify(name) == slug


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #


class TestJSONExport:
    def test_export_result(self, dcf, tmp_path):
        model, result = dcf
        path = model.export_json(tmp_path / "sub" / "m.json", result)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["periods"] == result.labels
        assert "DCF Valuation" in data["analysis"]
        assert not any(isinstance(v, float) and math.isnan(v) for v in data["analysis"]["DCF Valuation"].values())

    def test_export_tables(self, tmp_path):
        path = JSONExporter().export_tables({"t": pd.DataFrame({"v": [1.0]}, index=["r"])}, tmp_path / "t.json")
        assert json.loads(path.read_text(encoding="utf-8")) == {"t": {"r": {"v": 1.0}}}
