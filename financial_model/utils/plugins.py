"""
Entry-point plugin discovery.

Third-party packages extend the toolkit without code changes here by declaring, in
their own ``pyproject.toml``::

    [project.entry-points."financial_model.data_providers"]
    edgar = "my_pkg.providers:EdgarProvider"

    [project.entry-points."financial_model.models"]
    saas-cohort = "my_pkg.models:CohortModel"
"""

from __future__ import annotations

import logging
import sys
from importlib.metadata import entry_points
from typing import Any

logger = logging.getLogger(__name__)

PROVIDER_GROUP = "financial_model.data_providers"
MODEL_GROUP = "financial_model.models"


def _entry_points(group: str) -> list[Any]:
    if sys.version_info >= (3, 10):
        return list(entry_points(group=group))
    return list(entry_points().get(group, []))  # Python 3.9: a dict of group -> entry points


def discover(group: str) -> dict[str, Any]:
    """Load every entry point in ``group``; a broken plugin is logged and skipped."""
    found: dict[str, Any] = {}
    for ep in _entry_points(group):
        try:
            found[ep.name] = ep.load()
        except Exception as exc:  # noqa: BLE001 - one bad plugin must not break the toolkit
            logger.warning("Skipping plugin %s from %s: %s", ep.name, group, exc)
    return found
