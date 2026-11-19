"""
Make the suite runnable on any install profile.

`pip install .` (core only) runs every engine, model, export and CLI test; modules that
need an optional extra are skipped (not failed) when that extra is not installed. With
`pip install -e ".[dev]"` nothing is skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

from financial_model.utils.optional import is_available

# extra package -> patterns that mean a test module depends on it
_NEEDS = {
    "fastapi": (r"\bfastapi\b", r"\bTestClient\b", r"financial_model\.api\b"),
    "plotly": (r"\bplotly\b", r"financial_model\.visualization\b"),
    "yfinance": (r"^\s*import yfinance", r"loader\.yf\.", r"\byfinance\.Ticker\b"),
}
_MISSING = [pkg for pkg in _NEEDS if not is_available(pkg)]


def pytest_ignore_collect(collection_path: Path, config) -> bool | None:  # noqa: ARG001
    if not _MISSING or collection_path.suffix != ".py" or not collection_path.name.startswith("test_"):
        return None
    text = collection_path.read_text(encoding="utf-8")
    for pkg in _MISSING:
        if any(re.search(p, text, re.MULTILINE) for p in _NEEDS[pkg]):
            return True
    return None


def pytest_report_header(config) -> str:  # noqa: ARG001
    if _MISSING:
        return f"optional packages not installed (their tests are skipped): {', '.join(_MISSING)}"
    return "all optional extras installed"
