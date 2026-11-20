"""
Data providers: where historical statements come from.

Built in:

- ``file``     your own statements, offline: an Excel workbook, a directory of CSV/XLSX/JSON
               files, or a JSON file (including this toolkit's own ``run -o model.json`` output)
- ``yfinance`` Yahoo Finance (optional extra: ``pip install 'financial-model[yfinance]'``)

Add more with ``register_provider()`` or the ``financial_model.data_providers`` entry point.
Every provider returns the same shape, which ``derive_drivers_from_financials`` turns into
model drivers::

    {"income_statement": DataFrame, "balance_sheet": DataFrame, "cash_flow": DataFrame,
     "key_stats": dict}   # rows = line items, columns = periods

``fetch_statements`` wraps every provider with source validation, rate limiting and a
circuit breaker (remote providers only) and uniform ``DataFetchError`` reporting.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import pandas as pd

from financial_model.utils.error_handler import DataFetchError
from financial_model.utils.optional import MissingDependencyError
from financial_model.utils.plugins import PROVIDER_GROUP, discover
from financial_model.utils.rate_limiter import check_rate_limit, record_fetch_failure, record_fetch_success
from financial_model.utils.validation import ValidationError

STATEMENTS = ("income_statement", "balance_sheet", "cash_flow")


class DataProvider(ABC):
    """Base class for statement sources."""

    name: str = ""
    description: str = ""
    #: Makes network calls: rate limited and guarded by the circuit breaker.
    remote: bool = True
    #: May REST API clients select it? Providers that read server files must say no.
    allow_in_api: bool = True

    def validate_source(self, source: str) -> str:
        """Normalise / validate the source identifier; raise ``ValidationError`` if malformed."""
        if not source or not str(source).strip():
            raise ValidationError("source", "must not be empty", source)
        return str(source).strip()

    @abstractmethod
    def fetch(self, source: str, years: int) -> dict[str, Any]:
        """Return the three statements (+ ``key_stats``) for ``source``, newest ``years`` periods."""


# --------------------------------------------------------------------------- #
# yfinance
# --------------------------------------------------------------------------- #
class YFinanceProvider(DataProvider):
    name = "yfinance"
    description = "Yahoo Finance via the yfinance package (source = ticker, e.g. SNOW, BRK-B, RELIANCE.NS)"
    remote = True
    allow_in_api = True

    def validate_source(self, source: str) -> str:
        from financial_model.data.loader import normalize_ticker

        return normalize_ticker(source)

    def fetch(self, source: str, years: int) -> dict[str, Any]:
        from financial_model.data import loader

        dl = loader.DataLoader(source)
        return {
            "income_statement": dl.get_income_statement(years),
            "balance_sheet": dl.get_balance_sheet(years),
            "cash_flow": dl.get_cash_flow(years),
            "key_stats": dl.get_key_stats(),
        }


# --------------------------------------------------------------------------- #
# Local files
# --------------------------------------------------------------------------- #
def _statement_aliases() -> dict[str, set[str]]:
    from financial_model.data.loader import load_mappings

    return {k: set(v) for k, v in (load_mappings().get("statement_aliases") or {}).items()}


_ALIASES = _statement_aliases()
_FILE_EXTS = (".csv", ".xlsx", ".xls", ".json")
_YEAR = re.compile(r"(19|20)\d{2}")
_QUARTER = re.compile(r"Q([1-4])", re.IGNORECASE)


def _slug(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(name).lower().replace("&", "_and_")).strip("_")


def _which(name: str) -> str | None:
    slug = _slug(name)
    for key, names in _ALIASES.items():
        if slug in names or slug.removesuffix("_statement") in names:
            return key
    return None


def _period_key(label: Any) -> tuple[int, int] | None:
    """(year, quarter) for labels like 2023, FY2023, Q1 FY2024, 2023-12-31; None otherwise.

    Only years 1900-2099 count, so plain numbers (a value of 1000) are not mistaken for periods.
    """
    if isinstance(label, (pd.Timestamp,)) or hasattr(label, "year") and hasattr(label, "month"):
        ts = pd.Timestamp(label)
        return (ts.year, ts.quarter)
    if isinstance(label, (int, float)) and not isinstance(label, bool):
        return (int(label), 4) if float(label).is_integer() and 1900 <= label <= 2099 else None
    text = str(label).strip()
    year = _YEAR.search(text)
    if not year:
        return None
    quarter = _QUARTER.search(text)
    if quarter:
        return (int(year.group(0)), int(quarter.group(1)))
    if re.fullmatch(r"(FY|CY)?\s*(19|20)\d{2}", text, re.IGNORECASE):
        return (int(year.group(0)), 4)
    try:  # dates: 2023-12-31, 31/03/2024, Dec 2023
        ts = pd.Timestamp(text)
    except (ValueError, TypeError):
        return (int(year.group(0)), 4) if re.search(r"\b(FY|CY)", text, re.IGNORECASE) else None
    return (ts.year, ts.quarter)


def _to_number(value: Any) -> float:
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("$", "")
        if text.startswith("(") and text.endswith(")"):
            text = "-" + text[1:-1]
        value = text or None
    out = pd.to_numeric(value, errors="coerce")
    return float(out) if pd.notna(out) else float("nan")


def normalize_statement_frame(df: pd.DataFrame, statement: str, years: int | None = None) -> pd.DataFrame:
    """Coerce a user-supplied statement into provider shape.

    Accepts either orientation (lines as rows or as columns), period labels such as
    ``2023``, ``FY2023``, ``Q1 FY2024`` or dates, accounting-formatted numbers (``1,200``,
    ``(300)``), and provider line names (mapped to the model's). Periods are ordered
    oldest -> newest and labelled with period-end timestamps; ``years`` keeps the newest N.
    """
    from financial_model.data.loader import standardize_statement

    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    # Periods belong in the columns: transpose when the index looks like periods instead.
    col_periods = sum(_period_key(c) is not None for c in out.columns)
    row_periods = sum(_period_key(i) is not None for i in out.index)
    if row_periods > col_periods:
        out = out.T
    keyed = [(c, _period_key(c)) for c in out.columns]
    keep = [(c, k) for c, k in keyed if k is not None]
    if not keep:
        raise ValidationError(statement, "no period columns found (expected labels like 2023, FY2023 or a date)")
    keep.sort(key=lambda ck: ck[1])
    out = out[[c for c, _ in keep]]
    out.columns = [pd.Period(year=y, quarter=q, freq="Q").end_time.normalize() for _, (y, q) in keep]
    out.index = [str(i).strip() for i in out.index]
    out = out.map(_to_number).dropna(how="all")
    out = standardize_statement(out, statement)
    if years:
        out = out.iloc[:, -years:]
    # Providers return newest first (like yfinance); derive_drivers re-sorts chronologically.
    return out.iloc[:, ::-1]


def statements_from_mapping(payload: dict[str, Any], years: int | None = None) -> dict[str, Any]:
    """Statements from ``{statement: {period: {line: value}}}`` (or ``{line: {period: value}}``).

    This is the layout of ``financial-model run -o model.json`` and ``POST /calculate``.
    """
    data: dict[str, Any] = {"key_stats": {}}
    for raw_key, value in payload.items():
        key = _which(raw_key)
        if key == "key_stats":
            data["key_stats"] = dict(value or {})
        elif key is not None and isinstance(value, dict):
            data[key] = normalize_statement_frame(pd.DataFrame(value), key, years)
    for key in STATEMENTS:
        data.setdefault(key, pd.DataFrame())
    return data


def _with_header_row(raw: pd.DataFrame) -> pd.DataFrame:
    """Use the first row that contains period labels as the header (skips titles / notes above it).

    Handles periods across the top or down the first column (header = the row above the
    first period). Falls back to the first row (e.g. a key-stats sheet).
    """
    raw = raw.dropna(how="all").dropna(axis=1, how="all")
    if raw.empty:
        return pd.DataFrame()
    pos = 0
    head = raw.head(20)
    for i, (_, row) in enumerate(head.iterrows()):
        if any(_period_key(v) is not None for v in list(row)[1:] if pd.notna(v)):
            pos = i  # periods across the top: this row is the header
            break
    else:
        # Periods down the first column (transposed): the header is the row above the first one.
        first_col = list(head.iloc[:, 0])
        for i, v in enumerate(first_col):
            if pd.notna(v) and _period_key(v) is not None:
                pos = max(i - 1, 0)
                break
    header = [str(v).strip() if pd.notna(v) else f"col{i}" for i, v in enumerate(raw.iloc[pos])]
    body = raw.iloc[pos + 1 :].copy()
    body.columns = header
    body = body.set_index(header[0])
    body.index.name = None
    return body[body.index.notna()]


def _read_table(path: Path) -> Any:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _with_header_row(pd.read_csv(path, header=None, dtype=object))
    if suffix in (".xlsx", ".xls"):
        return _with_header_row(pd.read_excel(path, header=None, dtype=object))
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _key_stats(table: Any) -> dict[str, Any]:
    if isinstance(table, dict):
        return table
    if isinstance(table, pd.DataFrame) and not table.empty:
        out: dict[str, Any] = {}
        for k, v in table.iloc[:, 0].items():
            num = _to_number(v)
            out[str(k)] = v if pd.isna(num) else (int(num) if float(num).is_integer() else num)
        return out
    return {}


class FileProvider(DataProvider):
    name = "file"
    description = (
        "Your own statements: an .xlsx workbook (sheets named Income Statement / Balance Sheet / "
        "Cash Flow), a .json file, or a directory of income_statement / balance_sheet / cash_flow "
        "files (.csv, .xlsx or .json)"
    )
    remote = False
    allow_in_api = False  # would read files on the server

    def validate_source(self, source: str) -> str:
        path = Path(super().validate_source(source)).expanduser()
        if not path.exists():
            raise ValidationError("source", "file or directory not found", str(path))
        return str(path)

    def fetch(self, source: str, years: int) -> dict[str, Any]:
        path = Path(source)
        if path.is_dir():
            return self._from_directory(path, years)
        suffix = path.suffix.lower()
        if suffix in (".xlsx", ".xls"):
            sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
            return self._assemble({name: _with_header_row(frame) for name, frame in sheets.items()}, years, str(path))
        if suffix == ".json":
            with path.open(encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                raise ValidationError("source", "JSON must be an object keyed by statement", str(path))
            return statements_from_mapping(payload, years)
        raise ValidationError("source", "expected an .xlsx/.json file or a directory of statement files", str(path))

    def _from_directory(self, path: Path, years: int) -> dict[str, Any]:
        tables = {f.stem: _read_table(f) for f in sorted(path.iterdir()) if f.suffix.lower() in _FILE_EXTS}
        return self._assemble(tables, years, str(path))

    @staticmethod
    def _assemble(tables: dict[str, Any], years: int, where: str) -> dict[str, Any]:
        data: dict[str, Any] = {"key_stats": {}}
        for name, table in tables.items():
            key = _which(name)
            if key == "key_stats":
                data["key_stats"] = _key_stats(table)
            elif key is not None and key not in data:
                frame = pd.DataFrame(table) if isinstance(table, dict) else table
                data[key] = normalize_statement_frame(frame, key, years)
        if "income_statement" not in data:
            raise ValidationError(
                "source", "no income statement found (name it income_statement / Income Statement / P&L)", where
            )
        for key in STATEMENTS:
            data.setdefault(key, pd.DataFrame())
        return data


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
_PROVIDERS: dict[str, DataProvider] = {}
_plugins_loaded = False


def register_provider(provider: DataProvider | type[DataProvider], name: str | None = None) -> DataProvider:
    """Register a provider instance (or class) under ``name`` (default: its ``name``)."""
    instance = provider() if isinstance(provider, type) else provider
    if not isinstance(instance, DataProvider):
        raise TypeError("providers must subclass DataProvider")
    key = (name or instance.name).strip().lower()
    if not key:
        raise ValueError("provider needs a name")
    _PROVIDERS[key] = instance
    return instance


def _load_plugins() -> None:
    global _plugins_loaded
    if _plugins_loaded:
        return
    _plugins_loaded = True
    for name, obj in discover(PROVIDER_GROUP).items():
        try:
            register_provider(obj, name)
        except (TypeError, ValueError):
            continue


def available_providers() -> dict[str, DataProvider]:
    _load_plugins()
    return dict(_PROVIDERS)


def get_provider(name: str) -> DataProvider:
    providers = available_providers()
    key = (name or "").strip().lower()
    if key not in providers:
        raise ValueError(f"Unknown data provider '{name}'. Available: {', '.join(providers)}")
    return providers[key]


def default_provider_name() -> str:
    from financial_model.config_loader import get_config

    return str(get_config().get("data", {}).get("default_provider") or "yfinance")


def resolve_provider_name(source: str, provider: str | None, for_api: bool = False) -> str:
    """Explicit provider > (CLI only) an existing local path means ``file`` > settings default."""
    if provider:
        return provider
    if not for_api and source and Path(str(source)).expanduser().exists():
        return "file"
    return default_provider_name()


def fetch_statements(
    source: str, years: int = 5, provider: str | None = None, *, for_api: bool = False
) -> dict[str, Any]:
    """Load statements from any provider with uniform validation, rate limiting and errors.

    Raises:
        ValidationError: malformed source (bad ticker, missing file, unreadable layout).
        MissingDependencyError: the provider's optional package is not installed.
        RateLimitExceeded / CircuitBreakerOpen: remote provider throttled.
        DataFetchError: the provider failed or returned no income statement.
        ValueError: unknown provider, provider not allowed over the API, or years < 1.
    """
    if years < 1:
        raise ValueError("years must be >= 1")
    name = resolve_provider_name(source, provider, for_api)
    prov = get_provider(name)
    if for_api and not prov.allow_in_api:
        raise ValueError(f"data provider '{name}' is not available over the API")

    src = prov.validate_source(source)
    if prov.remote:
        check_rate_limit(name)
    try:
        data = prov.fetch(src, years)
    except (ValidationError, MissingDependencyError, DataFetchError):
        if prov.remote:
            record_fetch_failure(name)
        raise
    except Exception as exc:
        if prov.remote:
            record_fetch_failure(name)
        raise DataFetchError(f"{src}: {exc}", service=name) from exc

    ist = data.get("income_statement")
    if not isinstance(ist, pd.DataFrame) or ist.empty:
        if prov.remote:
            record_fetch_failure(name)
        raise DataFetchError(f"no financial statements found for {src}", service=name)
    for key in STATEMENTS:
        data.setdefault(key, pd.DataFrame())
    data.setdefault("key_stats", {})
    data.setdefault("provider", name)
    if prov.remote:
        record_fetch_success(name)
    return data


register_provider(FileProvider())
register_provider(YFinanceProvider())
