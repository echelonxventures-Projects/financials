"""
JSON serialisation for model results and analysis tables.

``to_jsonable`` is the single conversion used by the CLI, the REST API and the
CSV/Excel exporters' metadata: pandas/numpy containers become plain python, NaN
becomes ``None`` and non-string keys (periods, timestamps) become strings.
"""

from __future__ import annotations

import contextlib
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


def to_jsonable(obj: Any) -> Any:
    """Convert pandas/numpy containers to JSON-safe python values."""
    if isinstance(obj, dict):
        return {(k if isinstance(k, str) else str(k)): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, pd.DataFrame):
        return to_jsonable(obj.to_dict())
    if isinstance(obj, pd.Series):
        return to_jsonable(obj.to_dict())
    if isinstance(obj, pd.Timestamp):
        return str(obj)
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        with contextlib.suppress(ValueError, TypeError, AttributeError):
            obj = obj.item()
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if obj is pd.NA or obj is pd.NaT:
        return None
    return obj


def labelled(result: Any, df: Any) -> Any:
    """Statement with period labels as columns when the result supports it."""
    if hasattr(result, "labelled") and isinstance(df, pd.DataFrame):
        return result.labelled(df)
    return df


def statement_to_dict(result: Any, df: Any) -> Any:
    """Statement -> ``{period_label: {line: value}}`` (JSON-safe)."""
    out = labelled(result, df)
    return to_jsonable(out.to_dict() if hasattr(out, "to_dict") else out)


def result_to_dict(result: Any, extras: dict[str, Any] | None = None) -> dict[str, Any]:
    """Full JSON payload for a ``ModelResult`` (plus optional analysis tables)."""
    periods = getattr(result, "periods", [])
    payload: dict[str, Any] = {
        "income_statement": statement_to_dict(result, result.income_statement),
        "balance_sheet": statement_to_dict(result, result.balance_sheet),
        "cash_flow": statement_to_dict(result, result.cash_flow),
        "periods": [getattr(p, "label", str(p)) for p in periods],
        "metadata": to_jsonable(getattr(result, "metadata", {}) or {}),
    }
    bs = getattr(result, "bs_balance_result", None)
    cf = getattr(result, "cf_balance_result", None)
    if bs is not None and cf is not None:
        payload.update(
            {
                "bs_balanced": bool(getattr(bs, "balanced", False)),
                "bs_max_diff": to_jsonable(getattr(bs, "max_diff", None)),
                "cf_balanced": bool(getattr(cf, "balanced", False)),
                "cf_max_diff": to_jsonable(getattr(cf, "max_diff", None)),
                "errors": [*getattr(bs, "errors", []), *getattr(cf, "errors", [])],
            }
        )
    if extras:
        payload["analysis"] = {name: table_to_jsonable(value) for name, value in extras.items()}
    return payload


def table_to_jsonable(value: Any) -> Any:
    """Analysis table -> JSON: DataFrames become records keyed by their index."""
    if isinstance(value, pd.DataFrame):
        return to_jsonable(value.to_dict(orient="index"))
    return to_jsonable(value)


class JSONExporter:
    """Write a model result (and analysis tables) to a JSON file."""

    def __init__(self, indent: int = 2) -> None:
        self.indent = indent

    def dumps_result(self, result: Any, extras: dict[str, Any] | None = None) -> str:
        """The JSON text `export_result` writes (used for stdout output)."""
        return json.dumps(result_to_dict(result, extras), indent=self.indent, default=str)

    def dumps_tables(self, tables: dict[str, Any]) -> str:
        return json.dumps({k: table_to_jsonable(v) for k, v in tables.items()}, indent=self.indent, default=str)

    def export_result(self, result: Any, path: str | Path, extras: dict[str, Any] | None = None) -> Path:
        return self._write(path, self.dumps_result(result, extras))

    def export_tables(self, tables: dict[str, Any], path: str | Path) -> Path:
        return self._write(path, self.dumps_tables(tables))

    @staticmethod
    def _write(path: str | Path, text: str) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        return out.resolve()
