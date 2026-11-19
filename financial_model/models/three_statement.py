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
The period-0 values of ``beginning_cash``, ``ppe_gross``, ``intangibles``,
``goodwill``, ``long_term_debt``, ``current_debt``, ``dlt``, ``common_stock`` and
``apic`` form the opening balance sheet. Working-capital balances start from zero.
Opening retained earnings is whatever makes that opening balance sheet balance,
so the cash flow statement ties in every period, including the first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from financial_model.core.assumptions import Assumptions
from financial_model.core.line_item import (
    CalculationRule,
    Driver,
    LineItem,
    LineItemType,
    lookup_value,
    previous_value,
)
from financial_model.core.model import FinancialModel
from financial_model.core.period import PeriodIndex

BS = "balance_sheet"
IS = "income_statement"
CF = "cash_flow"


def _dv(drivers: dict[str, Driver], name: str, idx: int, default: float = 0.0) -> float:
    driver = drivers.get(name)
    return driver.get_value(idx) if driver is not None else default


class ThreeStatementModel(FinancialModel):
    """
    Pre-configured three-statement model with standard line items.
    Extensible via YAML config or programmatic API.
    """

    model_type = "three-statement"

    OPENING_LINES = {
        "Cash & Cash Equivalents": "beginning_cash",
        "Property Plant & Equipment, Gross": "ppe_gross",
        "Intangible Assets": "intangibles",
        "Goodwill": "goodwill",
        "Current Portion of Long-Term Debt": "current_debt",
        "Long-Term Debt": "long_term_debt",
        "Deferred Tax Liability": "dlt",
        "Common Stock": "common_stock",
        "Additional Paid-In Capital": "apic",
    }

    def __init__(
        self,
        name: str = "Three Statement Model",
        periods: PeriodIndex | None = None,
        assumptions: Assumptions | None = None,
    ):
        super().__init__(name=name, periods=periods, assumptions=assumptions)
        self._setup_default_assumptions()
        self._setup_default_structure()

    # ------------------------------------------------------------------ #
    # Opening balance sheet
    # ------------------------------------------------------------------ #
    def opening_balance(self, line: str, drivers: dict[str, Driver]) -> float:
        driver_name = self.OPENING_LINES.get(line)
        if driver_name is None:
            return 0.0
        return _dv(drivers, driver_name, 0)

    def opening_retained_earnings(self, drivers: dict[str, Driver]) -> float:
        ob = {line: self.opening_balance(line, drivers) for line in self.OPENING_LINES}
        assets = (
            ob["Cash & Cash Equivalents"]
            + ob["Property Plant & Equipment, Gross"]
            + ob["Intangible Assets"]
            + ob["Goodwill"]
        )
        liabilities = ob["Current Portion of Long-Term Debt"] + ob["Long-Term Debt"] + ob["Deferred Tax Liability"]
        paid_in = ob["Common Stock"] + ob["Additional Paid-In Capital"]
        return assets - liabilities - paid_in

    # ------------------------------------------------------------------ #
    # Helpers shared by formulas
    # ------------------------------------------------------------------ #
    def ppe_gross(self, drivers: dict[str, Driver], idx: int) -> float:
        """Gross PP&E = opening gross PP&E + cumulative capex through period ``idx``."""
        gross = self.opening_balance("Property Plant & Equipment, Gross", drivers)
        for k in range(idx + 1):
            gross += abs(_dv(drivers, "capex", k))
        return gross

    def depreciation(self, drivers: dict[str, Driver], idx: int) -> float:
        """D&A charge (positive magnitude) for period ``idx``."""
        return abs(_dv(drivers, "depreciation_rate", idx)) * self.ppe_gross(drivers, idx)

    def _delta(self, df: pd.DataFrame, line: str, idx: int, drivers: dict[str, Driver], statement: str = BS) -> float:
        current = lookup_value(df, line, idx, statement=statement)
        prev = previous_value(df, line, idx, opening=self.opening_balance(line, drivers), statement=statement)
        return current - prev

    # ------------------------------------------------------------------ #
    # Structure
    # ------------------------------------------------------------------ #
    def _setup_default_structure(self) -> None:
        self._setup_income_statement()
        self._setup_balance_sheet()
        self._setup_cash_flow()

    def _setup_income_statement(self) -> None:
        items = [
            LineItem(
                "Revenue",
                LineItemType.CALCULATED,
                IS,
                order=10,
                unit="$",
                calculation_rule=CalculationRule.GROWTH_RATE,
                calculation_params={"driver": "revenue_growth", "base_line": "Revenue"},
                driver=Driver("revenue", [10_000_000], "Starting revenue", "$"),
                is_total=True,
                description="Total revenue",
            ),
            LineItem(
                "Revenue Growth",
                LineItemType.INPUT,
                IS,
                order=11,
                unit="%",
                format="{:.1%}",
                driver=Driver("revenue_growth", [0.20], "YoY revenue growth", "%"),
                description="Revenue growth rate",
            ),
            LineItem(
                "COGS",
                LineItemType.CALCULATED,
                IS,
                order=20,
                unit="$",
                calculation_rule=CalculationRule.PCT_OF_REVENUE,
                calculation_params={"driver": "cogs_pct", "sign": -1},
                driver=Driver("cogs_pct", [0.35], "COGS as % of revenue", "%"),
                description="Cost of goods sold",
            ),
            LineItem(
                "Gross Profit",
                LineItemType.CALCULATED,
                IS,
                order=30,
                unit="$",
                calculation_rule=CalculationRule.SUM_CHILDREN,
                children=["Revenue", "COGS"],
                is_total=True,
                description="Gross profit",
            ),
            LineItem(
                "R&D Expense",
                LineItemType.CALCULATED,
                IS,
                order=40,
                unit="$",
                calculation_rule=CalculationRule.PCT_OF_REVENUE,
                calculation_params={"driver": "rd_pct", "sign": -1},
                driver=Driver("rd_pct", [0.15], "R&D as % of revenue", "%"),
                description="Research & development",
            ),
            LineItem(
                "Sales & Marketing Expense",
                LineItemType.CALCULATED,
                IS,
                order=41,
                unit="$",
                calculation_rule=CalculationRule.PCT_OF_REVENUE,
                calculation_params={"driver": "sm_pct", "sign": -1},
                driver=Driver("sm_pct", [0.25], "S&M as % of revenue", "%"),
                description="Sales & marketing",
            ),
            LineItem(
                "G&A Expense",
                LineItemType.CALCULATED,
                IS,
                order=42,
                unit="$",
                calculation_rule=CalculationRule.PCT_OF_REVENUE,
                calculation_params={"driver": "gna_pct", "sign": -1},
                driver=Driver("gna_pct", [0.10], "G&A as % of revenue", "%"),
                description="General & administrative",
            ),
            LineItem(
                "Stock-Based Compensation",
                LineItemType.CALCULATED,
                IS,
                order=43,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=lambda df, idx, drivers: -abs(_dv(drivers, "sbc", idx)),
                driver=Driver("sbc", [0], "Stock-based compensation", "$"),
                description="Non-cash SBC expense",
            ),
            LineItem(
                "Depreciation & Amortization",
                LineItemType.CALCULATED,
                IS,
                order=44,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=lambda df, idx, drivers: -self.depreciation(drivers, idx),
                driver=Driver("depreciation_rate", [0.10], "Depreciation rate on gross PP&E", "%"),
                description="Depreciation & amortization expense",
            ),
            LineItem(
                "Total Operating Expenses",
                LineItemType.AGGREGATE,
                IS,
                order=45,
                unit="$",
                children=[
                    "R&D Expense",
                    "Sales & Marketing Expense",
                    "G&A Expense",
                    "Stock-Based Compensation",
                    "Depreciation & Amortization",
                ],
                is_total=True,
                description="Total operating expenses",
            ),
            LineItem(
                "Operating Income",
                LineItemType.CALCULATED,
                IS,
                order=50,
                unit="$",
                calculation_rule=CalculationRule.SUM_CHILDREN,
                children=["Gross Profit", "Total Operating Expenses"],
                is_total=True,
                description="Operating income (EBIT)",
            ),
            LineItem(
                "Interest Expense",
                LineItemType.CALCULATED,
                IS,
                order=60,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=lambda df, idx, drivers: -abs(_dv(drivers, "interest_expense", idx)),
                driver=Driver("interest_expense", [0], "Interest expense", "$"),
                description="Interest expense",
            ),
            LineItem(
                "Other Income (Expense)",
                LineItemType.INPUT,
                IS,
                order=61,
                unit="$",
                driver=Driver("other_income", [0], "Other income/expense", "$"),
                description="Other non-operating items",
            ),
            LineItem(
                "Pre-Tax Income",
                LineItemType.CALCULATED,
                IS,
                order=70,
                unit="$",
                calculation_rule=CalculationRule.SUM_CHILDREN,
                children=["Operating Income", "Interest Expense", "Other Income (Expense)"],
                is_total=True,
                description="Pre-tax income",
            ),
            LineItem(
                "Income Tax Expense",
                LineItemType.CALCULATED,
                IS,
                order=80,
                unit="$",
                calculation_rule=CalculationRule.PCT_OF_LINE,
                calculation_params={"base_line": "Pre-Tax Income", "driver": "tax_rate", "sign": -1},
                driver=Driver("tax_rate", [0.21], "Effective tax rate", "%"),
                description="Income tax expense (benefit when pre-tax income is negative)",
            ),
            LineItem(
                "Net Income",
                LineItemType.CALCULATED,
                IS,
                order=90,
                unit="$",
                calculation_rule=CalculationRule.SUM_CHILDREN,
                children=["Pre-Tax Income", "Income Tax Expense"],
                is_total=True,
                description="Net income",
            ),
        ]
        for item in items:
            self.add_income_statement_item(item)

    def _setup_balance_sheet(self) -> None:
        def accumulated_depreciation(df: pd.DataFrame, idx: int, drivers: dict[str, Driver]) -> float:
            prev = previous_value(df, "Accumulated Depreciation", idx, opening=0.0, statement=BS)
            return prev - self.depreciation(drivers, idx)

        def goodwill(df: pd.DataFrame, idx: int, drivers: dict[str, Driver]) -> float:  # noqa: ARG001
            value = self.opening_balance("Goodwill", drivers)
            for k in range(idx + 1):
                value += abs(_dv(drivers, "acquisitions", k))
            return value

        def apic(df: pd.DataFrame, idx: int, drivers: dict[str, Driver]) -> float:  # noqa: ARG001
            value = self.opening_balance("Additional Paid-In Capital", drivers)
            for k in range(idx + 1):
                value += abs(_dv(drivers, "sbc", k)) + _dv(drivers, "equity_issuance", k)
            return value

        items = [
            LineItem(
                "Cash & Cash Equivalents",
                LineItemType.PLUG,
                BS,
                order=10,
                unit="$",
                description="Cash (plug: balances the balance sheet, floored at min_cash)",
            ),
            LineItem(
                "Accounts Receivable",
                LineItemType.CALCULATED,
                BS,
                order=20,
                unit="$",
                calculation_rule=CalculationRule.DAYS_OUTSTANDING,
                calculation_params={"driver": "ar_days", "base_line": "Revenue"},
                driver=Driver("ar_days", [45], "Accounts receivable days", "days"),
                description="Accounts receivable",
            ),
            LineItem(
                "Inventory",
                LineItemType.CALCULATED,
                BS,
                order=30,
                unit="$",
                calculation_rule=CalculationRule.DAYS_OUTSTANDING,
                calculation_params={"driver": "inventory_days", "base_line": "COGS"},
                driver=Driver("inventory_days", [0], "Inventory days", "days"),
                description="Inventory",
            ),
            LineItem(
                "Prepaid Expenses",
                LineItemType.CALCULATED,
                BS,
                order=40,
                unit="$",
                calculation_rule=CalculationRule.PCT_OF_REVENUE,
                calculation_params={"driver": "prepaid_pct"},
                driver=Driver("prepaid_pct", [0.02], "Prepaid as % of revenue", "%"),
                description="Prepaid expenses",
            ),
            LineItem(
                "Total Current Assets",
                LineItemType.AGGREGATE,
                BS,
                order=50,
                unit="$",
                children=["Cash & Cash Equivalents", "Accounts Receivable", "Inventory", "Prepaid Expenses"],
                is_total=True,
                description="Total current assets",
            ),
            LineItem(
                "Property Plant & Equipment, Gross",
                LineItemType.CALCULATED,
                BS,
                order=60,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=lambda df, idx, drivers: self.ppe_gross(drivers, idx),
                driver=Driver("ppe_gross", [1_000_000], "Opening gross PP&E", "$"),
                description="Gross PP&E (opening + cumulative capex)",
            ),
            LineItem(
                "Accumulated Depreciation",
                LineItemType.CALCULATED,
                BS,
                order=63,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=accumulated_depreciation,
                description="Accumulated depreciation (contra-asset, negative)",
            ),
            LineItem(
                "Property Plant & Equipment, Net",
                LineItemType.CALCULATED,
                BS,
                order=70,
                unit="$",
                calculation_rule=CalculationRule.SUM_CHILDREN,
                children=["Property Plant & Equipment, Gross", "Accumulated Depreciation"],
                description="Net PP&E",
            ),
            LineItem(
                "Intangible Assets",
                LineItemType.INPUT,
                BS,
                order=80,
                unit="$",
                driver=Driver("intangibles", [0], "Intangible assets", "$"),
                description="Intangible assets",
            ),
            LineItem(
                "Goodwill",
                LineItemType.CALCULATED,
                BS,
                order=90,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=goodwill,
                driver=Driver("goodwill", [0], "Opening goodwill", "$"),
                description="Goodwill (opening + cumulative acquisitions)",
            ),
            LineItem(
                "Total Assets",
                LineItemType.AGGREGATE,
                BS,
                order=100,
                unit="$",
                children=["Total Current Assets", "Property Plant & Equipment, Net", "Intangible Assets", "Goodwill"],
                is_total=True,
                description="Total assets",
            ),
            LineItem(
                "Accounts Payable",
                LineItemType.CALCULATED,
                BS,
                order=110,
                unit="$",
                calculation_rule=CalculationRule.DAYS_OUTSTANDING,
                calculation_params={"driver": "ap_days", "base_line": "COGS"},
                driver=Driver("ap_days", [60], "Accounts payable days", "days"),
                description="Accounts payable",
            ),
            LineItem(
                "Accrued Expenses",
                LineItemType.CALCULATED,
                BS,
                order=120,
                unit="$",
                calculation_rule=CalculationRule.PCT_OF_REVENUE,
                calculation_params={"driver": "accrued_pct"},
                driver=Driver("accrued_pct", [0.05], "Accrued as % of revenue", "%"),
                description="Accrued expenses",
            ),
            LineItem(
                "Deferred Revenue",
                LineItemType.CALCULATED,
                BS,
                order=130,
                unit="$",
                calculation_rule=CalculationRule.PCT_OF_REVENUE,
                calculation_params={"driver": "deferred_rev_pct"},
                driver=Driver("deferred_rev_pct", [0.15], "Deferred revenue as % of revenue", "%"),
                description="Deferred revenue",
            ),
            LineItem(
                "Current Portion of Long-Term Debt",
                LineItemType.INPUT,
                BS,
                order=140,
                unit="$",
                driver=Driver("current_debt", [0], "Current portion of debt", "$"),
                description="Current debt portion",
            ),
            LineItem(
                "Total Current Liabilities",
                LineItemType.AGGREGATE,
                BS,
                order=150,
                unit="$",
                children=[
                    "Accounts Payable",
                    "Accrued Expenses",
                    "Deferred Revenue",
                    "Current Portion of Long-Term Debt",
                ],
                is_total=True,
                description="Total current liabilities",
            ),
            LineItem(
                "Revolver",
                LineItemType.PLUG,
                BS,
                order=160,
                unit="$",
                description="Revolver (plug: drawn when cash would fall below min_cash)",
            ),
            LineItem(
                "Long-Term Debt",
                LineItemType.INPUT,
                BS,
                order=170,
                unit="$",
                driver=Driver("long_term_debt", [0], "Long-term debt", "$"),
                description="Long-term debt",
            ),
            LineItem(
                "Deferred Tax Liability",
                LineItemType.INPUT,
                BS,
                order=180,
                unit="$",
                driver=Driver("dlt", [0], "Deferred tax liability", "$"),
                description="Deferred tax liability",
            ),
            LineItem(
                "Total Liabilities",
                LineItemType.AGGREGATE,
                BS,
                order=190,
                unit="$",
                children=["Total Current Liabilities", "Revolver", "Long-Term Debt", "Deferred Tax Liability"],
                is_total=True,
                description="Total liabilities",
            ),
            LineItem(
                "Common Stock",
                LineItemType.CALCULATED,
                BS,
                order=200,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=lambda df, idx, drivers: self.opening_balance("Common Stock", drivers),
                driver=Driver("common_stock", [1_000], "Common stock (par)", "$"),
                description="Common stock",
            ),
            LineItem(
                "Additional Paid-In Capital",
                LineItemType.CALCULATED,
                BS,
                order=210,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=apic,
                driver=Driver("apic", [5_000_000], "Opening APIC", "$"),
                description="APIC (opening + cumulative SBC and equity issuance)",
            ),
            LineItem(
                "Retained Earnings",
                LineItemType.PLUG,
                BS,
                order=220,
                unit="$",
                description="Retained earnings (opening RE + cumulative net income - dividends)",
            ),
            LineItem(
                "Total Equity",
                LineItemType.AGGREGATE,
                BS,
                order=230,
                unit="$",
                children=["Common Stock", "Additional Paid-In Capital", "Retained Earnings"],
                is_total=True,
                description="Total equity",
            ),
        ]
        for item in items:
            self.add_balance_sheet_item(item)

    def _setup_cash_flow(self) -> None:
        def wc_change(line: str, sign: float) -> Any:
            return lambda df, idx, drivers: sign * self._delta(df, line, idx, drivers)

        def debt_change(df: pd.DataFrame, idx: int, drivers: dict[str, Driver]) -> float:
            return self._delta(df, "Long-Term Debt", idx, drivers) + self._delta(
                df, "Current Portion of Long-Term Debt", idx, drivers
            )

        items = [
            LineItem(
                "Net Income",
                LineItemType.LINK,
                CF,
                order=10,
                unit="$",
                calculation_params={"source_line": "Net Income", "source_statement": IS},
                description="Net income from IS",
            ),
            LineItem(
                "Depreciation & Amortization",
                LineItemType.LINK,
                CF,
                order=20,
                unit="$",
                calculation_params={"source_line": "Depreciation & Amortization", "source_statement": IS, "sign": -1},
                description="D&A add-back",
            ),
            LineItem(
                "Stock-Based Compensation",
                LineItemType.LINK,
                CF,
                order=30,
                unit="$",
                calculation_params={"source_line": "Stock-Based Compensation", "source_statement": IS, "sign": -1},
                description="SBC add-back",
            ),
            LineItem(
                "Change in Accounts Receivable",
                LineItemType.CALCULATED,
                CF,
                order=40,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=wc_change("Accounts Receivable", -1),
                description="(Increase) decrease in AR",
            ),
            LineItem(
                "Change in Inventory",
                LineItemType.CALCULATED,
                CF,
                order=41,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=wc_change("Inventory", -1),
                description="(Increase) decrease in inventory",
            ),
            LineItem(
                "Change in Prepaid Expenses",
                LineItemType.CALCULATED,
                CF,
                order=42,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=wc_change("Prepaid Expenses", -1),
                description="(Increase) decrease in prepaid",
            ),
            LineItem(
                "Change in Accounts Payable",
                LineItemType.CALCULATED,
                CF,
                order=50,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=wc_change("Accounts Payable", 1),
                description="Increase (decrease) in AP",
            ),
            LineItem(
                "Change in Accrued Expenses",
                LineItemType.CALCULATED,
                CF,
                order=51,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=wc_change("Accrued Expenses", 1),
                description="Increase (decrease) in accrued",
            ),
            LineItem(
                "Change in Deferred Revenue",
                LineItemType.CALCULATED,
                CF,
                order=52,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=wc_change("Deferred Revenue", 1),
                description="Increase (decrease) in deferred revenue",
            ),
            LineItem(
                "Change in Deferred Taxes",
                LineItemType.CALCULATED,
                CF,
                order=53,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=wc_change("Deferred Tax Liability", 1),
                description="Increase (decrease) in deferred tax liability",
            ),
            LineItem(
                "Operating Cash Flow",
                LineItemType.AGGREGATE,
                CF,
                order=60,
                unit="$",
                children=[
                    "Net Income",
                    "Depreciation & Amortization",
                    "Stock-Based Compensation",
                    "Change in Accounts Receivable",
                    "Change in Inventory",
                    "Change in Prepaid Expenses",
                    "Change in Accounts Payable",
                    "Change in Accrued Expenses",
                    "Change in Deferred Revenue",
                    "Change in Deferred Taxes",
                ],
                is_total=True,
                description="Cash from operations",
            ),
            LineItem(
                "Capital Expenditures",
                LineItemType.CALCULATED,
                CF,
                order=70,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=lambda df, idx, drivers: -abs(_dv(drivers, "capex", idx)),
                driver=Driver("capex", [-500_000], "Annual CapEx", "$"),
                description="Capital expenditures",
            ),
            LineItem(
                "Acquisitions",
                LineItemType.CALCULATED,
                CF,
                order=80,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=lambda df, idx, drivers: -abs(_dv(drivers, "acquisitions", idx)),
                driver=Driver("acquisitions", [0], "Acquisitions", "$"),
                description="Acquisitions (added to goodwill)",
            ),
            LineItem(
                "Change in Intangible Assets",
                LineItemType.CALCULATED,
                CF,
                order=81,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=wc_change("Intangible Assets", -1),
                description="(Increase) decrease in intangibles",
            ),
            LineItem(
                "Investing Cash Flow",
                LineItemType.AGGREGATE,
                CF,
                order=90,
                unit="$",
                children=["Capital Expenditures", "Acquisitions", "Change in Intangible Assets"],
                is_total=True,
                description="Cash from investing",
            ),
            LineItem(
                "Debt Issuance (Repayment)",
                LineItemType.CALCULATED,
                CF,
                order=100,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=debt_change,
                description="Net change in term debt",
            ),
            LineItem(
                "Revolver Draw (Repayment)",
                LineItemType.CALCULATED,
                CF,
                order=101,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=wc_change("Revolver", 1),
                description="Net revolver draw",
            ),
            LineItem(
                "Equity Issuance (Repurchase)",
                LineItemType.CALCULATED,
                CF,
                order=110,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=lambda df, idx, drivers: _dv(drivers, "equity_issuance", idx),
                driver=Driver("equity_issuance", [0], "Equity issuance (+) / repurchase (-)", "$"),
                description="Equity financing",
            ),
            LineItem(
                "Dividends Paid",
                LineItemType.CALCULATED,
                CF,
                order=120,
                unit="$",
                calculation_rule=CalculationRule.CUSTOM,
                custom_formula=lambda df, idx, drivers: -abs(_dv(drivers, "dividends", idx)),
                driver=Driver("dividends", [0], "Dividends paid", "$"),
                description="Dividends",
            ),
            LineItem(
                "Financing Cash Flow",
                LineItemType.AGGREGATE,
                CF,
                order=130,
                unit="$",
                children=[
                    "Debt Issuance (Repayment)",
                    "Revolver Draw (Repayment)",
                    "Equity Issuance (Repurchase)",
                    "Dividends Paid",
                ],
                is_total=True,
                description="Cash from financing",
            ),
            LineItem(
                "Net Change in Cash",
                LineItemType.PLUG,
                CF,
                order=140,
                unit="$",
                description="Net change in cash (ties to balance sheet)",
            ),
            LineItem(
                "Beginning Cash Balance",
                LineItemType.INPUT,
                CF,
                order=150,
                unit="$",
                driver=Driver("beginning_cash", [500_000], "Opening cash", "$"),
                description="Beginning cash",
            ),
            LineItem(
                "Ending Cash Balance",
                LineItemType.AGGREGATE,
                CF,
                order=160,
                unit="$",
                children=["Beginning Cash Balance", "Net Change in Cash"],
                is_total=True,
                description="Ending cash",
            ),
        ]
        for item in items:
            self.add_cash_flow_item(item)

    def _setup_default_assumptions(self) -> None:
        """Set default drivers for the model (overridden by YAML / API config)."""
        default_drivers = [
            Driver("revenue", [10_000_000], "Starting revenue", "$"),
            Driver("revenue_growth", [0.20], "YoY revenue growth", "%"),
            Driver("cogs_pct", [0.35], "COGS as % of revenue", "%"),
            Driver("rd_pct", [0.15], "R&D as % of revenue", "%"),
            Driver("sm_pct", [0.25], "S&M as % of revenue", "%"),
            Driver("gna_pct", [0.10], "G&A as % of revenue", "%"),
            Driver("sbc", [0], "Stock-based compensation", "$"),
            Driver("tax_rate", [0.21], "Effective tax rate", "%"),
            Driver("interest_expense", [0], "Interest expense (non-modelled debt)", "$"),
            Driver("other_income", [0], "Other income/expense", "$"),
            Driver("ar_days", [45], "Accounts receivable days", "days"),
            Driver("inventory_days", [0], "Inventory days", "days"),
            Driver("prepaid_pct", [0.02], "Prepaid as % of revenue", "%"),
            Driver("ppe_gross", [1_000_000], "Opening gross PP&E", "$"),
            Driver("capex", [-500_000], "Annual CapEx", "$"),
            Driver("depreciation_rate", [0.10], "Depreciation rate on gross PP&E", "%"),
            Driver("intangibles", [0], "Intangible assets", "$"),
            Driver("goodwill", [0], "Opening goodwill", "$"),
            Driver("acquisitions", [0], "Acquisitions", "$"),
            Driver("ap_days", [60], "Accounts payable days", "days"),
            Driver("accrued_pct", [0.05], "Accrued as % of revenue", "%"),
            Driver("deferred_rev_pct", [0.15], "Deferred revenue as % of revenue", "%"),
            Driver("current_debt", [0], "Current portion of debt", "$"),
            Driver("long_term_debt", [0], "Long-term debt", "$"),
            Driver("dlt", [0], "Deferred tax liability", "$"),
            Driver("common_stock", [1_000], "Common stock (par)", "$"),
            Driver("apic", [5_000_000], "Opening APIC", "$"),
            Driver("equity_issuance", [0], "Equity issuance (+) / repurchase (-)", "$"),
            Driver("dividends", [0], "Dividends paid", "$"),
            Driver("beginning_cash", [500_000], "Opening cash", "$"),
            Driver("min_cash", [0], "Minimum cash balance before drawing the revolver", "$"),
            Driver("shares_outstanding", [1_000_000], "Diluted shares outstanding", "#"),
        ]
        for driver in default_drivers:
            self.assumptions.set_driver(driver)

    @classmethod
    def from_yaml(cls, path: str | Path) -> ThreeStatementModel:
        """Load model from YAML configuration (YAML drivers overlay the defaults)."""
        model = super().from_yaml(path)
        assert isinstance(model, ThreeStatementModel)
        return model
