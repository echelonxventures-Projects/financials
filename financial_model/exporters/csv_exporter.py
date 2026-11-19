"""
CSV exporter for model results, analysis tables and ad-hoc data.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

import pandas as pd

from financial_model.exporters.json_exporter import labelled, to_jsonable

STATEMENT_FILES = {
    "income_statement": "income_statement.csv",
    "balance_sheet": "balance_sheet.csv",
    "cash_flow": "cash_flow.csv",
}


def slugify(name: str) -> str:
    """'Debt Schedule' -> 'debt_schedule' (safe file / sheet stem)."""
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    return slug or "table"


class CSVExporter:
    """Export financial model data to CSV files.

    Parameters
    ----------
    output_dir:
        Directory used by ``export_data`` / ``export_table`` for relative filenames.
    """

    def __init__(self, output_dir: str | Path = ".") -> None:
        self.output_dir = Path(output_dir)

    # ------------------------------------------------------------------ #
    # Model results
    # ------------------------------------------------------------------ #
    def export_result(
        self, result: Any, output_dir: str | Path | None = None, extras: dict[str, Any] | None = None
    ) -> dict[str, Path]:
        """Write one CSV per statement (period labels as columns) plus one per analysis table.

        Returns ``{name: path}`` for every file written.
        """
        out = Path(output_dir) if output_dir is not None else self.output_dir
        out.mkdir(parents=True, exist_ok=True)
        written: dict[str, Path] = {}
        for attr, filename in STATEMENT_FILES.items():
            df = labelled(result, getattr(result, attr))
            path = out / filename
            df.to_csv(path)
            written[attr] = path
        for name, table in (extras or {}).items():
            path = out / f"{slugify(name)}.csv"
            self._write_table(table, path)
            written[name] = path
        return written

    def export_tables(self, tables: dict[str, Any], output_dir: str | Path | None = None) -> dict[str, Path]:
        """One CSV per table (``{name}.csv``, slugified) in ``output_dir``."""
        out = Path(output_dir) if output_dir is not None else self.output_dir
        out.mkdir(parents=True, exist_ok=True)
        written: dict[str, Path] = {}
        for name, table in tables.items():
            path = out / f"{slugify(name)}.csv"
            self._write_table(table, path)
            written[name] = path
        return written

    @staticmethod
    def _write_table(table: Any, path: Path) -> None:
        if isinstance(table, pd.DataFrame):
            table.to_csv(path)
            return
        if isinstance(table, dict):
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["metric", "value"])
                for key, value in table.items():
                    writer.writerow([key, to_jsonable(value)])
            return
        pd.DataFrame(table).to_csv(path, index=False)

    # ------------------------------------------------------------------ #
    # Generic data
    # ------------------------------------------------------------------ #
    def export_data(self, data_dict: dict[str, list[Any]], filename: str) -> str:
        """Export a column-oriented dict (``{column: [values]}``) to CSV; returns the absolute path.

        Raises ``ValueError`` when the dict is empty or its columns differ in length.
        """
        if not data_dict:
            raise ValueError("data_dict must not be empty")
        lengths = {len(v) for v in data_dict.values()}
        if len(lengths) > 1:
            raise ValueError("All value lists in data_dict must have the same length")

        filepath = self._path(filename)
        columns = list(data_dict)
        num_rows = len(next(iter(data_dict.values())))
        with filepath.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, quoting=csv.QUOTE_ALL)
            writer.writerow(columns)
            for row_idx in range(num_rows):
                writer.writerow([data_dict[col][row_idx] for col in columns])
        return str(filepath.resolve())

    def export_table(self, rows: list[list[Any]], header: list[str] | None = None, filename: str = "output.csv") -> str:
        """Export a list of rows (optionally with a header) to CSV; returns the absolute path."""
        filepath = self._path(filename)
        with filepath.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, quoting=csv.QUOTE_ALL)
            if header is not None:
                writer.writerow(header)
            writer.writerows(rows)
        return str(filepath.resolve())

    def _path(self, filename: str) -> Path:
        name = filename if filename.lower().endswith(".csv") else f"{filename}.csv"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir / name

    @staticmethod
    def _ensure_csv_ext(filename: str) -> str:
        return filename if filename.lower().endswith(".csv") else f"{filename}.csv"
