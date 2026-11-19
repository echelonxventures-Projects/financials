"""
Pre-built model implementations.
"""
from __future__ import annotations

from financial_model.models.budget import BudgetModel
from financial_model.models.dcf import DCFModel
from financial_model.models.lbo import LBOModel
from financial_model.models.ma import MergerModel
from financial_model.models.registry import (
    MODEL_CLASSES,
    available_model_types,
    build_model,
    normalize_model_type,
    register_model,
)
from financial_model.models.three_statement import ThreeStatementModel

__all__ = [
    "ThreeStatementModel",
    "DCFModel",
    "LBOModel",
    "MergerModel",
    "BudgetModel",
    "MODEL_CLASSES",
    "available_model_types",
    "build_model",
    "normalize_model_type",
    "register_model",
]
