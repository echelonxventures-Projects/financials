"""
Synthetic data generators for demo companies by industry.
"""

import pandas as pd
import numpy as np
from financial_model.core.period import Period, PeriodIndex, PeriodType


def generate_saas_company(
    name: str = "SaaS Demo Co",
    start_year: int = 2020,
    years: int = 5,
    base_arr: float = 10_000_000,
    arr_growth: float = 0.35,
    gross_margin: float = 0.78,
    sales_marketing_pct: float = 0.45,
    rd_pct: float = 0.22,
    gna_pct: float = 0.12,
    churn_rate: float = 0.05,
    expansion_rate: float = 0.15,
    cac_payback_months: float = 12,
    tax_rate: float = 0.21,
) -> dict[str, pd.DataFrame]:
    """
    Generate realistic SaaS financials with ARR-based drivers.
    """
    periods = PeriodIndex([Period(year=start_year + i, period_type=PeriodType.ANNUAL) for i in range(years)])
    cols = periods.to_dataframe_index()

    # Build ARR trajectory
    arr = [base_arr]
    for i in range(1, years):
        new_arr = arr[-1] * (1 + arr_growth)
        arr.append(new_arr)

    # Revenue recognition (simplified: ratable over year)
    revenue = [a * (1 - churn_rate) + a * expansion_rate for a in arr]  # Net revenue retention

    # Income Statement
    is_data = pd.DataFrame(index=[
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
    ], columns=cols, dtype=float)

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
    bs_data = pd.DataFrame(index=[
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
    ], columns=cols, dtype=float)

    # Simple BS projections
    for i in range(years):
        rev = revenue[i]
        ar_days = 45
        dso = (rev / 365) * ar_days
        deferred_rev = rev * 0.3  # 30% of revenue deferred
        ap = rev * (1 - gross_margin) * (30 / 365)
        accrued = rev * 0.05

        cash = max(500_000, revenue[i] * 0.15) if i == 0 else bs_data.iloc[0, i-1] + is_data.iloc[12, i] * 0.5  # Simplified

        bs_data.iloc[0, i] = cash  # Cash
        bs_data.iloc[1, i] = dso  # AR
        bs_data.iloc[2, i] = sm * 0.1  # Deferred contract costs
        bs_data.iloc[3, i] = rev * 0.02  # Prepaid
        bs_data.iloc[4, i] = cash + dso + sm * 0.1 + rev * 0.02  # Total Current Assets
        bs_data.iloc[5, i] = rev * 0.05  # PP&E
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
            bs_data.iloc[15, i] = 1_000  # Common Stock
            bs_data.iloc[16, i] = 5_000_000  # APIC
            bs_data.iloc[17, i] = is_data.iloc[12, i]  # RE
        else:
            bs_data.iloc[15, i] = bs_data.iloc[15, i-1]
            bs_data.iloc[16, i] = bs_data.iloc[16, i-1]
            bs_data.iloc[17, i] = bs_data.iloc[17, i-1] + is_data.iloc[12, i]
        bs_data.iloc[18, i] = bs_data.iloc[15, i] + bs_data.iloc[16, i] + bs_data.iloc[17, i]
        bs_data.iloc[8, i] = bs_data.iloc[14, i] + bs_data.iloc[18, i]  # Balance

    # Cash Flow
    cf_data = pd.DataFrame(index=[
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
    ], columns=cols, dtype=float)

    for i in range(years):
        ni = is_data.iloc[12, i]
        dna = rev * 0.03
        sbc = rev * 0.08
        wc_change = 0
        if i > 0:
            wc_change = -(bs_data.iloc[1, i] - bs_data.iloc[1, i-1]) - (bs_data.iloc[3, i] - bs_data.iloc[3, i-1]) \
                        + (bs_data.iloc[9, i] - bs_data.iloc[9, i-1]) + (bs_data.iloc[10, i] - bs_data.iloc[10, i-1])
        deferred_change = bs_data.iloc[11, i] - (bs_data.iloc[11, i-1] if i > 0 else 0)

        ocf = ni + dna + sbc + wc_change + deferred_change
        capex = -rev * 0.05
        inv = capex
        debt = 0
        equity = 0 if i > 0 else 5_000_000
        fin = debt + equity
        net_change = ocf + inv + fin
        beg_cash = bs_data.iloc[0, i-1] if i > 0 else 500_000
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
        "metadata": {"company": name, "industry": "SaaS"},
    }


def generate_consumer_company(
    name: str = "Consumer Demo Co",
    start_year: int = 2020,
    years: int = 5,
) -> dict[str, pd.DataFrame]:
    """Generate consumer/retail company financials."""
    # Placeholder - similar structure
    return generate_saas_company(name, start_year, years, base_arr=50_000_000, arr_growth=0.15, gross_margin=0.45)


def generate_manufacturing_company(
    name: str = "Manufacturing Demo Co",
    start_year: int = 2020,
    years: int = 5,
) -> dict[str, pd.DataFrame]:
    """Generate manufacturing company financials."""
    # Placeholder
    return generate_saas_company(name, start_year, years, base_arr=100_000_000, arr_growth=0.08, gross_margin=0.35)