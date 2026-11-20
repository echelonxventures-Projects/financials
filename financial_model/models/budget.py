"""
Budget / Forecast Driver Model.
Bottom-up operating model with driver trees (ARR roll-forward, headcount x cost).
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from financial_model.core.assumptions import Assumptions
from financial_model.core.definitions import DefinitionError, load_definition
from financial_model.core.line_item import Driver, LineItem
from financial_model.core.model import FinancialModel
from financial_model.core.period import PeriodIndex
from financial_model.core.statement import StatementType

IS = StatementType.INCOME_STATEMENT.value
BS = StatementType.BALANCE_SHEET.value
CF = StatementType.CASH_FLOW.value


def department_key(name: str, keys: dict[str, str] | None = None) -> str:
    """Driver prefix for a department name: 'G&A' -> 'gna', 'Customer Success' -> 'customer_success'.

    Special names come from the budget definition's ``department_keys``.
    """
    if keys is None:
        keys = load_definition("budget", missing_ok=True).get("department_keys") or {}
    lowered = name.strip().lower()
    if lowered in keys:
        return str(keys[lowered])
    return re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")


def _fill(value: Any, dept: str, key: str, quoted: bool = False) -> Any:
    """Replace ``{dept}`` / ``{key}`` placeholders (escaped for use inside a formula's quotes)."""
    if isinstance(value, str):
        name = dept.replace("\\", "\\\\").replace("'", "\\'") if quoted else dept
        return value.replace("{dept}", name).replace("{key}", key)
    if isinstance(value, list):
        return [_fill(v, dept, key, quoted) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, dept, key, quoted) for k, v in value.items()}
    return value


class BudgetModel(FinancialModel):
    """
    Driver-based budget/forecast model.
    Supports:
    - Revenue drivers (ARR roll-forward: starting + new + expansion - churn)
    - Expense drivers (headcount x fully loaded cost per department)
    - Department-level rollups
    - Scenario management (base, upside, downside)
    - Variance analysis vs actuals

    The fixed lines are the definition's ``structure``; each department's lines come from
    its ``department_template`` (models/definitions/budget.yaml).
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
        for dept in self.departments:
            self._add_department_lines(dept)
        self._update_rollup()

    # ------------------------------------------------------------------ #
    def department_key(self, name: str) -> str:
        return department_key(name, self.setting("department_keys") or {})

    def _dept_items(self, dept: str, headcount: Any = None, cost: Any = None) -> list[LineItem]:
        key = self.department_key(dept)
        seeds = self.setting("department_defaults", {})
        template = self.setting("department_template") or {}
        given = {"headcount": headcount, "cost_per_head": cost}

        def driver(suffix: str) -> Driver:
            name = f"{key}_{suffix}"
            seed = seeds.get(suffix) or {}
            label = f"{dept} {seed.get('label', suffix)}"
            if given.get(suffix) is not None:
                return Driver(name, given[suffix], label, seed.get("unit", ""))
            if name in self._default_drivers:
                return self.ref(name)
            if "value" not in seed:
                raise DefinitionError(f"budget: no default for '{name}' and no department_defaults.{suffix}")
            return Driver(name, seed["value"], label, seed.get("unit", ""))

        position = self.departments.index(dept) if dept in self.departments else len(self.departments)
        base_order = float(template.get("order_start", 0)) + position * float(template.get("order_step", 1))
        items = []
        for line_name, raw in (template.get("lines") or {}).items():
            spec = {k: _fill(v, dept, key, quoted=k == "formula") for k, v in dict(raw).items() if k != "format"}
            if "format" in raw:
                spec["format"] = raw["format"]
            statement = spec.pop("statement", IS)
            order = base_order + float(spec.pop("offset", 0))
            spec["order"] = int(order) if order.is_integer() else order
            if isinstance(spec.get("driver"), str):
                spec["driver"] = driver(spec["driver"])
            items.append(self._line_from_spec(statement, _fill(line_name, dept, key), spec, None))
        return items

    def _add_department_lines(self, dept: str, headcount: Any = None, cost: Any = None) -> None:
        statements = {IS: self.income_statement, BS: self.balance_sheet, CF: self.cash_flow}
        for item in self._dept_items(dept, headcount, cost):
            statements[item.statement_type].add_line_item(item)

    def _update_rollup(self) -> None:
        template = self.setting("department_template") or {}
        rollup = self.income_statement.get_line_item(str(template.get("rollup", "")))
        if rollup is not None and template.get("rollup_line"):
            rollup.children = [_fill(template["rollup_line"], d, self.department_key(d)) for d in self.departments]

    def add_department(self, name: str, headcount: list[float], cost_per_head: float) -> BudgetModel:
        """Add a new department to the budget."""
        if name not in self.departments:
            self.departments.append(name)
        self.headcount_by_dept[name] = list(headcount)
        self.cost_per_head[name] = cost_per_head

        key = self.department_key(name)
        seeds = self.setting("department_defaults", {})
        for suffix, value in (("headcount", list(headcount)), ("cost_per_head", cost_per_head)):
            seed = seeds.get(suffix) or {}
            self.assumptions.set_driver(
                Driver(f"{key}_{suffix}", value, f"{name} {seed.get('label', suffix)}", seed.get("unit", ""))
            )
        self._add_department_lines(name, headcount=list(headcount), cost=cost_per_head)
        self._update_rollup()
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
