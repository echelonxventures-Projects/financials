"""
Everything the command line prints is a template in the settings: ``cli.messages`` (wording,
language) and ``presentation.console`` (statement layout). Override any key in a settings file::

    cli:
      messages:
        exported: "Écrit dans {output}"
"""

from __future__ import annotations

from typing import Any

from financial_model.config_loader import get_config
from financial_model.core.expressions import format_text


def messages() -> dict[str, str]:
    """The effective ``cli.messages`` templates."""
    return dict((get_config().get("cli") or {}).get("messages") or {})


def say(key: str, /, **values: Any) -> str:
    """Message ``key`` with ``{name}`` values filled in (format specs allowed: ``{max_diff:,.2f}``)."""
    templates = messages()
    if key not in templates:
        raise KeyError(f"no CLI message '{key}' in the settings (cli.messages)")
    return format_text(templates[key], values)


def console_style() -> dict[str, Any]:
    """The ``presentation.console`` settings (statement printout layout)."""
    return dict((get_config().get("presentation") or {}).get("console") or {})
