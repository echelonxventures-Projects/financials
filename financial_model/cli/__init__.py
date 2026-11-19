"""
CLI interface for financial model.
"""

from typing import Any

__all__ = ["app"]


def __getattr__(name: str) -> Any:
    if name == "app":
        from financial_model.cli.main import app

        return app
    raise AttributeError(name)
