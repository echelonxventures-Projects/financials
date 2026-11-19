"""
Optional-dependency handling.

The core toolkit (engine, models, YAML configs, Excel/CSV/JSON export, CLI) needs only
pandas, numpy, openpyxl, pydantic, pyyaml and typer. Everything else is an extra:

    api       fastapi + uvicorn         REST API / `financial-model serve`
    charts    plotly                    `financial-model chart`, /charts
    yfinance  yfinance                  the `yfinance` data provider
    all       every extra above
"""

from __future__ import annotations

import importlib
from types import ModuleType

EXTRAS: dict[str, str] = {
    "fastapi": "api",
    "uvicorn": "api",
    "starlette": "api",
    "plotly": "charts",
    "yfinance": "yfinance",
}


class MissingDependencyError(ImportError):
    """An optional dependency is not installed; the message says which extra provides it."""

    def __init__(self, module: str, feature: str, extra: str | None = None):
        self.module = module
        self.extra = extra or EXTRAS.get(module.split(".")[0], "all")
        super().__init__(
            f"{feature} needs the optional package '{module.split('.')[0]}'. "
            f"Install it with: pip install 'financial-model[{self.extra}]'"
        )


def require(module: str, feature: str, extra: str | None = None) -> ModuleType:
    """Import ``module`` or raise ``MissingDependencyError`` naming the extra to install."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise MissingDependencyError(module, feature, extra) from exc


def is_available(module: str) -> bool:
    try:
        importlib.import_module(module)
    except ImportError:
        return False
    return True
