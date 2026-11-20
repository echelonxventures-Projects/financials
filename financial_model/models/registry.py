"""
Model-type registry shared by the CLI, the REST API and the M&A loader.

Register a new model class once here (or call ``register_model`` from a plugin)
and it becomes available as ``model_type`` in YAML configs, ``--type`` on the
CLI and ``config.model_type`` on the API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from financial_model.core.model import FinancialModel
from financial_model.models.budget import BudgetModel
from financial_model.models.business import B2BSaaSModel, B2CSubscriptionModel, PaaSModel
from financial_model.models.dcf import DCFModel
from financial_model.models.lbo import LBOModel
from financial_model.models.three_statement import ThreeStatementModel
from financial_model.utils.plugins import MODEL_GROUP, discover

MODEL_CLASSES: dict[str, type[FinancialModel]] = {
    "three-statement": ThreeStatementModel,
    "dcf": DCFModel,
    "lbo": LBOModel,
    "budget": BudgetModel,
    # Business-model templates (customer engine -> three statements)
    "paas": PaaSModel,
    "b2b-saas": B2BSaaSModel,
    "b2c-subscription": B2CSubscriptionModel,
}

# Composite models built from other models' configs (not FinancialModel subclasses).
COMPOSITE_TYPES = ("ma",)


def _definition_aliases() -> dict[str, str]:
    """Alternative names of model types, from each definition's ``aliases`` list."""
    from financial_model.core.definitions import available_definitions, load_definition

    out: dict[str, str] = {}
    for key in available_definitions():
        for alias in load_definition(key).get("aliases") or []:
            out[str(alias).strip().lower()] = key
    return out


ALIASES: dict[str, str] = _definition_aliases()


_plugins_loaded = False


def load_model_plugins() -> None:
    """Register models published under the ``financial_model.models`` entry point (once)."""
    global _plugins_loaded
    if _plugins_loaded:
        return
    _plugins_loaded = True
    for name, cls in discover(MODEL_GROUP).items():
        try:
            register_model(name, cls)
        except TypeError:
            continue


def normalize_model_type(model_type: str | None) -> str:
    """Canonical model-type key; raises ``ValueError`` for unknown types."""
    load_model_plugins()
    key = (model_type or "three-statement").strip().lower().replace("_", "-").replace(" ", "-")
    key = ALIASES.get(key, key)
    if key not in MODEL_CLASSES and key not in COMPOSITE_TYPES:
        raise ValueError(f"Unknown model type '{model_type}'. Choose from: {', '.join(available_model_types())}")
    return key


def available_model_types() -> list[str]:
    load_model_plugins()
    return [*MODEL_CLASSES, *COMPOSITE_TYPES]


def register_model(key: str, cls: type[FinancialModel]) -> None:
    """Make ``cls`` available under ``model_type: <key>``."""
    if not (isinstance(cls, type) and issubclass(cls, FinancialModel)):
        raise TypeError("model classes must subclass FinancialModel")
    MODEL_CLASSES[key.strip().lower()] = cls


def build_model(config: dict[str, Any], base_dir: Path | None = None, model_type: str | None = None) -> Any:
    """Instantiate the model a config describes (``model_type`` argument > config key)."""
    key = normalize_model_type(model_type or config.get("model_type"))
    if key == "ma":
        from financial_model.models.ma import MergerModel

        return MergerModel.from_config(config, base_dir=base_dir)
    return MODEL_CLASSES[key].from_config(config, base_dir=base_dir)
