"""
Synthetic data generators for demo companies by industry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from financial_model.core.period import Period, PeriodIndex, PeriodType

PROFILES_PATH = Path(__file__).with_name("synthetic.yaml")


def profiles() -> dict[str, dict[str, Any]]:
    """Synthetic company profiles: built-in file + ``data.synthetic_paths`` (``extends`` resolved)."""
    from financial_model.config_loader import get_config

    raw: dict[str, Any] = {}
    extra = (get_config().get("data") or {}).get("synthetic_paths") or []
    for path in [PROFILES_PATH, *([extra] if isinstance(extra, (str, Path)) else extra)]:
        with Path(path).open(encoding="utf-8") as f:
            for key, spec in ((yaml.safe_load(f) or {}).get("profiles") or {}).items():
                if spec is None:
                    raw.pop(key, None)
                else:
                    raw[key] = {**raw.get(key, {}), **spec}

    def resolve(key: str, seen: tuple[str, ...] = ()) -> dict[str, Any]:
        if key in seen:
            raise ValueError(f"synthetic profile '{key}' extends itself")
        spec = dict(raw[key])
        parent = spec.pop("extends", None)
        return {**(resolve(parent, (*seen, key)) if parent else {}), **spec}

    return {key: resolve(key) for key in raw}


def generate_company(industry: str, **overrides: Any) -> dict[str, Any]:
    """Synthetic statements for a profile; keyword arguments override any profile parameter."""
    available = profiles()
    if industry not in available:
        raise ValueError(f"unknown synthetic industry '{industry}'. Available: {', '.join(available)}")
    p = {**available[industry], **{k: v for k, v in overrides.items() if v is not None}}
    return _generate(p)


def generate_saas_company(**overrides: Any) -> dict[str, Any]:
    """The ``saas`` profile (kept for existing callers); keyword arguments override parameters."""
    return generate_company("saas", **overrides)


def generate_consumer_company(**overrides: Any) -> dict[str, Any]:
    return generate_company("consumer", **overrides)


def generate_manufacturing_company(**overrides: Any) -> dict[str, Any]:
    return generate_company("manufacturing", **overrides)


def _generate(p: dict[str, Any]) -> dict[str, Any]:
    """ARR-driven statements from a resolved profile ``p``."""
    name, start_year, years = p["name"], int(p["start_year"]), int(p["years"])
    base_arr, arr_growth, gross_margin = p["base_arr"], p["arr_growth"], p["gross_margin"]
    sales_marketing_pct, rd_pct, gna_pct = p["sales_marketing_pct"], p["rd_pct"], p["gna_pct"]
    churn_rate, expansion_rate, tax_rate = p["churn_rate"], p["expansion_rate"], p["tax_rate"]
    days = p["days_in_year"]
    periods = PeriodIndex([Period(year=start_year + i, period_type=PeriodType.ANNUAL) for i in range(years)])
    cols = periods.to_dataframe_index()

    # Build ARR trajectory
    arr = [base_arr]
    for _ in range(1, years):
        new_arr = arr[-1] * (1 + arr_growth)
        arr.append(new_arr)

    # Revenue recognition (simplified: ratable over year)
    revenue = [a * (1 - churn_rate) + a * expansion_rate for a in arr]  # Net revenue retention

    # Income Statement
    is_data = pd.DataFrame(
        index=[
            "Revenue",
            "COGS",
            "Gross Profit",
            "R&D Expense",
            "Sales & Marketing Expense",
            "General & Administrative Expense",
            "Total Operating Expenses",
            "Operating Income",
            "Interest Expense",
            "Other Income (Expense)",
            "Pre-Tax Income",
            "Income Tax Expense",
            "Net Income",
        ],
        columns=cols,
        dtype=float,
    )

    for i in range(years):
        rev = revenue[i]
        cogs = rev * (1 - gross_margin)
        rd = rev * rd_pct
        sm = rev * sales_marketing_pct
        gna = rev * gna_pct
        opex = rd + sm + gna
        oi = rev - cogs - opex
        interest = 0  # No debt initially
        pretax = oi + interest
        tax = pretax * tax_rate if pretax > 0 else 0
        ni = pretax - tax

        is_data.iloc[0, i] = rev
        is_data.iloc[1, i] = cogs
        is_data.iloc[2, i] = rev - cogs
        is_data.iloc[3, i] = rd
        is_data.iloc[4, i] = sm
        is_data.iloc[5, i] = gna
        is_data.iloc[6, i] = opex
        is_data.iloc[7, i] = oi
        is_data.iloc[8, i] = interest
        is_data.iloc[9, i] = 0
        is_data.iloc[10, i] = pretax
        is_data.iloc[11, i] = tax
        is_data.iloc[12, i] = ni

    # Balance Sheet
    bs_data = pd.DataFrame(
        index=[
            "Cash & Cash Equivalents",
            "Accounts Receivable",
            "Deferred Contract Costs",
            "Prepaid Expenses",
            "Total Current Assets",
            "Property Plant & Equipment, Net",
            "Intangible Assets",
            "Goodwill",
            "Total Assets",
            "Accounts Payable",
            "Accrued Expenses",
            "Deferred Revenue",
            "Total Current Liabilities",
            "Long-Term Debt",
            "Total Liabilities",
            "Common Stock",
            "Additional Paid-In Capital",
            "Retained Earnings",
            "Total Equity",
        ],
        columns=cols,
        dtype=float,
    )

    # Simple BS projections
    for i in range(years):
        rev = revenue[i]
        ar_days = p["ar_days"]
        dso = (rev / days) * ar_days
        deferred_rev = rev * p["deferred_revenue_pct"]
        ap = rev * (1 - gross_margin) * (p["ap_days"] / days)
        accrued = rev * p["accrued_pct"]

        cash = (
            max(p["opening_cash_min"], revenue[i] * p["opening_cash_pct"])
            if i == 0
            else bs_data.iloc[0, i - 1] + is_data.iloc[12, i] * p["cash_retention"]
        )  # Simplified

        sm = rev * sales_marketing_pct
        bs_data.iloc[0, i] = cash  # Cash
        bs_data.iloc[1, i] = dso  # AR
        bs_data.iloc[2, i] = sm * p["deferred_contract_cost_pct_of_sm"]  # Deferred contract costs
        bs_data.iloc[3, i] = rev * p["prepaid_pct"]  # Prepaid
        bs_data.iloc[4, i] = cash + dso + bs_data.iloc[2, i] + bs_data.iloc[3, i]  # Total Current Assets
        bs_data.iloc[5, i] = rev * p["ppe_pct"]  # PP&E
        bs_data.iloc[6, i] = 0  # Intangibles
        bs_data.iloc[7, i] = 0  # Goodwill
        bs_data.iloc[8, i] = bs_data.iloc[4, i] + bs_data.iloc[5, i]  # Total Assets

        bs_data.iloc[9, i] = ap  # AP
        bs_data.iloc[10, i] = accrued  # Accrued
        bs_data.iloc[11, i] = deferred_rev  # Deferred Revenue
        bs_data.iloc[12, i] = ap + accrued + deferred_rev  # Total Current Liab
        bs_data.iloc[13, i] = 0  # Long-term debt
        bs_data.iloc[14, i] = bs_data.iloc[12, i]  # Total Liabilities

        # Equity
        if i == 0:
            bs_data.iloc[15, i] = p["common_stock"]  # Common Stock
            bs_data.iloc[16, i] = p["apic"]  # APIC
            bs_data.iloc[17, i] = is_data.iloc[12, i]  # RE
        else:
            bs_data.iloc[15, i] = bs_data.iloc[15, i - 1]
            bs_data.iloc[16, i] = bs_data.iloc[16, i - 1]
            bs_data.iloc[17, i] = bs_data.iloc[17, i - 1] + is_data.iloc[12, i]
        bs_data.iloc[18, i] = bs_data.iloc[15, i] + bs_data.iloc[16, i] + bs_data.iloc[17, i]
        bs_data.iloc[8, i] = bs_data.iloc[14, i] + bs_data.iloc[18, i]  # Balance

    # Cash Flow
    cf_data = pd.DataFrame(
        index=[
            "Net Income",
            "Depreciation & Amortization",
            "Stock-Based Compensation",
            "Change in Working Capital",
            "Deferred Revenue Change",
            "Operating Cash Flow",
            "Capital Expenditures",
            "Investing Cash Flow",
            "Debt Issuance (Repayment)",
            "Equity Issuance (Repurchase)",
            "Financing Cash Flow",
            "Net Change in Cash",
            "Beginning Cash Balance",
            "Ending Cash Balance",
        ],
        columns=cols,
        dtype=float,
    )

    for i in range(years):
        rev = revenue[i]
        ni = is_data.iloc[12, i]
        dna = rev * p["dna_pct"]
        sbc = rev * p["sbc_pct"]
        wc_change = 0
        if i > 0:
            wc_change = (
                -(bs_data.iloc[1, i] - bs_data.iloc[1, i - 1])
                - (bs_data.iloc[3, i] - bs_data.iloc[3, i - 1])
                + (bs_data.iloc[9, i] - bs_data.iloc[9, i - 1])
                + (bs_data.iloc[10, i] - bs_data.iloc[10, i - 1])
            )
        deferred_change = bs_data.iloc[11, i] - (bs_data.iloc[11, i - 1] if i > 0 else 0)

        ocf = ni + dna + sbc + wc_change + deferred_change
        capex = -rev * p["capex_pct"]
        inv = capex
        debt = 0
        equity = 0 if i > 0 else p["initial_equity"]
        fin = debt + equity
        net_change = ocf + inv + fin
        beg_cash = bs_data.iloc[0, i - 1] if i > 0 else p["beginning_cash"]
        end_cash = beg_cash + net_change

        cf_data.iloc[0, i] = ni
        cf_data.iloc[1, i] = dna
        cf_data.iloc[2, i] = sbc
        cf_data.iloc[3, i] = wc_change
        cf_data.iloc[4, i] = deferred_change
        cf_data.iloc[5, i] = ocf
        cf_data.iloc[6, i] = capex
        cf_data.iloc[7, i] = inv
        cf_data.iloc[8, i] = debt
        cf_data.iloc[9, i] = equity
        cf_data.iloc[10, i] = fin
        cf_data.iloc[11, i] = net_change
        cf_data.iloc[12, i] = beg_cash
        cf_data.iloc[13, i] = end_cash

    return {
        "income_statement": is_data,
        "balance_sheet": bs_data,
        "cash_flow": cf_data,
        "periods": periods,
        "metadata": {"company": name, "industry": p["label"]},
    }
