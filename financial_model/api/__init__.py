"""
FastAPI REST API for financial model.
"""

from typing import Any

__all__ = ["app"]


def __getattr__(name: str) -> Any:
    if name == "app":
        from financial_model.api.main import app

        return app
    raise AttributeError(name)
