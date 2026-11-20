"""
Three-Statement Integrated Financial Model.

Sign conventions (FAST style)
-----------------------------
* Income statement: revenue positive, every expense line negative, subtotals are
  plain sums of their children.
* Balance sheet: assets, liabilities and equity positive; accumulated depreciation
  is a negative contra-asset.
* Cash flow: inflows positive, outflows negative.
* Percentage / magnitude drivers (``cogs_pct``, ``capex``, ``sbc``, ``dividends`` ...)
  may be entered as positive or negative numbers; the model applies the sign.

Opening balance sheet
---------------------
The period-0 values of the drivers listed under ``opening_balances`` (``beginning_cash``,
``ppe_gross``, ``intangibles``, ``goodwill``, ``long_term_debt``, ``current_debt``, ``dlt``,
``common_stock``, ``apic``) form the opening balance sheet. Working-capital balances start
from zero. Opening retained earnings is whatever makes that opening balance sheet balance,
so the cash flow statement ties in every period, including the first.

Structure
---------
Every line, its order, rule or formula, and the opening balance sheet are data:
``models/definitions/three-statement.yaml`` (``structure``, ``opening_balances``,
``opening_retained_earnings``, ``expressions``). This class only names the engine.
"""

from __future__ import annotations

from pathlib import Path

from financial_model.core.line_item import Driver
from financial_model.core.model import FinancialModel
from financial_model.core.statement import StatementType

BS = StatementType.BALANCE_SHEET.value
IS = StatementType.INCOME_STATEMENT.value
CF = StatementType.CASH_FLOW.value


def _dv(drivers: dict[str, Driver], name: str, idx: int, default: float = 0.0) -> float:
    driver = drivers.get(name)
    return driver.get_value(idx) if driver is not None else default


class ThreeStatementModel(FinancialModel):
    """
    Three-statement model whose standard line items come from its definition's ``structure``.
    Extensible via YAML config, definitions or the programmatic API.
    """

    model_type = "three-statement"

    @property
    def OPENING_LINES(self) -> dict[str, str]:  # noqa: N802 - kept for code written against the old constant
        return self.opening_lines()

    @classmethod
    def from_yaml(cls, path: str | Path) -> ThreeStatementModel:
        """Load model from YAML configuration (YAML drivers overlay the defaults)."""
        model = super().from_yaml(path)
        assert isinstance(model, ThreeStatementModel)
        return model
