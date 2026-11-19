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
from importlib.metadata import entry_points
from typing import Any

logger = logging.getLogger(__name__)

PROVIDER_GROUP = "financial_model.data_providers"
MODEL_GROUP = "financial_model.models"


def discover(group: str) -> dict[str, Any]:
    """Load every entry point in ``group``; a broken plugin is logged and skipped."""
    found: dict[str, Any] = {}
    for ep in entry_points(group=group):
        try:
            found[ep.name] = ep.load()
        except Exception as exc:  # noqa: BLE001 - one bad plugin must not break the toolkit
            logger.warning("Skipping plugin %s from %s: %s", ep.name, group, exc)
    return found
