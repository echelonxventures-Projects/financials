"""
Language-neutral description of the config format.

The same pydantic models validate API request bodies and produce the JSON Schema that
`financial-model schema` prints (committed as ``schemas/config.schema.json``), so editors
and validators in any language can check a YAML/JSON config without running Python.
"""

from __future__ import annotations

import json
from typing import Any, Union

from pydantic import BaseModel, ConfigDict, Field

from financial_model.models.registry import ALIASES

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_URL = "https://raw.githubusercontent.com/echelonxventures-Projects/financials/main/schemas/config.schema.json"

# typed as list[Any] so they fit pydantic's JsonValue in json_schema_extra
PERIOD_TYPES: list[Any] = ["annual", "quarterly", "monthly"]
STATEMENT_TYPES: list[Any] = ["income_statement", "balance_sheet", "cash_flow"]
BUILTIN_MODEL_TYPES: list[Any] = ["three-statement", "dcf", "lbo", "budget", "ma"]
DEAL_MODEL_TYPES: list[Any] = ["ma", *sorted(k for k, v in ALIASES.items() if v == "ma")]

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
    order: int = 0
    is_total: bool = False
    description: str = ""


class StatementConfig(BaseModel):
    type: str = Field(..., json_schema_extra={"enum": STATEMENT_TYPES})
    line_items: list[LineItemConfig] = Field(default_factory=list)


class ModelConfig(BaseModel):
    """A single-entity model: three-statement, dcf, lbo, budget (or a plugin model type)."""

    name: str = "Financial Model"
    model_type: str = Field(
        "three-statement",
        description="Model class; plugins can register more types",
        json_schema_extra={"examples": BUILTIN_MODEL_TYPES[:-1]},
    )
    periods: PeriodConfig = Field(default_factory=lambda: PeriodConfig())
    assumptions: AssumptionsConfig = Field(default_factory=lambda: AssumptionsConfig())
    statements: list[StatementConfig] = Field(default_factory=list)


class DealTerms(BaseModel):
    """Transaction terms of an M&A deal (all optional; defaults shown)."""

    model_config = ConfigDict(extra="forbid")

    offer_price_per_share: float = 0.0
    target_shares: float = 1_000_000
    cash_consideration_pct: float = 0.5
    stock_consideration_pct: float = 0.5
    synergies: float = Field(0.0, description="Annual run-rate pre-tax synergies")
    synergy_phase_in: list[float] = Field(default_factory=lambda: [0.3, 0.6, 0.8, 1.0, 1.0])
    financing_rate: float = 0.06
    tax_rate: float = 0.21
    acquirer_shares: float = 10_000_000
    acquirer_share_price: float | None = Field(None, description="Default: acquirer year-1 EPS x acquirer_pe")
    acquirer_pe: float = 20.0


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
