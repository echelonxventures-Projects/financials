"""
Budget / Forecast Driver Model.
Bottom-up operating model with driver trees (ARR roll-forward, headcount x cost).
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from financial_model.core.assumptions import Assumptions
from financial_model.core.definitions import DefinitionError
from financial_model.core.line_item import CalculationRule, Driver, LineItem, LineItemType, lookup_value
from financial_model.core.model import FinancialModel
from financial_model.core.period import PeriodIndex

IS = "income_statement"
BS = "balance_sheet"
CF = "cash_flow"

_DEPT_KEYS = {"g&a": "gna", "r&d": "rd", "s&m": "sm"}


def department_key(name: str) -> str:
    """Driver prefix for a department name: 'G&A' -> 'gna', 'Customer Success' -> 'customer_success'."""
    lowered = name.strip().lower()
    if lowered in _DEPT_KEYS:
        return _DEPT_KEYS[lowered]
    return re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")


class BudgetModel(FinancialModel):
    """
    Driver-based budget/forecast model.
    Supports:
    - Revenue drivers (ARR roll-forward: starting + new + expansion - churn)
    - Expense drivers (headcount x fully loaded cost per department)
    - Department-level rollups
    - Scenario management (base, upside, downside)
    - Variance analysis vs actuals
    """

    model_type = "budget"

    def __init__(
        self,
        name: str | None = None,
        periods: PeriodIndex | None = None,
        assumptions: Assumptions | None = None,
        departments: list[str] | None = None,
    ):
        super().__init__(name=name, periods=periods, assumptions=assumptions)
        self.departments = list(departments or self.setting("departments", []))
        self.headcount_by_dept: dict[str, list[float]] = {}
        self.cost_per_head: dict[str, float] = {}
        self._setup_budget_structure()

    # ------------------------------------------------------------------ #
    def _dept_items(self, dept: str, order: int, headcount: Any = None, cost: Any = None) -> list[LineItem]:
        key = department_key(dept)
        seeds = self.setting("department_defaults", {})

        def driver(suffix: str, given: Any, label: str) -> Driver:
            name = f"{key}_{suffix}"
            if given is not None:
                return Driver(name, given, f"{dept} {label}", seeds.get(suffix, {}).get("unit", ""))
            if name in self._default_drivers:
                return self.ref(name)
            seed = seeds.get(suffix)
            if seed is None:
                raise DefinitionError(f"budget: no default for '{name}' and no department_defaults.{suffix}")
            return Driver(name, seed["value"], f"{dept} {label}", seed.get("unit", ""))

        def expense(df: pd.DataFrame, idx: int, d: str = dept) -> float:
            hc = lookup_value(df, f"{d} Headcount", idx, statement=IS)
            cph = lookup_value(df, f"{d} Cost per Head", idx, statement=IS)
            return -hc * cph

        headcount_driver = driver("headcount", headcount, "headcount")
        cost_driver = driver("cost_per_head", cost, "fully loaded cost")
        return [
            LineItem(
                f"{dept} Headcount",
                LineItemType.INPUT,
                IS,
                order=order,
                unit="FTE",
                format="{:,.1f}",
                driver=headcount_driver,
            ),
            LineItem(
                f"{dept} Cost per Head",
                LineItemType.INPUT,
                IS,
                order=order + 1,
                unit="$",
                driver=cost_driver,
            ),
            LineItem(
                f"{dept} Expense",
                LineItemType.CALCULATED,
                IS,
                order=order + 2,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=expense,
                description=f"{dept} total expense (negative)",
            ),
        ]

    def _setup_budget_structure(self) -> None:
        """Set up driver-based budget line items."""

        def revenue(df: pd.DataFrame, idx: int) -> float:
            start = lookup_value(df, "Starting ARR", idx, statement=IS)
            end = lookup_value(df, "Ending ARR", idx, statement=IS)
            return (start + end) / 2

        rev_items = [
            LineItem(
                "Starting ARR",
                LineItemType.INPUT,
                IS,
                order=1,
                unit="$",
                driver=self.ref("starting_arr"),
            ),
            LineItem(
                "New ARR",
                LineItemType.INPUT,
                IS,
                order=2,
                unit="$",
                driver=self.ref("new_arr"),
            ),
            LineItem(
                "Expansion ARR",
                LineItemType.INPUT,
                IS,
                order=3,
                unit="$",
                driver=self.ref("expansion_arr"),
            ),
            LineItem(
                "Churned ARR",
                LineItemType.INPUT,
                IS,
                order=4,
                unit="$",
                driver=self.ref("churned_arr"),
            ),
            LineItem(
                "Ending ARR",
                LineItemType.CALCULATED,
                IS,
                order=5,
                unit="$",
                calculation_rule=CalculationRule.SUM_CHILDREN,
                children=["Starting ARR", "New ARR", "Expansion ARR", "Churned ARR"],
                is_total=True,
            ),
            LineItem(
                "Revenue",
                LineItemType.CALCULATED,
                IS,
                order=10,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=revenue,
                is_total=True,
                description="Recognized revenue (average ARR)",
            ),
        ]
        for item in rev_items:
            self.add_income_statement_item(item)

        for i, dept in enumerate(self.departments):
            for item in self._dept_items(dept, 20 + i * 10):
                self.add_income_statement_item(item)

        self.add_income_statement_item(
            LineItem(
                "Total Operating Expense",
                LineItemType.AGGREGATE,
                IS,
                order=100,
                unit="$",
                children=[f"{d} Expense" for d in self.departments],
                is_total=True,
                description="Total OpEx (negative)",
            )
        )
        self.add_income_statement_item(
            LineItem(
                "Operating Income",
                LineItemType.CALCULATED,
                IS,
                order=110,
                unit="$",
                calculation_rule=CalculationRule.SUM_CHILDREN,
                children=["Revenue", "Total Operating Expense"],
                is_total=True,
            )
        )

        def net_income(df: pd.DataFrame, idx: int, drivers: dict[str, Driver]) -> float:
            op_income = lookup_value(df, "Operating Income", idx, statement=IS)
            tax_rate = drivers["tax_rate"].get_value(idx)
            return op_income * (1 - tax_rate)

        self.add_income_statement_item(
            LineItem(
                "Net Income",
                LineItemType.CALCULATED,
                IS,
                order=120,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=net_income,
                driver=self.ref("tax_rate"),
                is_total=True,
                description="Operating income x (1 - tax rate)",
            )
        )

        # Minimal balance sheet / cash flow so the statements tie
        bs_items = [
            LineItem("Cash & Cash Equivalents", LineItemType.PLUG, BS, order=1, unit="$"),
            LineItem(
                "Total Assets",
                LineItemType.AGGREGATE,
                BS,
                order=100,
                unit="$",
                children=["Cash & Cash Equivalents"],
                is_total=True,
            ),
            LineItem("Total Liabilities", LineItemType.AGGREGATE, BS, order=200, unit="$", children=[], is_total=True),
            LineItem("Retained Earnings", LineItemType.PLUG, BS, order=250, unit="$"),
            LineItem(
                "Total Equity",
                LineItemType.AGGREGATE,
                BS,
                order=300,
                unit="$",
                children=["Retained Earnings"],
                is_total=True,
            ),
        ]
        for item in bs_items:
            self.add_balance_sheet_item(item)

        cf_items = [
            LineItem(
                "Net Income",
                LineItemType.LINK,
                CF,
                order=1,
                unit="$",
                calculation_params={"source_line": "Net Income", "source_statement": IS},
            ),
            LineItem(
                "Operating Cash Flow",
                LineItemType.AGGREGATE,
                CF,
                order=10,
                unit="$",
                children=["Net Income"],
                is_total=True,
            ),
            LineItem("Investing Cash Flow", LineItemType.AGGREGATE, CF, order=20, unit="$", children=[], is_total=True),
            LineItem("Financing Cash Flow", LineItemType.AGGREGATE, CF, order=30, unit="$", children=[], is_total=True),
            LineItem("Net Change in Cash", LineItemType.PLUG, CF, order=90, unit="$"),
            LineItem(
                "Beginning Cash Balance",
                LineItemType.INPUT,
                CF,
                order=91,
                unit="$",
                driver=self.ref("beginning_cash"),
            ),
            LineItem(
                "Ending Cash Balance",
                LineItemType.AGGREGATE,
                CF,
                order=92,
                unit="$",
                children=["Beginning Cash Balance", "Net Change in Cash"],
                is_total=True,
            ),
        ]
        for item in cf_items:
            self.add_cash_flow_item(item)

    def opening_retained_earnings(self, drivers: dict[str, Driver]) -> float:
        driver = drivers.get("beginning_cash")
        return driver.get_value(0) if driver else 0.0

    def add_department(self, name: str, headcount: list[float], cost_per_head: float) -> BudgetModel:
        """Add a new department to the budget."""
        if name not in self.departments:
            self.departments.append(name)
        self.headcount_by_dept[name] = list(headcount)
        self.cost_per_head[name] = cost_per_head

        key = department_key(name)
        units = {k: v.get("unit", "") for k, v in self.setting("department_defaults", {}).items()}
        self.assumptions.set_driver(
            Driver(f"{key}_headcount", list(headcount), f"{name} headcount", units.get("headcount", ""))
        )
        self.assumptions.set_driver(
            Driver(f"{key}_cost_per_head", cost_per_head, f"{name} cost per head", units.get("cost_per_head", ""))
        )

        order = 20 + (self.departments.index(name)) * 10
        for item in self._dept_items(name, order, headcount=list(headcount), cost=cost_per_head):
            self.add_income_statement_item(item)

        total = self.income_statement.get_line_item("Total Operating Expense")
        if total is not None:
            total.children = [f"{d} Expense" for d in self.departments]
        return self

    def variance_analysis(self, actuals: pd.DataFrame, budget_result: Any) -> pd.DataFrame:
        """Compare actuals vs budget (latest period)."""
        budget_is = budget_result.income_statement
        variances = pd.DataFrame(
            index=budget_is.index, columns=["Budget", "Actual", "Variance", "Variance %"], dtype=float
        )

        for line in budget_is.index:
            if line in actuals.index:
                bud = float(budget_is.loc[line].iloc[-1])
                act = float(actuals.loc[line].iloc[-1])
                var = act - bud
                var_pct = var / abs(bud) if bud != 0 else 0.0
                variances.loc[line] = [bud, act, var, var_pct]

        return variances
