"""
Three-Statement Integrated Financial Model.
Core model with configurable line items, drivers, and balancing.
"""

from dataclasses import dataclass, field
from typing import Any
import pandas as pd
import numpy as np

from financial_model.core.model import FinancialModel, ModelResult
from financial_model.core.statement import Statement, StatementType
from financial_model.core.line_item import LineItem, LineItemType, CalculationRule, Driver
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.core.assumptions import Assumptions
from financial_model.core.balancer import BalanceSheetBalancer, CashFlowBalancer


class ThreeStatementModel(FinancialModel):
    """
    Pre-configured three-statement model with standard line items.
    Extensible via YAML config or programmatic API.
    """

    def __init__(
        self,
        name: str = "Three Statement Model",
        periods: PeriodIndex | None = None,
        assumptions: Assumptions | None = None,
    ):
        super().__init__(name=name, periods=periods, assumptions=assumptions)
        self._setup_default_structure()
        self._setup_default_assumptions()

    def _setup_default_structure(self) -> None:
        """Add standard line items for three statements."""
        self._setup_income_statement()
        self._setup_balance_sheet()
        self._setup_cash_flow()

    def _setup_income_statement(self) -> None:
        items = [
            # Revenue section
            LineItem("Revenue", LineItemType.CALCULATED, "income_statement", order=10, unit="$",
                     calculation_rule=CalculationRule.GROWTH_RATE,
                     calculation_params={"driver": "revenue_growth", "base_line": "Revenue"},
                     driver=Driver("revenue", [10_000_000], "Starting revenue", "$"),
                     is_total=True,
                     description="Total revenue"),
            LineItem("Revenue Growth", LineItemType.INPUT, "income_statement", order=11, unit="%",
                     driver=Driver("revenue_growth", [0.20], "YoY revenue growth", "%"),
                     description="Revenue growth rate"),

            # COGS
            LineItem("COGS", LineItemType.CALCULATED, "income_statement", order=20, unit="$",
                     calculation_rule=CalculationRule.PCT_OF_REVENUE,
                     calculation_params={"driver": "cogs_pct"},
                     driver=Driver("cogs_pct", [0.35], "COGS as % of revenue", "%"),
                     description="Cost of goods sold"),

            LineItem("Gross Profit", LineItemType.CALCULATED, "income_statement", order=30, unit="$",
                     calculation_rule=CalculationRule.SUM_CHILDREN,
                     children=["Revenue", "COGS"],
                     is_total=True,
                     description="Gross profit"),

            # Operating Expenses
            LineItem("R&D Expense", LineItemType.CALCULATED, "income_statement", order=40, unit="$",
                     calculation_rule=CalculationRule.PCT_OF_REVENUE,
                     calculation_params={"driver": "rd_pct"},
                     driver=Driver("rd_pct", [0.15], "R&D as % of revenue", "%"),
                     description="Research & development"),

            LineItem("Sales & Marketing Expense", LineItemType.CALCULATED, "income_statement", order=41, unit="$",
                     calculation_rule=CalculationRule.PCT_OF_REVENUE,
                     calculation_params={"driver": "sm_pct"},
                     driver=Driver("sm_pct", [0.25], "S&M as % of revenue", "%"),
                     description="Sales & marketing"),

            LineItem("G&A Expense", LineItemType.CALCULATED, "income_statement", order=42, unit="$",
                     calculation_rule=CalculationRule.PCT_OF_REVENUE,
                     calculation_params={"driver": "gna_pct"},
                     driver=Driver("gna_pct", [0.10], "G&A as % of revenue", "%"),
                     description="General & administrative"),

            LineItem("Total Operating Expenses", LineItemType.AGGREGATE, "income_statement", order=45, unit="$",
                     children=["R&D Expense", "Sales & Marketing Expense", "G&A Expense"],
                     is_total=True,
                     description="Total OpEx"),

            LineItem("Operating Income", LineItemType.CALCULATED, "income_statement", order=50, unit="$",
                     calculation_rule=CalculationRule.SUM_CHILDREN,
                     children=["Gross Profit", "Total Operating Expenses"],
                     is_total=True,
                     description="Operating income (EBIT)"),

            # Below the line
            LineItem("Interest Expense", LineItemType.INPUT, "income_statement", order=60, unit="$",
                     driver=Driver("interest_expense", [0], "Interest expense", "$"),
                     description="Interest expense"),

            LineItem("Other Income (Expense)", LineItemType.INPUT, "income_statement", order=61, unit="$",
                     driver=Driver("other_income", [0], "Other income/expense", "$"),
                     description="Other non-operating items"),

            LineItem("Pre-Tax Income", LineItemType.CALCULATED, "income_statement", order=70, unit="$",
                     calculation_rule=CalculationRule.SUM_CHILDREN,
                     children=["Operating Income", "Interest Expense", "Other Income (Expense)"],
                     is_total=True,
                     description="Pre-tax income"),

            LineItem("Income Tax Expense", LineItemType.CALCULATED, "income_statement", order=80, unit="$",
                     calculation_rule=CalculationRule.PCT_OF_LINE,
                     calculation_params={"base_line": "Pre-Tax Income", "driver": "tax_rate"},
                     driver=Driver("tax_rate", [0.21], "Effective tax rate", "%"),
                     description="Income tax expense"),

            LineItem("Net Income", LineItemType.CALCULATED, "income_statement", order=90, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: (
                                                        (df.loc[("income_statement", "Pre-Tax Income")].iloc[idx] if ("income_statement", "Pre-Tax Income") in df.index else 0)
                                                        - (df.loc[("income_statement", "Income Tax Expense")].iloc[idx] if ("income_statement", "Income Tax Expense") in df.index else 0)
                                                    ),
                     is_total=True,
                     description="Net income"),
        ]
        for item in items:
            self.add_income_statement_item(item)

    def _setup_balance_sheet(self) -> None:
        items = [
            # Current Assets
            LineItem("Cash & Cash Equivalents", LineItemType.PLUG, "balance_sheet", order=10, unit="$",
                     description="Cash (plug)"),
            LineItem("Accounts Receivable", LineItemType.CALCULATED, "balance_sheet", order=20, unit="$",
                     calculation_rule=CalculationRule.DAYS_OUTSTANDING,
                     calculation_params={"driver": "ar_days", "base_line": "Revenue"},
                     driver=Driver("ar_days", [45], "Accounts receivable days", "days"),
                     description="Accounts receivable"),
            LineItem("Inventory", LineItemType.CALCULATED, "balance_sheet", order=30, unit="$",
                     calculation_rule=CalculationRule.DAYS_OUTSTANDING,
                     calculation_params={"driver": "inventory_days", "base_line": "COGS"},
                     driver=Driver("inventory_days", [0], "Inventory days", "days"),
                     description="Inventory"),
            LineItem("Prepaid Expenses", LineItemType.CALCULATED, "balance_sheet", order=40, unit="$",
                     calculation_rule=CalculationRule.PCT_OF_REVENUE,
                     calculation_params={"driver": "prepaid_pct"},
                     driver=Driver("prepaid_pct", [0.02], "Prepaid as % of revenue", "%"),
                     description="Prepaid expenses"),
            LineItem("Total Current Assets", LineItemType.AGGREGATE, "balance_sheet", order=50, unit="$",
                     children=["Cash & Cash Equivalents", "Accounts Receivable", "Inventory", "Prepaid Expenses"],
                     is_total=True,
                     description="Total current assets"),

            # Non-Current Assets
            LineItem("Property Plant & Equipment, Gross", LineItemType.INPUT, "balance_sheet", order=60, unit="$",
                     driver=Driver("ppe_gross", [1_000_000], "Gross PP&E", "$"),
                     description="Gross PP&E"),
            LineItem("Capital Expenditures", LineItemType.INPUT, "balance_sheet", order=61, unit="$",
                     driver=Driver("capex", [-500_000], "Annual CapEx", "$"),
                     description="Capital expenditures"),
            LineItem("Depreciation & Amortization", LineItemType.CALCULATED, "balance_sheet", order=62, unit="$",
                     calculation_rule=CalculationRule.PCT_OF_LINE,
                     calculation_params={"base_line": "Property Plant & Equipment, Gross", "driver": "depreciation_rate"},
                     driver=Driver("depreciation_rate", [0.10], "Depreciation rate", "%"),
                     description="D&A"),
            LineItem("Accumulated Depreciation", LineItemType.CALCULATED, "balance_sheet", order=63, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: (df.loc[("balance_sheet", "Accumulated Depreciation")].iloc[idx-1] if idx > 0 else 0)
                                                    + abs(df.loc[("balance_sheet", "Depreciation & Amortization")].iloc[idx]),
                     description="Accumulated depreciation"),
            LineItem("Property Plant & Equipment, Net", LineItemType.CALCULATED, "balance_sheet", order=70, unit="$",
                     calculation_rule=CalculationRule.SUM_CHILDREN,
                     children=["Property Plant & Equipment, Gross", "Accumulated Depreciation"],
                     description="Net PP&E"),
            LineItem("Intangible Assets", LineItemType.INPUT, "balance_sheet", order=80, unit="$",
                     driver=Driver("intangibles", [0], "Intangible assets", "$"),
                     description="Intangible assets"),
            LineItem("Goodwill", LineItemType.INPUT, "balance_sheet", order=90, unit="$",
                     driver=Driver("goodwill", [0], "Goodwill", "$"),
                     description="Goodwill"),
            LineItem("Total Assets", LineItemType.AGGREGATE, "balance_sheet", order=100, unit="$",
                     children=["Total Current Assets", "Property Plant & Equipment, Net", "Intangible Assets", "Goodwill"],
                     is_total=True,
                     description="Total assets"),

            # Current Liabilities
            LineItem("Accounts Payable", LineItemType.CALCULATED, "balance_sheet", order=110, unit="$",
                     calculation_rule=CalculationRule.DAYS_OUTSTANDING,
                     calculation_params={"driver": "ap_days", "base_line": "COGS"},
                     driver=Driver("ap_days", [60], "Accounts payable days", "days"),
                     description="Accounts payable"),
            LineItem("Accrued Expenses", LineItemType.CALCULATED, "balance_sheet", order=120, unit="$",
                     calculation_rule=CalculationRule.PCT_OF_REVENUE,
                     calculation_params={"driver": "accrued_pct"},
                     driver=Driver("accrued_pct", [0.05], "Accrued as % of revenue", "%"),
                     description="Accrued expenses"),
            LineItem("Deferred Revenue", LineItemType.CALCULATED, "balance_sheet", order=130, unit="$",
                     calculation_rule=CalculationRule.PCT_OF_REVENUE,
                     calculation_params={"driver": "deferred_rev_pct"},
                     driver=Driver("deferred_rev_pct", [0.15], "Deferred revenue as % of revenue", "%"),
                     description="Deferred revenue"),
            LineItem("Current Portion of Long-Term Debt", LineItemType.INPUT, "balance_sheet", order=140, unit="$",
                     driver=Driver("current_debt", [0], "Current portion of debt", "$"),
                     description="Current debt portion"),
            LineItem("Total Current Liabilities", LineItemType.AGGREGATE, "balance_sheet", order=150, unit="$",
                     children=["Accounts Payable", "Accrued Expenses", "Deferred Revenue", "Current Portion of Long-Term Debt"],
                     is_total=True,
                     description="Total current liabilities"),

            # Non-Current Liabilities
            LineItem("Revolver", LineItemType.PLUG, "balance_sheet", order=160, unit="$",
                     description="Revolver (plug)"),
            LineItem("Long-Term Debt", LineItemType.INPUT, "balance_sheet", order=170, unit="$",
                     driver=Driver("long_term_debt", [0], "Long-term debt", "$"),
                     description="Long-term debt"),
            LineItem("Deferred Tax Liability", LineItemType.INPUT, "balance_sheet", order=180, unit="$",
                     driver=Driver("dlt", [0], "Deferred tax liability", "$"),
                     description="Deferred tax liability"),
            LineItem("Total Liabilities", LineItemType.AGGREGATE, "balance_sheet", order=190, unit="$",
                     children=["Total Current Liabilities", "Revolver", "Long-Term Debt", "Deferred Tax Liability"],
                     is_total=True,
                     description="Total liabilities"),

            # Equity
            LineItem("Common Stock", LineItemType.INPUT, "balance_sheet", order=200, unit="$",
                     driver=Driver("common_stock", [1_000], "Common stock", "$"),
                     description="Common stock"),
            LineItem("Additional Paid-In Capital", LineItemType.INPUT, "balance_sheet", order=210, unit="$",
                     driver=Driver("apic", [5_000_000], "APIC", "$"),
                     description="Additional paid-in capital"),
            LineItem("Retained Earnings", LineItemType.PLUG, "balance_sheet", order=220, unit="$",
                     description="Retained earnings (plug - calculated by balancer)"),
            LineItem("Total Equity", LineItemType.AGGREGATE, "balance_sheet", order=230, unit="$",
                     children=["Common Stock", "Additional Paid-In Capital", "Retained Earnings"],
                     is_total=True,
                     description="Total equity"),
        ]
        for item in items:
            self.add_balance_sheet_item(item)

    def _setup_cash_flow(self) -> None:
        items = [
            # Operating Activities
            LineItem("Net Income", LineItemType.LINK, "cash_flow", order=10, unit="$",
                     calculation_params={"source_line": "Net Income", "source_statement": "income_statement"},
                     description="Net income from IS"),
            LineItem("Depreciation & Amortization", LineItemType.LINK, "cash_flow", order=20, unit="$",
                     calculation_params={"source_line": "Depreciation & Amortization", "source_statement": "balance_sheet"},
                     description="D&A from BS"),
            LineItem("Stock-Based Compensation", LineItemType.INPUT, "cash_flow", order=30, unit="$",
                     driver=Driver("sbc", [0], "Stock-based compensation", "$"),
                     description="SBC expense"),
            LineItem("Change in Accounts Receivable", LineItemType.CALCULATED, "cash_flow", order=40, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: -(df.loc[("balance_sheet", "Accounts Receivable")].iloc[idx] - (df.loc[("balance_sheet", "Accounts Receivable")].iloc[idx-1] if idx > 0 else 0))
                                                    if ("balance_sheet", "Accounts Receivable") in df.index else 0,
                     description="Change in AR"),
            LineItem("Change in Inventory", LineItemType.CALCULATED, "cash_flow", order=41, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: -(df.loc[("balance_sheet", "Inventory")].iloc[idx] - (df.loc[("balance_sheet", "Inventory")].iloc[idx-1] if idx > 0 else 0))
                                                    if ("balance_sheet", "Inventory") in df.index else 0,
                     description="Change in inventory"),
            LineItem("Change in Prepaid Expenses", LineItemType.CALCULATED, "cash_flow", order=42, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: -(df.loc[("balance_sheet", "Prepaid Expenses")].iloc[idx] - (df.loc[("balance_sheet", "Prepaid Expenses")].iloc[idx-1] if idx > 0 else 0))
                                                    if ("balance_sheet", "Prepaid Expenses") in df.index else 0,
                     description="Change in prepaid"),
            LineItem("Change in Accounts Payable", LineItemType.CALCULATED, "cash_flow", order=50, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: (df.loc[("balance_sheet", "Accounts Payable")].iloc[idx] - (df.loc[("balance_sheet", "Accounts Payable")].iloc[idx-1] if idx > 0 else 0))
                                                    if ("balance_sheet", "Accounts Payable") in df.index else 0,
                     description="Change in AP"),
            LineItem("Change in Accrued Expenses", LineItemType.CALCULATED, "cash_flow", order=51, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: (df.loc[("balance_sheet", "Accrued Expenses")].iloc[idx] - (df.loc[("balance_sheet", "Accrued Expenses")].iloc[idx-1] if idx > 0 else 0))
                                                    if ("balance_sheet", "Accrued Expenses") in df.index else 0,
                     description="Change in accrued"),
            LineItem("Change in Deferred Revenue", LineItemType.CALCULATED, "cash_flow", order=52, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: (df.loc[("balance_sheet", "Deferred Revenue")].iloc[idx] - (df.loc[("balance_sheet", "Deferred Revenue")].iloc[idx-1] if idx > 0 else 0))
                                                    if ("balance_sheet", "Deferred Revenue") in df.index else 0,
                     description="Change in deferred revenue"),
            LineItem("Operating Cash Flow", LineItemType.AGGREGATE, "cash_flow", order=60, unit="$",
                     children=["Net Income", "Depreciation & Amortization", "Stock-Based Compensation",
                               "Change in Accounts Receivable", "Change in Inventory", "Change in Prepaid Expenses",
                               "Change in Accounts Payable", "Change in Accrued Expenses", "Change in Deferred Revenue"],
                     is_total=True,
                     description="Cash from operations"),

            # Investing Activities
            LineItem("Capital Expenditures", LineItemType.LINK, "cash_flow", order=70, unit="$",
                     calculation_params={"source_line": "Capital Expenditures", "source_statement": "balance_sheet"},
                     description="CapEx from BS"),
            LineItem("Acquisitions", LineItemType.INPUT, "cash_flow", order=80, unit="$",
                     driver=Driver("acquisitions", [0], "Acquisitions", "$"),
                     description="Acquisitions"),
            LineItem("Investing Cash Flow", LineItemType.AGGREGATE, "cash_flow", order=90, unit="$",
                     children=["Capital Expenditures", "Acquisitions"],
                     is_total=True,
                     description="Cash from investing"),

            # Financing Activities
            LineItem("Debt Issuance (Repayment)", LineItemType.CALCULATED, "cash_flow", order=100, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: (df.loc[("balance_sheet", "Long-Term Debt")].iloc[idx] - (df.loc[("balance_sheet", "Long-Term Debt")].iloc[idx-1] if idx > 0 else 0))
                                                    + (df.loc[("balance_sheet", "Current Portion of Long-Term Debt")].iloc[idx] - (df.loc[("balance_sheet", "Current Portion of Long-Term Debt")].iloc[idx-1] if idx > 0 else 0))
                                                    if ("balance_sheet", "Long-Term Debt") in df.index else 0,
                     description="Net debt issuance"),
            LineItem("Revolver Draw (Repayment)", LineItemType.CALCULATED, "cash_flow", order=101, unit="$",
                     calculation_rule=CalculationRule.CUSTOM,
                     custom_formula=lambda df, idx: (df.loc[("balance_sheet", "Revolver")].iloc[idx] - (df.loc[("balance_sheet", "Revolver")].iloc[idx-1] if idx > 0 else 0))
                                                    if ("balance_sheet", "Revolver") in df.index else 0,
                     description="Revolver change"),
            LineItem("Equity Issuance (Repurchase)", LineItemType.INPUT, "cash_flow", order=110, unit="$",
                     driver=Driver("equity_issuance", [0], "Equity issuance/repurchase", "$"),
                     description="Equity financing"),
            LineItem("Dividends Paid", LineItemType.INPUT, "cash_flow", order=120, unit="$",
                     driver=Driver("dividends", [0], "Dividends paid", "$"),
                     description="Dividends"),
            LineItem("Financing Cash Flow", LineItemType.AGGREGATE, "cash_flow", order=130, unit="$",
                     children=["Debt Issuance (Repayment)", "Revolver Draw (Repayment)", "Equity Issuance (Repurchase)", "Dividends Paid"],
                     is_total=True,
                     description="Cash from financing"),

            # Net Change
            LineItem("Net Change in Cash", LineItemType.PLUG, "cash_flow", order=140, unit="$",
                     description="Net change in cash (plug)"),
            LineItem("Beginning Cash Balance", LineItemType.INPUT, "cash_flow", order=150, unit="$",
                     driver=Driver("beginning_cash", [500_000], "Beginning cash", "$"),
                     description="Beginning cash"),
            LineItem("Ending Cash Balance", LineItemType.AGGREGATE, "cash_flow", order=160, unit="$",
                     children=["Beginning Cash Balance", "Net Change in Cash"],
                     description="Ending cash"),
        ]
        for item in items:
            self.add_cash_flow_item(item)

    def _setup_default_assumptions(self) -> None:
        """Set default drivers for the model."""
        default_drivers = {
            "revenue": Driver("revenue", [10_000_000], "Starting revenue", "$"),
            "revenue_growth": Driver("revenue_growth", [0.20], "YoY revenue growth", "%"),
            "cogs_pct": Driver("cogs_pct", [0.35], "COGS as % of revenue", "%"),
            "rd_pct": Driver("rd_pct", [0.15], "R&D as % of revenue", "%"),
            "sm_pct": Driver("sm_pct", [0.25], "S&M as % of revenue", "%"),
            "gna_pct": Driver("gna_pct", [0.10], "G&A as % of revenue", "%"),
            "tax_rate": Driver("tax_rate", [0.21], "Effective tax rate", "%"),
            "ar_days": Driver("ar_days", [45], "Accounts receivable days", "days"),
            "inventory_days": Driver("inventory_days", [0], "Inventory days", "days"),
            "prepaid_pct": Driver("prepaid_pct", [0.02], "Prepaid as % of revenue", "%"),
            "ppe_gross": Driver("ppe_gross", [1_000_000], "Gross PP&E", "$"),
            "capex": Driver("capex", [-500_000], "Annual CapEx", "$"),
            "depreciation_rate": Driver("depreciation_rate", [0.10], "Depreciation rate", "%"),
            "intangibles": Driver("intangibles", [0], "Intangible assets", "$"),
            "goodwill": Driver("goodwill", [0], "Goodwill", "$"),
            "ap_days": Driver("ap_days", [60], "Accounts payable days", "days"),
            "accrued_pct": Driver("accrued_pct", [0.05], "Accrued as % of revenue", "%"),
            "deferred_rev_pct": Driver("deferred_rev_pct", [0.15], "Deferred revenue as % of revenue", "%"),
            "current_debt": Driver("current_debt", [0], "Current portion of debt", "$"),
            "long_term_debt": Driver("long_term_debt", [0], "Long-term debt", "$"),
            "dlt": Driver("dlt", [0], "Deferred tax liability", "$"),
            "common_stock": Driver("common_stock", [1_000], "Common stock", "$"),
            "apic": Driver("apic", [5_000_000], "APIC", "$"),
            "sbc": Driver("sbc", [0], "Stock-based compensation", "$"),
            "acquisitions": Driver("acquisitions", [0], "Acquisitions", "$"),
            "equity_issuance": Driver("equity_issuance", [0], "Equity issuance/repurchase", "$"),
            "dividends": Driver("dividends", [0], "Dividends paid", "$"),
            "beginning_cash": Driver("beginning_cash", [500_000], "Beginning cash", "$"),
            "interest_expense": Driver("interest_expense", [0], "Interest expense", "$"),
            "other_income": Driver("other_income", [0], "Other income/expense", "$"),
        }
        for name, driver in default_drivers.items():
            self.assumptions.set_driver(driver)

    @classmethod
    def from_yaml(cls, path: str) -> "ThreeStatementModel":
        """Load model from YAML configuration."""
        import yaml
        with open(path) as f:
            config = yaml.safe_load(f)

        model = cls(name=config.get("name", "Three Statement Model"))

        # Periods
        period_config = config.get("periods", {})
        start_year = period_config.get("start_year", 2020)
        years = period_config.get("years", 5)
        period_type = PeriodType(period_config.get("type", "annual"))

        periods = PeriodIndex()
        for i in range(years):
            periods.add(Period(year=start_year + i, period_type=period_type))
        model.set_periods(periods)

        # Assumptions
        if "assumptions" in config:
            if isinstance(config["assumptions"], str):
                model.set_assumptions(Assumptions.from_yaml(config["assumptions"]))
            else:
                # Inline assumptions - handle both formats: {"drivers": {name: {...}}} and flat {name: {...}}
                assumptions_data = config["assumptions"]
                if "drivers" in assumptions_data:
                    drivers = {name: Driver(name=name, **dc) for name, dc in assumptions_data.get("drivers", {}).items()}
                else:
                    drivers = {name: Driver(name=name, **dc) for name, dc in assumptions_data.items()}
                scenarios = assumptions_data.get("scenarios", {})
                metadata = assumptions_data.get("metadata", {})
                model.set_assumptions(Assumptions(drivers=drivers, scenarios=scenarios, metadata=metadata))

        # Override line items from config
        for stmt_config in config.get("statements", []):
            stmt_type = StatementType(stmt_config["type"])
            for item_config in stmt_config.get("line_items", []):
                # Convert dict to LineItem
                item = LineItem(**item_config)
                if stmt_type == StatementType.INCOME_STATEMENT:
                    model.add_income_statement_item(item)
                elif stmt_type == StatementType.BALANCE_SHEET:
                    model.add_balance_sheet_item(item)
                elif stmt_type == StatementType.CASH_FLOW:
                    model.add_cash_flow_item(item)

        return model