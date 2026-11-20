"""
Language-neutral description of the config format.

The same pydantic models validate API request bodies and produce the JSON Schema that
`financial-model schema` prints (committed as ``schemas/config.schema.json``), so editors
and validators in any language can check a YAML/JSON config without running Python.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, create_model

from financial_model.core.definitions import load_definition
from financial_model.core.period import PeriodType
from financial_model.core.statement import StatementType
from financial_model.models.registry import ALIASES, COMPOSITE_TYPES, available_model_types

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_URL = "https://raw.githubusercontent.com/echelonxventures-Projects/financials/main/schemas/config.schema.json"

# typed as list[Any] so they fit pydantic's JsonValue in json_schema_extra
PERIOD_TYPES: list[Any] = [p.value for p in PeriodType]
STATEMENT_TYPES: list[Any] = [s.value for s in StatementType]
# Single models first, composite (deal) types last.
BUILTIN_MODEL_TYPES: list[Any] = [
    *(t for t in available_model_types() if t not in COMPOSITE_TYPES),
    *COMPOSITE_TYPES,
]
DEAL_MODEL_TYPES: list[Any] = [*COMPOSITE_TYPES, *sorted(k for k, v in ALIASES.items() if v in COMPOSITE_TYPES)]

Number = Union[float, list[float]]


class PeriodConfig(BaseModel):
    start_year: int = 2024
    years: int = 5
    type: str = Field("annual", json_schema_extra={"enum": PERIOD_TYPES})
    count: int | None = Field(None, description="Total number of periods (overrides years x periods per year)")


class DriverConfig(BaseModel):
    value: Number = Field(..., description="One value for every period, or one value per period")
    description: str = ""
    unit: str = ""


class AssumptionsConfig(BaseModel):
    drivers: dict[str, DriverConfig] = Field(default_factory=dict)
    scenarios: dict[str, dict[str, Number]] = Field(
        default_factory=dict, description="Scenario name -> {driver: value} overrides of the base drivers"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class LineItemConfig(BaseModel):
    name: str
    line_type: str
    statement_type: str = Field(..., json_schema_extra={"enum": STATEMENT_TYPES})
    parent: str | None = None
    children: list[str] = Field(default_factory=list)
    calculation_rule: str | None = None
    calculation_params: dict[str, Any] = Field(default_factory=dict)
    driver: DriverConfig | None = None
    format: str = "{:,.0f}"
    unit: str = "$"
    order: float = 0
    is_total: bool = False
    description: str = ""
    formula: str | None = Field(None, description="Safe expression evaluated per period (docs/models/definitions.md)")


class StatementConfig(BaseModel):
    type: str = Field(..., json_schema_extra={"enum": STATEMENT_TYPES})
    line_items: list[LineItemConfig] = Field(default_factory=list)


class ModelConfig(BaseModel):
    """A single-entity model: three-statement, dcf, lbo, budget, paas, b2b-saas, b2c-subscription (or a plugin type)."""

    name: str = "Financial Model"
    model_type: str = Field(
        "three-statement",
        description="Model class; plugins can register more types",
        json_schema_extra={"examples": [t for t in BUILTIN_MODEL_TYPES if t not in COMPOSITE_TYPES]},
    )
    periods: PeriodConfig = Field(default_factory=lambda: PeriodConfig())
    assumptions: AssumptionsConfig = Field(default_factory=lambda: AssumptionsConfig())
    statements: list[StatementConfig] = Field(default_factory=list)


def _deal_terms_model() -> type[BaseModel]:
    """DealTerms built from the ``deal`` section of models/definitions/ma.yaml (no defaults in code)."""
    definition = load_definition("ma")
    descriptions = definition.get("deal_descriptions") or {}
    specs: dict[str, Any] = {}
    for key, default in (definition.get("deal") or {}).items():
        if default is None:
            annotation: Any = Optional[float]  # noqa: UP045 - evaluated at runtime on Python 3.9
        elif isinstance(default, list):
            annotation = list[float]
        else:
            annotation = float
        specs[key] = (annotation, Field(default, description=descriptions.get(key)))
    model: type[BaseModel] = create_model(
        "DealTerms",
        __config__=ConfigDict(extra="forbid"),
        __doc__="Transaction terms of an M&A deal (all optional; defaults shown).",
        **specs,
    )
    return model


if TYPE_CHECKING:
    DealTerms = BaseModel
else:
    DealTerms = _deal_terms_model()


class DealConfig(BaseModel):
    """An M&A deal: two model configs (inline, or file paths when used from the CLI) plus terms."""

    name: str = "M&A Model"
    model_type: str = Field(..., json_schema_extra={"enum": DEAL_MODEL_TYPES})
    acquirer: ModelConfig | str = Field(..., description="Inline model config, or a path to one (CLI only)")
    target: ModelConfig | str = Field(..., description="Inline model config, or a path to one (CLI only)")
    deal: DealTerms = Field(default_factory=lambda: DealTerms())


def config_json_schema() -> dict[str, Any]:
    """JSON Schema (draft 2020-12) accepted for any config file: a model or an M&A deal."""
    model = ModelConfig.model_json_schema(ref_template="#/$defs/{model}")
    deal = DealConfig.model_json_schema(ref_template="#/$defs/{model}")
    defs = {**model.pop("$defs", {}), **deal.pop("$defs", {})}
    defs["ModelConfig"], defs["DealConfig"] = model, deal
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": SCHEMA_URL,
        "title": "Financial Model config",
        "description": "Config accepted by the financial-model CLI, API and library.",
        # model_type picks the shape, as it does at runtime: an M&A deal or a single model.
        "if": {"required": ["model_type"], "properties": {"model_type": {"enum": DEAL_MODEL_TYPES}}},
        "then": {"$ref": "#/$defs/DealConfig"},
        "else": {"$ref": "#/$defs/ModelConfig"},
        "$defs": dict(sorted(defs.items())),
    }


def config_json_schema_text() -> str:
    return json.dumps(config_json_schema(), indent=2, sort_keys=False) + "\n"
