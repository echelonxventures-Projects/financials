"""
Financial statement definitions with line item registries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from financial_model.core.line_item import CalculationRule, LineItem, LineItemType
from financial_model.core.period import PeriodIndex


class StatementType(Enum):
    INCOME_STATEMENT = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"


@dataclass
class Statement:
    """
    A financial statement with ordered line items.
    Maintains calculation dependencies and formatting.
    """

    statement_type: StatementType
    line_items: dict[str, LineItem] = field(default_factory=dict)
    periods: PeriodIndex | None = None

    def add_line_item(self, item: LineItem) -> Statement:
        self.line_items[item.name] = item
        return self

    def remove_line_item(self, name: str) -> Statement:
        self.line_items.pop(name, None)
        return self

    def get_line_item(self, name: str) -> LineItem | None:
        return self.line_items.get(name)

    def ordered_items(self) -> list[LineItem]:
        return sorted(self.line_items.values(), key=lambda x: x.order)

    def input_items(self) -> list[LineItem]:
        return [item for item in self.ordered_items() if item.line_type == LineItemType.INPUT]

    def calculated_items(self) -> list[LineItem]:
        return [item for item in self.ordered_items() if item.line_type == LineItemType.CALCULATED]

    def plug_items(self) -> list[LineItem]:
        return [item for item in self.ordered_items() if item.line_type == LineItemType.PLUG]

    def link_items(self) -> list[LineItem]:
        return [item for item in self.ordered_items() if item.line_type == LineItemType.LINK]

    def aggregate_items(self) -> list[LineItem]:
        return [item for item in self.ordered_items() if item.line_type == LineItemType.AGGREGATE]

    def leaf_lines(self, name: str) -> list[str]:
        """
        Expand an aggregate line into the leaf line names it ultimately sums.
        A line that is not an aggregate (or is unknown) is returned as itself.
        """
        item = self.line_items.get(name)
        if (
            item is None
            or not item.children
            or item.line_type
            not in (
                LineItemType.AGGREGATE,
                LineItemType.CALCULATED,
            )
        ):
            return [name]
        if item.line_type == LineItemType.CALCULATED and item.calculation_rule != CalculationRule.SUM_CHILDREN:
            return [name]
        leaves: list[str] = []
        for child in item.children:
            if child == name:
                continue
            leaves.extend(self.leaf_lines(child))
        return leaves

    def to_dataframe(self, periods: PeriodIndex | None = None) -> pd.DataFrame:
        """Create empty DataFrame with line items as index, periods as columns."""
        periods = periods or self.periods or PeriodIndex()
        columns = periods.to_dataframe_index()
        index = [item.name for item in self.ordered_items()]
        return pd.DataFrame(index=index, columns=columns, dtype=float)

    def validate(self) -> list[str]:
        """Validate statement structure, return list of errors."""
        errors = []
        names = set(self.line_items.keys())

        for item in self.line_items.values():
            if item.parent and item.parent not in names:
                errors.append(f"{item.name}: parent '{item.parent}' not found")

            for child in item.children:
                if child not in names:
                    errors.append(f"{item.name}: child '{child}' not found")

            if item.calculation_rule == CalculationRule.PCT_OF_LINE:
                base = item.calculation_params.get("base_line")
                if base and base not in names:
                    errors.append(f"{item.name}: base_line '{base}' not found")

            if item.line_type == LineItemType.CALCULATED and not item.custom_formula:
                if item.calculation_rule is None:
                    errors.append(f"{item.name}: calculated line needs a calculation_rule or custom_formula")
                elif item.calculation_rule == CalculationRule.CUSTOM:
                    errors.append(f"{item.name}: custom rule requires a custom_formula (cannot be loaded from YAML)")

            if item.line_type == LineItemType.LINK:
                source = item.calculation_params.get("source_line")
                if not source:
                    errors.append(f"{item.name}: LINK type requires source_line param")

        return errors
