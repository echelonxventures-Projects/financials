"""
Line item definitions with calculation rules.
Supports inputs, calculations, links, and plugs for balancing.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Callable, Any
import pandas as pd
import numpy as np


class LineItemType(Enum):
    INPUT = "input"              # User-provided assumption
    CALCULATED = "calculated"    # Formula-driven
    LINK = "link"                # Reference to another statement's line
    PLUG = "plug"                # Balancing item (cash, revolver, etc.)
    AGGREGATE = "aggregate"      # Sum of children (e.g., Total Revenue)


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
        if isinstance(self.value, list):
            return self.value[period_idx] if period_idx < len(self.value) else self.value[-1]
        return self.value


@dataclass
class LineItem:
    """
    A single line item in a financial statement.
    Can be an input, calculation, link, or plug.
    """
    name: str
    line_type: LineItemType
    statement_type: str  # "income_statement", "balance_sheet", "cash_flow"
    parent: str | None = None  # For aggregation hierarchy
    children: list[str] = field(default_factory=list)
    calculation_rule: CalculationRule | None = None
    calculation_params: dict[str, Any] = field(default_factory=dict)
    custom_formula: Callable[[pd.DataFrame, int], float] | None = None
    driver: Driver | None = None
    format: str = "{:,.0f}"  # Display format
    unit: str = "$"  # $, %, x, days
    order: int = 0
    is_total: bool = False
    description: str = ""

    def calculate(self, model_data: pd.DataFrame, period_idx: int, assumptions: dict[str, Driver]) -> float:
        """Calculate value for given period."""
        if self.line_type == LineItemType.INPUT:
            # First check assumptions (allows overriding line item defaults)
            if self.driver and self.driver.name in assumptions:
                return assumptions[self.driver.name].get_value(period_idx)
            # Fallback to line item's own driver
            if self.driver:
                return self.driver.get_value(period_idx)
            return 0.0

        elif self.line_type == LineItemType.CALCULATED:
            if self.custom_formula:
                return self.custom_formula(model_data, period_idx)
            return self._apply_builtin_rule(model_data, period_idx, assumptions)

        elif self.line_type == LineItemType.LINK:
            # Reference to another line item - model_data should have a MultiIndex with (statement, line)
            source_line = self.calculation_params.get("source_line")
            source_statement = self.calculation_params.get("source_statement")
            if source_line and source_statement:
                try:
                    if isinstance(model_data.index, pd.MultiIndex):
                        return model_data.loc[(source_statement, source_line)].iloc[period_idx]
                    else:
                        # Fallback: check if it's in current statement
                        if source_line in model_data.index:
                            return model_data.loc[source_line].iloc[period_idx]
                except (KeyError, IndexError):
                    pass
            return 0.0

        elif self.line_type == LineItemType.AGGREGATE:
            # Sum of children
            total = 0.0
            for child in self.children:
                try:
                    if isinstance(model_data.index, pd.MultiIndex):
                        # Try to find in any statement
                        for stmt in model_data.index.get_level_values(0).unique():
                            try:
                                val = model_data.loc[(stmt, child)].iloc[period_idx]
                                if pd.notna(val):
                                    total += val
                                    break
                            except KeyError:
                                continue
                    else:
                        if child in model_data.index:
                            val = model_data.loc[child].iloc[period_idx]
                            if pd.notna(val):
                                total += val
                except (KeyError, IndexError):
                    continue
            return total

        elif self.line_type == LineItemType.PLUG:
            # Balancing item - calculated by balancer
            return 0.0

        return 0.0

    def _apply_builtin_rule(
        self,
        model_data: pd.DataFrame,
        period_idx: int,
        assumptions: dict[str, Driver],
    ) -> float:
        rule = self.calculation_rule
        params = self.calculation_params

        if rule == CalculationRule.PCT_OF_REVENUE:
            # Look up Revenue in income_statement - check both MultiIndex and simple index
            revenue = 0
            try:
                if isinstance(model_data.index, pd.MultiIndex):
                    revenue = model_data.loc[("income_statement", "Revenue")].iloc[period_idx]
                else:
                    revenue = model_data.loc["Revenue"].iloc[period_idx] if "Revenue" in model_data.index else 0
            except (KeyError, IndexError):
                revenue = 0

            # If revenue is NaN or 0, try to get from the assumptions (the driver value)
            if pd.isna(revenue) or revenue == 0:
                # The Revenue line item is an INPUT with a driver - use the driver value
                rev_driver = assumptions.get("revenue")
                if rev_driver:
                    revenue = rev_driver.get_value(period_idx)

            pct = assumptions.get(params.get("driver"), Driver("", params.get("value", 0))).get_value(period_idx)
            return revenue * pct

        elif rule == CalculationRule.PCT_OF_LINE:
            base_line = params.get("base_line")
            base_val = 0
            try:
                if isinstance(model_data.index, pd.MultiIndex):
                    for stmt in model_data.index.get_level_values(0).unique():
                        try:
                            base_val = model_data.loc[(stmt, base_line)].iloc[period_idx]
                            if pd.notna(base_val):
                                break
                        except KeyError:
                            continue
                else:
                    base_val = model_data.loc[base_line].iloc[period_idx] if base_line in model_data.index else 0
            except (KeyError, IndexError):
                base_val = 0
            pct = assumptions.get(params.get("driver"), Driver("", params.get("value", 0))).get_value(period_idx)
            return base_val * pct

        elif rule == CalculationRule.GROWTH_RATE:
            # For period 0, use base_line value (if provided) or line item's own driver value
            # For subsequent periods, grow from previous period
            if period_idx == 0:
                # Try to get base value from base_line if specified
                base_line = params.get("base_line")
                if base_line:
                    base_val = 0
                    try:
                        if isinstance(model_data.index, pd.MultiIndex):
                            for stmt in model_data.index.get_level_values(0).unique():
                                try:
                                    base_val = model_data.loc[(stmt, base_line)].iloc[period_idx]
                                    if pd.notna(base_val) and base_val != 0:
                                        break
                                except KeyError:
                                    continue
                        else:
                            base_val = model_data.loc[base_line].iloc[period_idx]
                    except (KeyError, IndexError):
                        base_val = 0
                    if pd.notna(base_val) and base_val != 0:
                        return base_val

                # Fallback: use the line item's own driver (base value driver)
                if self.driver:
                    return self.driver.get_value(0)
                # Final fallback: use params value
                return params.get("value", 0)
            else:
                # Look up previous period value - handle both MultiIndex and simple index
                prev_val = 0
                try:
                    if isinstance(model_data.index, pd.MultiIndex):
                        # Try to find in any statement
                        for stmt in model_data.index.get_level_values(0).unique():
                            try:
                                prev_val = model_data.loc[(stmt, self.name)].iloc[period_idx - 1]
                                if pd.notna(prev_val):
                                    break
                            except KeyError:
                                continue
                    else:
                        prev_val = model_data.loc[self.name].iloc[period_idx - 1]
                except (KeyError, IndexError):
                    prev_val = 0

                if pd.isna(prev_val):
                    prev_val = 0
                growth = assumptions.get(params.get("driver"), Driver("", params.get("value", 0))).get_value(period_idx)
                return prev_val * (1 + growth)

        elif rule == CalculationRule.DAYS_OUTSTANDING:
            # AR/AP/Inventory: (Days/365) * Revenue or COGS
            days = assumptions.get(params.get("driver"), Driver("", params.get("value", 0))).get_value(period_idx)
            base_line = params.get("base_line", "Revenue")
            base_val = 0
            try:
                if isinstance(model_data.index, pd.MultiIndex):
                    for stmt in model_data.index.get_level_values(0).unique():
                        try:
                            base_val = model_data.loc[(stmt, base_line)].iloc[period_idx]
                            if pd.notna(base_val):
                                break
                        except KeyError:
                            continue
                else:
                    base_val = model_data.loc[base_line].iloc[period_idx] if base_line in model_data.index else 0
            except (KeyError, IndexError):
                base_val = 0
            return (days / 365) * base_val

        elif rule == CalculationRule.TURNOVER:
            turnover = assumptions.get(params.get("driver"), Driver("", params.get("value", 0))).get_value(period_idx)
            base_line = params.get("base_line", "Revenue")
            base_val = 0
            try:
                if isinstance(model_data.index, pd.MultiIndex):
                    for stmt in model_data.index.get_level_values(0).unique():
                        try:
                            base_val = model_data.loc[(stmt, base_line)].iloc[period_idx]
                            if pd.notna(base_val):
                                break
                        except KeyError:
                            continue
                else:
                    base_val = model_data.loc[base_line].iloc[period_idx] if base_line in model_data.index else 0
            except (KeyError, IndexError):
                base_val = 0
            if turnover != 0:
                return base_val / turnover
            return 0.0

        elif rule == CalculationRule.SUM_CHILDREN:
            total = 0.0
            for child in self.children:
                try:
                    if isinstance(model_data.index, pd.MultiIndex):
                        for stmt in model_data.index.get_level_values(0).unique():
                            try:
                                val = model_data.loc[(stmt, child)].iloc[period_idx]
                                if pd.notna(val):
                                    total += val
                                    break
                            except KeyError:
                                continue
                    else:
                        if child in model_data.index:
                            val = model_data.loc[child].iloc[period_idx]
                            if pd.notna(val):
                                total += val
                except (KeyError, IndexError):
                    continue
            return total

        return 0.0