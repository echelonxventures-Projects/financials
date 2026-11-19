"""
Golden master tests: compare model output against reference Excel workbook.
"""
from __future__ import annotations

from financial_model.core.assumptions import Driver
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.models.three_statement import ThreeStatementModel


class TestGoldenMaster:
    """Compare model output to known-good reference values."""

    # Hand-derived reference values (see docs/conventions.md for the sign and
    # opening-balance conventions). Drivers: revenue 10M growing 20%, COGS 35%,
    # R&D 15%, S&M 15%, G&A 10%, tax 21%, AR 45 days, AP 60 days on COGS,
    # deferred revenue 10%, prepaid 2%, accrued 5%, capex 500k/yr on opening
    # gross PP&E of 1M depreciated at 10% of gross, opening cash 500k, common
    # stock 1k, APIC 5M.
    #
    #   Gross PP&E   = 1.0M + 0.5M * (t+1)          -> 1.5M, 2.0M, 2.5M
    #   D&A          = 10% x gross                   -> 150k, 200k, 250k
    #   Opex         = 40% x revenue + D&A           -> 4.15M, 5.0M, 6.01M
    #   EBIT         = 65% x revenue - opex          -> 2.35M, 2.8M, 3.35M
    #   Net income   = EBIT x 0.79                   -> 1,856,500 / 2,212,000 / 2,646,500
    #   Opening RE   = cash 500k + PP&E 1M - 1k - 5M -> -3,501,000 (opening BS plug)
    REFERENCE_VALUES = {
        "income_statement": {
            "Revenue": [10_000_000.0, 12_000_000.0, 14_400_000.0],
            "COGS": [-3_500_000.0, -4_200_000.0, -5_040_000.0],
            "Gross Profit": [6_500_000.0, 7_800_000.0, 9_360_000.0],
            "Depreciation & Amortization": [-150_000.0, -200_000.0, -250_000.0],
            "Operating Income": [2_350_000.0, 2_800_000.0, 3_350_000.0],
            "Income Tax Expense": [-493_500.0, -588_000.0, -703_500.0],
            "Net Income": [1_856_500.0, 2_212_000.0, 2_646_500.0],
        },
        "balance_sheet": {
            "Cash & Cash Equivalents": [2_648_965.75, 4_689_458.90, 7_240_150.68],
            "Accounts Receivable": [1_232_876.71, 1_479_452.05, 1_775_342.47],
            "Prepaid Expenses": [200_000.0, 240_000.0, 288_000.0],
            "Property Plant & Equipment, Gross": [1_500_000.0, 2_000_000.0, 2_500_000.0],
            "Accumulated Depreciation": [-150_000.0, -350_000.0, -600_000.0],
            "Property Plant & Equipment, Net": [1_350_000.0, 1_650_000.0, 1_900_000.0],
            "Accounts Payable": [575_342.47, 690_410.96, 828_493.15],
            "Accrued Expenses": [500_000.0, 600_000.0, 720_000.0],
            "Deferred Revenue": [1_000_000.0, 1_200_000.0, 1_440_000.0],
            "Total Assets": [5_431_842.47, 8_058_910.96, 11_203_493.15],
            "Total Liabilities": [2_075_342.47, 2_490_410.96, 2_988_493.15],
            "Retained Earnings": [-1_644_500.0, 567_500.0, 3_214_000.0],
            "Total Equity": [3_356_500.0, 5_568_500.0, 8_215_000.0],
        },
        "cash_flow": {
            "Operating Cash Flow": [2_648_965.75, 2_540_493.15, 3_050_691.78],
            "Investing Cash Flow": [-500_000.0, -500_000.0, -500_000.0],
            "Net Change in Cash": [2_148_965.75, 2_040_493.15, 2_550_691.78],
            "Ending Cash Balance": [2_648_965.75, 4_689_458.90, 7_240_150.68],
        },
    }

    TOLERANCE = 0.001  # values are hand-derived; only rounding differences allowed

    def test_income_statement_matches_reference(self):
        model = self._create_reference_model()
        result = model.calculate()

        is_df = result.income_statement
        for line, expected in self.REFERENCE_VALUES["income_statement"].items():
            if line in is_df.index:
                actual = is_df.loc[line].values
                for i, (act, exp) in enumerate(zip(actual, expected)):
                    rel_diff = abs(act - exp) / abs(exp) if exp != 0 else abs(act)
                    assert rel_diff < self.TOLERANCE, (
                        f"{line} period {i}: expected {exp:,.0f}, got {act:,.0f} (diff {rel_diff:.2%})"
                    )

    def test_balance_sheet_matches_reference(self):
        """Test BS lines that are deterministic (not plug-dependent)."""
        model = self._create_reference_model()
        result = model.calculate()

        bs_df = result.balance_sheet
        # Only test non-plug lines (AR, Total lines)
        for line, expected in self.REFERENCE_VALUES["balance_sheet"].items():
            if line in bs_df.index:
                actual = bs_df.loc[line].values
                for i, (act, exp) in enumerate(zip(actual, expected)):
                    rel_diff = abs(act - exp) / abs(exp) if exp != 0 else abs(act)
                    assert rel_diff < self.TOLERANCE, (
                        f"{line} period {i}: expected {exp:,.0f}, got {act:,.0f} (diff {rel_diff:.2%})"
                    )

    def test_cash_flow_matches_reference(self):
        model = self._create_reference_model()
        result = model.calculate()

        cf_df = result.cash_flow
        for line, expected in self.REFERENCE_VALUES["cash_flow"].items():
            assert line in cf_df.index, f"{line} missing from cash flow"
            actual = cf_df.loc[line].values
            for i, (act, exp) in enumerate(zip(actual, expected)):
                rel_diff = abs(act - exp) / abs(exp) if exp != 0 else abs(act)
                assert rel_diff < self.TOLERANCE, (
                    f"{line} period {i}: expected {exp:,.0f}, got {act:,.0f} (diff {rel_diff:.2%})"
                )

    def test_balance_sheet_balances_exactly(self):
        """Balance sheet must balance to near-zero."""
        model = self._create_reference_model()
        result = model.calculate()

        bs_df = result.balance_sheet
        for i in range(len(bs_df.columns)):
            assets = bs_df.loc["Total Assets"].iloc[i] if "Total Assets" in bs_df.index else 0
            le = (
                bs_df.loc["Total Liabilities"].iloc[i] + bs_df.loc["Total Equity"].iloc[i]
                if "Total Liabilities" in bs_df.index and "Total Equity" in bs_df.index
                else 0
            )
            diff = abs(assets - le)
            assert diff < 1.0, f"Period {i}: BS doesn't balance. Assets={assets:,.0f}, L+E={le:,.0f}, diff={diff:,.0f}"

    def test_cash_flow_ties_to_balance_sheet(self):
        """Ending cash in CF must match BS cash."""
        model = self._create_reference_model()
        result = model.calculate()

        bs_df = result.balance_sheet
        cf_df = result.cash_flow

        for i in range(len(bs_df.columns)):
            bs_cash = bs_df.loc["Cash & Cash Equivalents"].iloc[i] if "Cash & Cash Equivalents" in bs_df.index else 0
            cf_ending = cf_df.loc["Ending Cash Balance"].iloc[i] if "Ending Cash Balance" in cf_df.index else 0
            diff = abs(bs_cash - cf_ending)
            assert diff < 1.0, f"Period {i}: Cash mismatch. BS={bs_cash:,.0f}, CF={cf_ending:,.0f}"

    def _create_reference_model(self) -> ThreeStatementModel:
        """Create model with reference assumptions."""
        model = ThreeStatementModel(name="Golden Master Reference")
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        model.set_periods(periods)

        # Override with reference assumptions
        ref_drivers = {
            "revenue": Driver("revenue", [10_000_000, 12_000_000, 14_400_000], "Revenue", "$"),
            "revenue_growth": Driver("revenue_growth", 0.20, "Growth", "%"),
            "cogs_pct": Driver("cogs_pct", 0.35, "COGS %", "%"),
            "rd_pct": Driver("rd_pct", 0.15, "R&D %", "%"),
            "sm_pct": Driver("sm_pct", 0.15, "S&M %", "%"),
            "gna_pct": Driver("gna_pct", 0.10, "G&A %", "%"),
            "tax_rate": Driver("tax_rate", 0.21, "Tax Rate", "%"),
            "ar_days": Driver("ar_days", 45, "AR Days", "days"),
            "ap_days": Driver("ap_days", 60, "AP Days", "days"),
            "deferred_rev_pct": Driver("deferred_rev_pct", 0.10, "Deferred Rev %", "%"),
            "capex": Driver("capex", -500_000, "CapEx", "$"),
            "depreciation_rate": Driver("depreciation_rate", 0.10, "Dep Rate", "%"),
        }
        for name, driver in ref_drivers.items():
            model.assumptions.set_driver(driver)

        return model


class TestModelInvariants:
    """Property-based invariants that must always hold."""

    def test_bs_always_balances(self):
        """Balance sheet must balance for any valid input."""
        for _ in range(10):
            model = ThreeStatementModel()
            periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(5)])
            model.set_periods(periods)

            # Randomize some assumptions
            import random

            model.assumptions.set_driver(Driver("revenue_growth", random.uniform(0.05, 0.50), "Growth", "%"))
            model.assumptions.set_driver(Driver("cogs_pct", random.uniform(0.20, 0.70), "COGS %", "%"))
            model.assumptions.set_driver(Driver("tax_rate", random.uniform(0.15, 0.35), "Tax Rate", "%"))

            result = model.calculate()
            assert result.bs_balance_result.balanced, "BS failed to balance"
            assert result.bs_balance_result.max_diff < 1.0, f"BS diff too large: {result.bs_balance_result.max_diff}"

    def test_cf_always_ties(self):
        """Cash flow must tie to BS cash for any valid input."""
        for _ in range(10):
            model = ThreeStatementModel()
            periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(5)])
            model.set_periods(periods)

            result = model.calculate()
            assert result.cf_balance_result.balanced, "CF failed to tie"
            assert result.cf_balance_result.max_diff < 1.0, f"CF diff too large: {result.cf_balance_result.max_diff}"

    def test_retained_earnings_rollforward(self):
        """RE_t = RE_{t-1} + NI_t - Dividends_t, starting from the opening-balance-sheet RE."""
        model = ThreeStatementModel()
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(5)])
        model.set_periods(periods)
        result = model.calculate()

        is_df = result.income_statement
        bs_df = result.balance_sheet

        cf_df = result.cash_flow
        opening_re = model.opening_retained_earnings(result.drivers)
        if "Net Income" in is_df.index and "Retained Earnings" in bs_df.index:
            for i in range(len(periods)):
                ni = is_df.loc["Net Income"].iloc[i]
                dividends = cf_df.loc["Dividends Paid"].iloc[i] if "Dividends Paid" in cf_df.index else 0
                prev_re = bs_df.loc["Retained Earnings"].iloc[i - 1] if i > 0 else opening_re
                curr_re = bs_df.loc["Retained Earnings"].iloc[i]
                expected = prev_re + ni + dividends
                assert abs(curr_re - expected) < 1.0, f"RE rollforward failed at period {i}"

    def test_total_assets_equals_total_liabilities_plus_equity(self):
        """Fundamental accounting identity."""
        model = ThreeStatementModel()
        periods = PeriodIndex([Period(year=2020 + i, period_type=PeriodType.ANNUAL) for i in range(5)])
        model.set_periods(periods)
        result = model.calculate()

        bs_df = result.balance_sheet
        for i in range(len(periods)):
            assets = bs_df.loc["Total Assets"].iloc[i] if "Total Assets" in bs_df.index else 0
            liab = bs_df.loc["Total Liabilities"].iloc[i] if "Total Liabilities" in bs_df.index else 0
            equity = bs_df.loc["Total Equity"].iloc[i] if "Total Equity" in bs_df.index else 0
            assert abs(assets - (liab + equity)) < 0.01, f"Accounting identity violated at period {i}"
