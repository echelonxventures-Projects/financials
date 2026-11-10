"""
Budget / Forecast Driver Model.
Bottom-up operating model with driver trees.
"""

from dataclasses import dataclass, field
from typing import Any
import pandas as pd
import numpy as np

from financial_model.core.model import FinancialModel
from financial_model.core.statement import Statement, StatementType
from financial_model.core.line_item import LineItem, LineItemType, CalculationRule, Driver
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.core.assumptions import Assumptions


class BudgetModel(FinancialModel):
    """
    Driver-based budget/forecast model.
    Supports:
    - Revenue drivers (units × price, ARR × NRR, etc.)
    - Expense drivers (headcount × cost, % of revenue, fixed + variable)
    - Department-level rollups
    - Scenario management (base, upside, downside)
    - Variance analysis vs actuals
    """

    def __init__(
        self,
        name: str = "Budget Model",
        periods: PeriodIndex | None = None,
        assumptions: Assumptions | None = None,
        departments: list[str] | None = None,
    ):
        super().__init__(name=name, periods=periods, assumptions=assumptions)
        self.departments = departments or ["Engineering", "Sales", "Marketing", "G&A", "R&D"]
        self.headcount_by_dept = {}
        self.cost_per_head = {}
        self._setup_budget_structure()

    def _setup_budget_structure(self) -> None:
        """Set up driver-based budget line items."""
        # Revenue drivers (example: SaaS)
        rev_items = [
            LineItem("Starting ARR", LineItemType.INPUT, "income_statement", order=1, unit="$",
                     driver=Driver("starting_arr", [10_000_000], "Beginning ARR", "$")),
            LineItem("New ARR", LineItemType.INPUT, "income_statement", order=2, unit="$",
                     driver=Driver("new_arr", [3_000_000], "New ARR booked", "$")),
            LineItem("Expansion ARR", LineItemType.INPUT, "income_statement", order=3, unit="$",
                     driver=Driver("expansion_arr", [1_500_000], "Expansion ARR", "$")),
            LineItem("Churned ARR", LineItemType.INPUT, "income_statement", order=4, unit="$",
                     driver=Driver("churned_arr", [-500_000], "Churned ARR", "$")),
            LineItem("Ending ARR", LineItemType.CALCULATED, "income_statement", order=5, unit="$",
                     calculation_rule=CalculationRule.SUM_CHILDREN,
                     children=["Starting ARR", "New ARR", "Expansion ARR", "Churned ARR"],
                     is_total=True),
            LineItem("Revenue", LineItemType.CALCULATED, "income_statement", order=10, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: (df.loc["Starting ARR"].iloc[idx] + df.loc["Ending ARR"].iloc[idx]) / 2
                                                    if "Starting ARR" in df.index else 0,
                     description="Recognized revenue (avg ARR)"),
        ]
        for item in rev_items:
            self.add_income_statement_item(item)

        # Headcount-driven expenses
        for dept in self.departments:
            hc_items = [
                LineItem(f"{dept} Headcount", LineItemType.INPUT, "income_statement", order=20, unit="FTE",
                         driver=Driver(f"{dept.lower()}_headcount", [10], f"{dept} headcount", "FTE")),
                LineItem(f"{dept} Cost per Head", LineItemType.INPUT, "income_statement", order=21, unit="$",
                         driver=Driver(f"{dept.lower()}_cost_per_head", [150_000], f"{dept} fully loaded cost", "$")),
                LineItem(f"{dept} Expense", LineItemType.CALCULATED, "income_statement", order=22, unit="$",
                         calculation_rule=CalculationRule.CUSTOM,
                         custom_formula=lambda df, idx, d=dept: df.loc[f"{d} Headcount"].iloc[idx] * df.loc[f"{d} Cost per Head"].iloc[idx]
                                                    if f"{d} Headcount" in df.index else 0,
                         description=f"{dept} total expense"),
            ]
            for item in hc_items:
                self.add_income_statement_item(item)

        # Total OpEx
        dept_expenses = [f"{d} Expense" for d in self.departments]
        self.add_income_statement_item(LineItem(
            "Total Operating Expense", LineItemType.AGGREGATE, "income_statement", order=50, unit="$",
            children=dept_expenses, is_total=True, description="Total OpEx"
        ))

        # Net Income (simplified)
        self.add_income_statement_item(LineItem(
            "Operating Income", LineItemType.CALCULATED, "income_statement", order=60, unit="$",
            calculation_rule=CalculationRule.SUM_CHILDREN,
            children=["Revenue", "Total Operating Expense"],
            is_total=True
        ))
        self.add_income_statement_item(LineItem(
            "Net Income", LineItemType.CALCULATED, "income_statement", order=90, unit="$",
            calculation_rule=CalculationRule.PCT_OF_LINE,
            calculation_params={"base_line": "Operating Income", "driver": "tax_rate"},
            driver=Driver("tax_rate", [0.21], "Tax rate", "%")
        ))

        # Add minimal BS and CF items to prevent empty statement errors
        bs_items = [
            LineItem("Cash & Cash Equivalents", LineItemType.PLUG, "balance_sheet", order=1, unit="$",
                     driver=Driver("cash", 0, "Cash", "$")),
            LineItem("Total Assets", LineItemType.AGGREGATE, "balance_sheet", order=100, unit="$",
                     children=["Cash & Cash Equivalents"], is_total=True),
            LineItem("Total Liabilities", LineItemType.AGGREGATE, "balance_sheet", order=200, unit="$",
                     children=[], is_total=True),
            LineItem("Total Equity", LineItemType.AGGREGATE, "balance_sheet", order=300, unit="$",
                     children=[], is_total=True),
        ]
        for item in bs_items:
            self.add_balance_sheet_item(item)

        cf_items = [
            LineItem("Net Income", LineItemType.LINK, "cash_flow", order=1, unit="$",
                     calculation_params={"source_line": "Net Income", "source_statement": "income_statement"}),
            LineItem("Net Change in Cash", LineItemType.PLUG, "cash_flow", order=90, unit="$",
                     driver=Driver("net_change_cash", 0, "Net change in cash", "$")),
            LineItem("Beginning Cash Balance", LineItemType.INPUT, "cash_flow", order=91, unit="$",
                     driver=Driver("beginning_cash", 0, "Beginning cash", "$")),
            LineItem("Ending Cash Balance", LineItemType.CALCULATED, "cash_flow", order=92, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: df.loc["Beginning Cash Balance"].iloc[idx] + df.loc["Net Change in Cash"].iloc[idx]
                                                    if "Beginning Cash Balance" in df.index else 0),
        ]
        for item in cf_items:
            self.add_cash_flow_item(item)

    def add_department(self, name: str, headcount: list[int], cost_per_head: float) -> "BudgetModel":
        """Add a new department to the budget."""
        if name not in self.departments:
            self.departments.append(name)
        self.headcount_by_dept[name] = headcount
        self.cost_per_head[name] = cost_per_head

        # Update assumptions
        self.assumptions.set_driver(Driver(f"{name.lower()}_headcount", headcount, f"{name} headcount", "FTE"))
        self.assumptions.set_driver(Driver(f"{name.lower()}_cost_per_head", cost_per_head, f"{name} cost per head", "$"))
        return self

    def variance_analysis(self, actuals: pd.DataFrame, budget_result: Any) -> pd.DataFrame:
        """Compare actuals vs budget."""
        budget_is = budget_result.income_statement
        variances = pd.DataFrame(index=budget_is.index, columns=["Budget", "Actual", "Variance", "Variance %"])

        for line in budget_is.index:
            if line in actuals.index:
                bud = budget_is.loc[line].iloc[-1]  # Latest period
                act = actuals.loc[line].iloc[-1]
                var = act - bud
                var_pct = var / bud if bud != 0 else 0
                variances.loc[line] = [bud, act, var, var_pct]

        return variances