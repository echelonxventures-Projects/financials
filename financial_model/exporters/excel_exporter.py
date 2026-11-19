"""
Excel exporter: formatted statement sheets, assumptions, and per-model analysis
sheets (DCF valuation & sensitivity, LBO sources/uses, debt schedule, returns).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd
from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from financial_model.exporters.json_exporter import to_jsonable

_HEADER_FONT = Font(bold=True, size=11, color="FFFFFF")
_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
_TITLE_FONT = Font(bold=True, size=14)
_LABEL_FONT = Font(bold=True)
_TOTAL_FONT = Font(bold=True, size=11)
_THIN_BORDER = Border(bottom=Side(style="thin", color="B4C6E7"))
_THICK_BORDER = Border(bottom=Side(style="medium", color="4472C4"))

FMT_NUMBER = "#,##0"
FMT_DECIMAL = "#,##0.00"
FMT_PCT = "0.0%"
FMT_MULTIPLE = '0.00"x"'

_WIDTH_MIN = 12
_WIDTH_MAX = 40
_SHEET_NAME_MAX = 31  # Excel limit

# Analysis keys whose values are ratios / multiples rather than currency.
_PCT_HINTS = ("rate", "wacc", "pct", "growth", "irr", "margin", "ratio", "cost_of", "accretion", "%")
_MULTIPLE_HINTS = ("moic", "multiple", "beta")


def _number_format_for(name: str, value: Any) -> str:
    key = str(name).lower()
    if any(h in key for h in _MULTIPLE_HINTS):
        return FMT_MULTIPLE
    if any(h in key for h in _PCT_HINTS):
        return FMT_PCT
    if isinstance(value, float) and abs(value) < 100 and not float(value).is_integer():
        return FMT_DECIMAL
    return FMT_NUMBER


def _cell_value(value: Any) -> Any:
    """openpyxl-safe scalar: numpy -> python, NaN/inf -> None, containers -> str."""
    value = to_jsonable(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (list, dict)):
        return str(value)
    return value


class ExcelExporter:
    """Export model results to formatted Excel workbooks.

    Parameters
    ----------
    output_dir:
        Directory used for relative filenames.
    """

    def __init__(self, output_dir: str | Path = ".") -> None:
        self.output_dir = Path(output_dir)

    # ------------------------------------------------------------------ #
    # Model results
    # ------------------------------------------------------------------ #
    def export_result(
        self,
        result: Any,
        path: str | Path,
        model: Any = None,
        extras: dict[str, Any] | None = None,
    ) -> Path:
        """Write IS / BS / CF / Assumptions plus one sheet per analysis table.

        ``model`` (optional) supplies statement metadata so totals are bold and
        percentage lines are formatted as %. ``extras`` maps sheet name to a
        DataFrame (written as a table) or a dict of scalars (written as metric/value).
        """
        out = self._path(path)
        wb = openpyxl.Workbook()
        wb.remove(wb.active)

        statements = (
            ("Income Statement", result.income_statement, getattr(model, "income_statement", None)),
            ("Balance Sheet", result.balance_sheet, getattr(model, "balance_sheet", None)),
            ("Cash Flow", result.cash_flow, getattr(model, "cash_flow", None)),
        )
        for title, df, stmt in statements:
            self._write_statement(wb.create_sheet(title), title, df, result, stmt)

        self._write_assumptions(wb.create_sheet("Assumptions"), result)

        for name, table in (extras or {}).items():
            self.write_table(wb, name, table)

        for ws in wb.worksheets:
            self._auto_width(ws)
        wb.save(out)
        return out.resolve()

    def export_model(self, model_result: Any, filename: str = "model_output.xlsx") -> str:
        """Back-compatible wrapper around ``export_result`` without model metadata."""
        return str(self.export_result(model_result, filename))

    # ------------------------------------------------------------------ #
    # Generic tables
    # ------------------------------------------------------------------ #
    def write_table(self, wb: openpyxl.Workbook, name: str, table: Any) -> Worksheet:
        """Add ``table`` (DataFrame, dict of scalars, or list of records) as a new sheet."""
        ws = wb.create_sheet(self._sheet_name(wb, name))
        ws.cell(row=1, column=1, value=name).font = _TITLE_FONT
        if isinstance(table, dict):
            self.write_row(ws, 3, ["Metric", "Value"], header=True)
            row = 4
            for key, value in table.items():
                if isinstance(value, (dict, list)):
                    continue  # nested detail belongs in its own table
                ws.cell(row=row, column=1, value=str(key)).font = _LABEL_FONT
                cell = ws.cell(row=row, column=2, value=_cell_value(value))
                cell.number_format = _number_format_for(key, value)
                row += 1
            return ws

        df = table if isinstance(table, pd.DataFrame) else pd.DataFrame(table)
        index_name = df.index.name or ""
        self.write_row(ws, 3, [index_name, *[str(c) for c in df.columns]], header=True)
        for r, (idx, values) in enumerate(df.iterrows(), start=4):
            ws.cell(row=r, column=1, value=_cell_value(idx)).font = _LABEL_FONT
            for c, (col, value) in enumerate(values.items(), start=2):
                cell = ws.cell(row=r, column=c, value=_cell_value(value))
                cell.number_format = _number_format_for(col, value)
        return ws

    def export_tables(self, tables: dict[str, Any], path: str | Path) -> Path:
        """Workbook with one sheet per table (DataFrame or dict of scalars)."""
        out = self._path(path)
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for name, table in tables.items():
            self.write_table(wb, name, table)
        for ws in wb.worksheets:
            self._auto_width(ws)
        wb.save(out)
        return out.resolve()

    def export_statements(self, statements: dict[str, pd.DataFrame], filename: str = "statements.xlsx") -> str:
        """Write each DataFrame to its own sheet (index in column A); returns the absolute path."""
        return str(self.export_tables(statements, filename))

    def export_sensitivity_table(self, data: dict[str, Any] | pd.DataFrame, filename: str = "sensitivity.xlsx") -> str:
        """Write a sensitivity grid.

        Accepts a DataFrame (index = row labels, columns = column labels) or a dict with
        ``rows``, ``cols``, ``matrix`` and optional ``title``.
        """
        if isinstance(data, pd.DataFrame):
            df, title = data, "Sensitivity Analysis"
        else:
            rows, cols, matrix = data["rows"], data["cols"], data["matrix"]
            if len(matrix) != len(rows) or any(len(r) != len(cols) for r in matrix):
                raise ValueError("matrix must be len(rows) x len(cols)")
            df = pd.DataFrame(matrix, index=rows, columns=cols)
            title = data.get("title", "Sensitivity Analysis")
        out = self._path(filename)
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        self.write_table(wb, title, df)
        self._auto_width(wb.worksheets[0])
        wb.save(out)
        return str(out.resolve())

    def add_worksheet(
        self, name: str, data_dict: dict[str, list[Any]], workbook: openpyxl.Workbook | None = None
    ) -> Worksheet:
        """Create a sheet from ``{column: [values]}``: header in row 1, data from row 2."""
        if workbook is None:
            workbook = openpyxl.Workbook()
            workbook.remove(workbook.active)
        sheet = workbook.create_sheet(self._sheet_name(workbook, name))
        columns = list(data_dict)
        self.write_row(sheet, 1, columns, header=True)
        num_rows = max((len(v) for v in data_dict.values()), default=0)
        for r in range(num_rows):
            values = [data_dict[c][r] if r < len(data_dict[c]) else None for c in columns]
            self.write_row(sheet, r + 2, values)
        self._auto_width(sheet)
        return sheet

    def write_row(self, sheet: Worksheet, row: int, values: list[Any], header: bool = False) -> None:
        """Write one row (1-based) of values, optionally styled as a header."""
        for col_idx, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=col_idx, value=_cell_value(value))
            if header:
                self._apply_header_style(cell)
            elif isinstance(cell.value, float):
                cell.number_format = FMT_DECIMAL

    # ------------------------------------------------------------------ #
    # Sheets
    # ------------------------------------------------------------------ #
    def _write_statement(self, ws: Worksheet, title: str, df: pd.DataFrame, result: Any, stmt: Any) -> None:
        ws.cell(row=1, column=1, value=title).font = _TITLE_FONT
        labels = [p.label for p in result.periods] if len(df.columns) == len(result.periods) else list(df.columns)
        self.write_row(ws, 2, ["Line Item", *[str(x) for x in labels]], header=True)
        ws.freeze_panes = "B3"

        row = 3
        for line_name, values in df.iterrows():
            item = stmt.get_line_item(str(line_name)) if stmt is not None else None
            ws.cell(row=row, column=1, value=str(line_name))
            fmt = FMT_PCT if item is not None and getattr(item, "unit", "") == "%" else FMT_NUMBER
            for c, value in enumerate(values, start=2):
                v = _cell_value(value)
                cell = ws.cell(row=row, column=c, value=0.0 if v is None else v)
                cell.number_format = fmt
                cell.border = _THIN_BORDER
            if item is not None and getattr(item, "is_total", False):
                for c in range(1, len(labels) + 2):
                    ws.cell(row=row, column=c).font = _TOTAL_FONT
                    ws.cell(row=row, column=c).border = _THICK_BORDER
            row += 1

    def _write_assumptions(self, ws: Worksheet, result: Any) -> None:
        ws.cell(row=1, column=1, value="Assumptions").font = _TITLE_FONT
        labels = [p.label for p in result.periods]
        self.write_row(ws, 2, ["Driver", "Unit", "Description", *labels], header=True)
        ws.freeze_panes = "D3"

        drivers = getattr(result, "drivers", None) or {}
        if not drivers and getattr(result, "assumptions", None) is not None:
            drivers = result.assumptions.drivers
        for r, (name, driver) in enumerate(drivers.items(), start=3):
            ws.cell(row=r, column=1, value=name)
            ws.cell(row=r, column=2, value=driver.unit)
            ws.cell(row=r, column=3, value=driver.description)
            fmt = FMT_PCT if driver.unit == "%" else FMT_DECIMAL
            for i in range(len(labels)):
                cell = ws.cell(row=r, column=4 + i, value=_cell_value(driver.get_value(i)))
                cell.number_format = fmt

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _path(self, path: str | Path) -> Path:
        p = Path(path)
        if p.suffix.lower() != ".xlsx":
            p = p.with_name(p.name + ".xlsx")
        if not p.is_absolute():
            p = self.output_dir / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def _sheet_name(wb: openpyxl.Workbook, name: str) -> str:
        base = "".join(ch for ch in str(name) if ch not in "[]:*?/\\")[:_SHEET_NAME_MAX] or "Sheet"
        candidate, n = base, 2
        while candidate in wb.sheetnames:
            suffix = f" ({n})"
            candidate = base[: _SHEET_NAME_MAX - len(suffix)] + suffix
            n += 1
        return candidate

    @staticmethod
    def _apply_header_style(cell: Cell) -> None:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _HEADER_ALIGNMENT

    @staticmethod
    def _auto_width(sheet: Worksheet) -> None:
        for column_cells in sheet.columns:
            longest = max((len(str(c.value)) for c in column_cells if c.value is not None), default=0)
            letter = get_column_letter(column_cells[0].column)
            sheet.column_dimensions[letter].width = min(max(longest + 2, _WIDTH_MIN), _WIDTH_MAX)

    @staticmethod
    def _ensure_xlsx(filename: str) -> str:
        return filename if filename.lower().endswith(".xlsx") else f"{filename}.xlsx"
