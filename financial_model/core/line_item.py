"""
Line item definitions with calculation rules.
Supports inputs, calculations, links, and plugs for balancing.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd


class LineItemType(Enum):
    INPUT = "input"  # User-provided assumption
    CALCULATED = "calculated"  # Formula-driven
    LINK = "link"  # Reference to another statement's line
    PLUG = "plug"  # Balancing item (cash, revolver, etc.)
    AGGREGATE = "aggregate"  # Sum of children (e.g., Total Revenue)


class CalculationRule(Enum):
    """Built-in calculation patterns."""

    SUM_CHILDREN = "sum_children"
    PCT_OF_REVENUE = "pct_of_revenue"
    PCT_OF_LINE = "pct_of_line"
    GROWTH_RATE = "growth_rate"
    DAYS_OUTSTANDING = "days_outstanding"
    TURNOVER = "turnover"
    CUSTOM = "custom"


@dataclass
class Driver:
    """A configurable driver with metadata."""

    name: str
    value: float | list[float]  # Scalar or per-period array
    description: str = ""
    unit: str = ""  # %, $, days, x, etc.
    source: str = "assumption"  # assumption, calculated, external

    def get_value(self, period_idx: int) -> float:
        if isinstance(self.value, (list, tuple)):
            if not self.value:
                return 0.0
            idx = period_idx if period_idx < len(self.value) else len(self.value) - 1
            return float(self.value[idx])
        return float(self.value)


def lookup_value(
    model_data: pd.DataFrame,
    line: str,
    period_idx: int,
    statement: str | None = None,
    default: float = 0.0,
) -> float:
    """
    Read ``line`` for ``period_idx`` from a statement DataFrame.

    Works with the combined MultiIndex frame used during calculation
    (``(statement, line)`` rows) and with plain single-statement frames.
    Missing lines, out-of-range periods and NaN values return ``default``.
    """
    try:
        if isinstance(model_data.index, pd.MultiIndex):
            if statement is not None:
                key = (statement, line)
                if key in model_data.index:
                    val = model_data.loc[key].iloc[period_idx]
                    return default if pd.isna(val) else float(val)
                return default
            for stmt in model_data.index.get_level_values(0).unique():
                key = (stmt, line)
                if key in model_data.index:
                    val = model_data.loc[key].iloc[period_idx]
                    if pd.notna(val):
                        return float(val)
            return default
        if line in model_data.index:
            val = model_data.loc[line].iloc[period_idx]
            return default if pd.isna(val) else float(val)
    except (KeyError, IndexError):
        pass
    return default


def previous_value(
    model_data: pd.DataFrame,
    line: str,
    period_idx: int,
    opening: float = 0.0,
    statement: str | None = None,
) -> float:
    """Value of ``line`` in the prior period, or ``opening`` for the first period."""
    if period_idx <= 0:
        return opening
    return lookup_value(model_data, line, period_idx - 1, statement=statement, default=0.0)


@dataclass
class LineItem:
    """
    A single line item in a financial statement.
    Can be an input, calculation, link, or plug.

    ``custom_formula`` receives ``(model_data, period_idx)`` or, if it declares a
    third positional parameter, ``(model_data, period_idx, drivers)`` where
    ``drivers`` is the scenario-adjusted ``{name: Driver}`` mapping.
    """

    name: str
    line_type: LineItemType
    statement_type: str  # "income_statement", "balance_sheet", "cash_flow"
    parent: str | None = None  # For aggregation hierarchy
    children: list[str] = field(default_factory=list)
    calculation_rule: CalculationRule | None = None
    calculation_params: dict[str, Any] = field(default_factory=dict)
    custom_formula: Callable[..., float] | None = None
    driver: Driver | None = None
    format: str = "{:,.0f}"  # Display format
    unit: str = "$"  # $, %, x, days
    order: float = 0
    is_total: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.line_type, str):
            self.line_type = LineItemType(self.line_type)
        if isinstance(self.calculation_rule, str):
            self.calculation_rule = CalculationRule(self.calculation_rule)
        if isinstance(self.driver, dict):
            driver_data = dict(self.driver)
            driver_data.setdefault("name", self.name)
            self.driver = Driver(**driver_data)
        if self.children is None:
            self.children = []
        if self.calculation_params is None:
            self.calculation_params = {}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LineItem:
        """Build a LineItem from a YAML/JSON mapping (enums and drivers as plain values)."""
        payload = {k: v for k, v in data.items() if k != "custom_formula"}
        return cls(**payload)

    # ------------------------------------------------------------------ #
    # Calculation
    # ------------------------------------------------------------------ #
    def calculate(self, model_data: pd.DataFrame, period_idx: int, assumptions: dict[str, Driver]) -> float:
        """Calculate value for given period."""
        if self.line_type == LineItemType.INPUT:
            # Assumptions override the line item's own default driver
            if self.driver and self.driver.name in assumptions:
                return assumptions[self.driver.name].get_value(period_idx)
            if self.driver:
                return self.driver.get_value(period_idx)
            return 0.0

        if self.line_type == LineItemType.CALCULATED:
            if self.custom_formula:
                return float(self._call_custom(model_data, period_idx, assumptions))
            return self._apply_builtin_rule(model_data, period_idx, assumptions)

        if self.line_type == LineItemType.LINK:
            source_line = self.calculation_params.get("source_line")
            source_statement = self.calculation_params.get("source_statement")
            sign = float(self.calculation_params.get("sign", 1))
            if source_line:
                return sign * lookup_value(model_data, source_line, period_idx, statement=source_statement)
            return 0.0

        if self.line_type == LineItemType.AGGREGATE:
            return self._sum_lines(model_data, period_idx, self.children)

        # PLUG: solved by the balancer
        return 0.0

    def _call_custom(self, model_data: pd.DataFrame, period_idx: int, assumptions: dict[str, Driver]) -> float:
        assert self.custom_formula is not None
        if self._custom_arity() >= 3:
            return self.custom_formula(model_data, period_idx, assumptions)
        return self.custom_formula(model_data, period_idx)

    def _custom_arity(self) -> int:
        """Number of required positional parameters of ``custom_formula`` (cached)."""
        cached = self.__dict__.get("_arity_cache")
        if cached is not None:
            return int(cached)
        arity = 2
        try:
            sig = inspect.signature(self.custom_formula)  # type: ignore[arg-type]
            required = [
                p
                for p in sig.parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty
            ]
            if any(p.kind == p.VAR_POSITIONAL for p in sig.parameters.values()):
                arity = 3
            else:
                arity = len(required)
        except (TypeError, ValueError):
            arity = 2
        self.__dict__["_arity_cache"] = arity
        return arity

    def _lookup(self, model_data: pd.DataFrame, line: str, period_idx: int, default: float = 0.0) -> float:
        """Read a line, preferring this item's own statement over same-named lines elsewhere."""
        if isinstance(model_data.index, pd.MultiIndex) and (self.statement_type, line) in model_data.index:
            val = model_data.loc[(self.statement_type, line)].iloc[period_idx]
            if pd.notna(val):
                return float(val)
        return lookup_value(model_data, line, period_idx, statement=None, default=default)

    def _sum_lines(self, model_data: pd.DataFrame, period_idx: int, lines: list[str]) -> float:
        total = 0.0
        for child in lines:
            total += self._lookup(model_data, child, period_idx, default=0.0)
        return total

    def _param_driver_value(self, params: dict[str, Any], assumptions: dict[str, Driver], period_idx: int) -> float:
        """Resolve the driver named in ``params['driver']`` (assumptions first, then own driver, then ``value``)."""
        name = params.get("driver")
        if name and name in assumptions:
            return assumptions[name].get_value(period_idx)
        if self.driver is not None and (name is None or self.driver.name == name):
            return self.driver.get_value(period_idx)
        return float(params.get("value", 0))

    def _apply_builtin_rule(
        self,
        model_data: pd.DataFrame,
        period_idx: int,
        assumptions: dict[str, Driver],
    ) -> float:
        rule = self.calculation_rule
        params = self.calculation_params
        has_sign = "sign" in params
        sign = float(params.get("sign", 1))

        if rule == CalculationRule.PCT_OF_REVENUE:
            revenue = lookup_value(model_data, "Revenue", period_idx, statement="income_statement", default=0.0)
            if revenue == 0:
                revenue = lookup_value(model_data, "Revenue", period_idx, default=0.0)
            if revenue == 0 and "revenue" in assumptions:
                revenue = assumptions["revenue"].get_value(period_idx)
            pct = self._param_driver_value(params, assumptions, period_idx)
            if has_sign:
                pct = abs(pct)
            return sign * revenue * pct

        if rule == CalculationRule.PCT_OF_LINE:
            base_line = params.get("base_line")
            base_val = self._lookup(model_data, base_line, period_idx) if base_line else 0.0
            pct = self._param_driver_value(params, assumptions, period_idx)
            if has_sign:
                pct = abs(pct)
            return sign * base_val * pct

        if rule == CalculationRule.GROWTH_RATE:
            if period_idx == 0:
                base_line = params.get("base_line")
                if base_line:
                    base_val = self._lookup(model_data, base_line, 0, default=0.0)
                    if base_val != 0:
                        return base_val
                if self.driver and self.driver.name in assumptions:
                    return assumptions[self.driver.name].get_value(0)
                if self.driver:
                    return self.driver.get_value(0)
                return float(params.get("value", 0))
            prev_val = self._lookup(model_data, self.name, period_idx - 1, default=0.0)
            growth = self._param_driver_value(params, assumptions, period_idx)
            return prev_val * (1 + growth)

        if rule == CalculationRule.DAYS_OUTSTANDING:
            # AR/AP/Inventory balance = days / days_in_year * |base flow|
            days = self._param_driver_value(params, assumptions, period_idx)
            days_in_year = float(params.get("days_in_year", 365))
            base_line = params.get("base_line", "Revenue")
            base_val = abs(self._lookup(model_data, base_line, period_idx))
            return sign * (days / days_in_year) * base_val

        if rule == CalculationRule.TURNOVER:
            turnover = self._param_driver_value(params, assumptions, period_idx)
            base_line = params.get("base_line", "Revenue")
            base_val = abs(self._lookup(model_data, base_line, period_idx))
            if turnover != 0:
                return sign * base_val / turnover
            return 0.0

        if rule == CalculationRule.SUM_CHILDREN:
            return sign * self._sum_lines(model_data, period_idx, self.children)

        return 0.0
