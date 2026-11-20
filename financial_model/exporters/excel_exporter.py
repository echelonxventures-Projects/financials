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

from financial_model.config_loader import get_config
from financial_model.exporters.json_exporter import to_jsonable


def excel_style() -> dict[str, Any]:
    """The ``presentation.excel`` settings (financial_model/settings.yaml, overridable)."""
    return dict((get_config().get("presentation") or {}).get("excel") or {})


def _color(value: Any) -> str | None:
    """Hex colour as text (YAML reads 112233 as a number); a leading '#' is optional."""
    return None if value is None else str(value).lstrip("#").upper()


def _font(spec: dict[str, Any]) -> Font:
    return Font(bold=bool(spec.get("bold")), size=spec.get("size"), color=_color(spec.get("color")))


def _border(style: Any, color: Any) -> Border:
    return Border(bottom=Side(style=style, color=_color(color))) if style else Border()


def _number_format_for(name: str, value: Any, style: dict[str, Any] | None = None) -> str:
    style = style if style is not None else excel_style()
    fmts = style.get("number_formats") or {}
    key = str(name).lower()
    if any(str(h).lower() in key for h in style.get("multiple_hints") or []):
        return str(fmts.get("multiple", "General"))
    if any(str(h).lower() in key for h in style.get("percent_hints") or []):
        return str(fmts.get("percent", "General"))
    limit = style.get("decimal_below")
    if isinstance(value, float) and limit is not None and abs(value) < limit and not float(value).is_integer():
        return str(fmts.get("decimal", "General"))
    return str(fmts.get("number", "General"))


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

    def __init__(self, output_dir: str | Path = ".", style: dict[str, Any] | None = None) -> None:
        self.output_dir = Path(output_dir)
        self.style = style if style is not None else excel_style()
        st = self.style
        header = st.get("header") or {}
        total = st.get("total") or {}
        row_border = st.get("row_border") or {}
        fmts = st.get("number_formats") or {}
        self.title_font = _font(st.get("title") or {})
        self.label_font = _font(st.get("label") or {})
        self.total_font = _font(total)
        self.header_font = _font(header)
        self.header_fill = (
            PatternFill(start_color=_color(header["fill"]), end_color=_color(header["fill"]), fill_type="solid")
            if header.get("fill")
            else PatternFill()
        )
        self.header_alignment = Alignment(
            horizontal=header.get("align"), vertical="center", wrap_text=bool(header.get("wrap"))
        )
        self.row_border = _border(row_border.get("style"), row_border.get("color"))
        self.total_border = _border(total.get("border_style"), total.get("border_color"))
        self.fmt_number = str(fmts.get("number", "General"))
        self.fmt_decimal = str(fmts.get("decimal", "General"))
        self.fmt_pct = str(fmts.get("percent", "General"))
        self.percent_units = set(st.get("percent_units") or [])
        self.sheets = {k: str(v) for k, v in (st.get("sheets") or {}).items()}
        self.columns = {k: str(v) for k, v in (st.get("columns") or {}).items()}
        self.freeze = dict(st.get("freeze") or {})

    def _fmt(self, name: Any, value: Any) -> str:
        return _number_format_for(name, value, self.style)

    def _sheet(self, key: str) -> str:
        return self.sheets.get(key, key.replace("_", " ").title())

    def _col(self, key: str) -> str:
        return self.columns.get(key, key.replace("_", " ").title())

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

        for key in ("income_statement", "balance_sheet", "cash_flow"):
            title = self._sheet(key)
            df, stmt = getattr(result, key), getattr(model, key, None)
            self._write_statement(wb.create_sheet(self._sheet_name(wb, title)), title, df, result, stmt)

        self._write_assumptions(wb.create_sheet(self._sheet_name(wb, self._sheet("assumptions"))), result)

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
        ws.cell(row=1, column=1, value=name).font = self.title_font
        if isinstance(table, dict):
            self.write_row(ws, 3, [self._col("metric"), self._col("value")], header=True)
            row = 4
            for key, value in table.items():
                if isinstance(value, (dict, list)):
                    continue  # nested detail belongs in its own table
                ws.cell(row=row, column=1, value=str(key)).font = self.label_font
                cell = ws.cell(row=row, column=2, value=_cell_value(value))
                cell.number_format = self._fmt(key, value)
                row += 1
            return ws

        df = table if isinstance(table, pd.DataFrame) else pd.DataFrame(table)
        index_name = df.index.name or ""
        self.write_row(ws, 3, [index_name, *[str(c) for c in df.columns]], header=True)
        for r, (idx, values) in enumerate(df.iterrows(), start=4):
            ws.cell(row=r, column=1, value=_cell_value(idx)).font = self.label_font
            for c, (col, value) in enumerate(values.items(), start=2):
                cell = ws.cell(row=r, column=c, value=_cell_value(value))
                cell.number_format = self._fmt(col, value)
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
                cell.number_format = self.fmt_decimal

    # ------------------------------------------------------------------ #
    # Sheets
    # ------------------------------------------------------------------ #
    def _write_statement(self, ws: Worksheet, title: str, df: pd.DataFrame, result: Any, stmt: Any) -> None:
        ws.cell(row=1, column=1, value=title).font = self.title_font
        labels = [p.label for p in result.periods] if len(df.columns) == len(result.periods) else list(df.columns)
        self.write_row(ws, 2, [self._col("line_item"), *[str(x) for x in labels]], header=True)
        ws.freeze_panes = self.freeze.get("statement")

        row = 3
        for line_name, values in df.iterrows():
            item = stmt.get_line_item(str(line_name)) if stmt is not None else None
            ws.cell(row=row, column=1, value=str(line_name))
            pct = item is not None and getattr(item, "unit", "") in self.percent_units
            fmt = self.fmt_pct if pct else self.fmt_number
            for c, value in enumerate(values, start=2):
                v = _cell_value(value)
                cell = ws.cell(row=row, column=c, value=0.0 if v is None else v)
                cell.number_format = fmt
                cell.border = self.row_border
            if item is not None and getattr(item, "is_total", False):
                for c in range(1, len(labels) + 2):
                    ws.cell(row=row, column=c).font = self.total_font
                    ws.cell(row=row, column=c).border = self.total_border
            row += 1

    def _write_assumptions(self, ws: Worksheet, result: Any) -> None:
        ws.cell(row=1, column=1, value=self._sheet("assumptions")).font = self.title_font
        labels = [p.label for p in result.periods]
        header = [self._col("driver"), self._col("unit"), self._col("description"), *labels]
        self.write_row(ws, 2, header, header=True)
        ws.freeze_panes = self.freeze.get("assumptions")

        drivers = getattr(result, "drivers", None) or {}
        if not drivers and getattr(result, "assumptions", None) is not None:
            drivers = result.assumptions.drivers
        for r, (name, driver) in enumerate(drivers.items(), start=3):
            ws.cell(row=r, column=1, value=name)
            ws.cell(row=r, column=2, value=driver.unit)
            ws.cell(row=r, column=3, value=driver.description)
            fmt = self.fmt_pct if driver.unit in self.percent_units else self.fmt_decimal
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

    def _sheet_name(self, wb: openpyxl.Workbook, name: str) -> str:
        limit = int(self.style.get("sheet_name_max") or 31)
        base = "".join(ch for ch in str(name) if ch not in "[]:*?/\\")[:limit] or "Sheet"
        candidate, n = base, 2
        while candidate in wb.sheetnames:
            suffix = f" ({n})"
            candidate = base[: limit - len(suffix)] + suffix
            n += 1
        return candidate

    def _apply_header_style(self, cell: Cell) -> None:
        cell.font = self.header_font
        cell.fill = self.header_fill
        cell.alignment = self.header_alignment

    def _auto_width(self, sheet: Worksheet) -> None:
        width = self.style.get("column_width") or {}
        lo, hi, pad = width.get("min", 0), width.get("max", 255), width.get("padding", 0)
        for column_cells in sheet.columns:
            longest = max((len(str(c.value)) for c in column_cells if c.value is not None), default=0)
            letter = get_column_letter(column_cells[0].column)
            sheet.column_dimensions[letter].width = min(max(longest + pad, lo), hi)

    @staticmethod
    def _ensure_xlsx(filename: str) -> str:
        return filename if filename.lower().endswith(".xlsx") else f"{filename}.xlsx"
