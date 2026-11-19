"""
Tests for Budget Model.
"""

import pandas as pd

from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.models.budget import BudgetModel


class TestBudgetModel:
    def setup_method(self):
        periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(3)])
        self.model = BudgetModel(name="Test Budget", periods=periods)

    def test_budget_model_creation(self):
        assert self.model.name == "Test Budget"
        assert len(self.model.periods) == 3
        assert "Engineering" in self.model.departments
        assert "Sales" in self.model.departments

    def test_budget_calculates(self):
        result = self.model.calculate()

        assert "Revenue" in result.income_statement.index
        assert "Total Operating Expense" in result.income_statement.index
        assert "Operating Income" in result.income_statement.index
        assert "Net Income" in result.income_statement.index

        # Check balance sheet has minimal items
        assert "Cash & Cash Equivalents" in result.balance_sheet.index
        assert "Total Assets" in result.balance_sheet.index
        assert "Total Liabilities" in result.balance_sheet.index
        assert "Total Equity" in result.balance_sheet.index

        # Check cash flow has minimal items
        assert "Net Income" in result.cash_flow.index
        assert "Net Change in Cash" in result.cash_flow.index
        assert "Ending Cash Balance" in result.cash_flow.index

    def test_revenue_calculation(self):
        result = self.model.calculate()

        # Revenue should be calculated from ARR
        starting_arr = result.income_statement.loc["Starting ARR"].iloc[0]
        ending_arr = result.income_statement.loc["Ending ARR"].iloc[0]
        revenue = result.income_statement.loc["Revenue"].iloc[0]

        # Revenue = avg(Starting ARR, Ending ARR)
        expected = (starting_arr + ending_arr) / 2
        assert abs(revenue - expected) < 1.0

    def test_arr_rollforward(self):
        result = self.model.calculate()

        starting = result.income_statement.loc["Starting ARR"].iloc[0]
        new_arr = result.income_statement.loc["New ARR"].iloc[0]
        expansion = result.income_statement.loc["Expansion ARR"].iloc[0]
        churned = result.income_statement.loc["Churned ARR"].iloc[0]
        ending = result.income_statement.loc["Ending ARR"].iloc[0]

        expected = starting + new_arr + expansion + churned
        assert abs(ending - expected) < 1.0

    def test_headcount_expense_calculation(self):
        result = self.model.calculate()

        for dept in self.model.departments:
            hc = result.income_statement.loc[f"{dept} Headcount"].iloc[0]
            cost = result.income_statement.loc[f"{dept} Cost per Head"].iloc[0]
            expense = result.income_statement.loc[f"{dept} Expense"].iloc[0]

            expected = -hc * cost  # expenses are negative
            assert abs(expense - expected) < 1.0

    def test_total_opex_aggregate(self):
        result = self.model.calculate()

        total_opex = result.income_statement.loc["Total Operating Expense"].iloc[0]
        dept_expenses = sum(result.income_statement.loc[f"{d} Expense"].iloc[0] for d in self.model.departments)

        assert abs(total_opex - dept_expenses) < 1.0

    def test_operating_income(self):
        result = self.model.calculate()

        revenue = result.income_statement.loc["Revenue"].iloc[0]
        total_opex = result.income_statement.loc["Total Operating Expense"].iloc[0]
        op_income = result.income_statement.loc["Operating Income"].iloc[0]

        expected = revenue + total_opex  # OpEx is negative
        assert abs(op_income - expected) < 1.0

    def test_net_income_calculation(self):
        result = self.model.calculate()

        op_income = result.income_statement.loc["Operating Income"].iloc[0]
        tax_rate = self.model.assumptions.get_value("tax_rate", 0)
        net_income = result.income_statement.loc["Net Income"].iloc[0]

        expected = op_income * (1 - tax_rate)  # Simplified
        # Actually uses PCT_OF_LINE with tax_rate
        # Net Income = Operating Income - Tax = Operating Income * (1 - tax_rate)
        assert abs(net_income - expected) < 1.0

    def test_add_department(self):
        self.model.add_department("Operations", [5, 6, 7], 120_000)

        assert "Operations" in self.model.departments
        assert "operations_headcount" in self.model.assumptions.drivers
        assert "operations_cost_per_head" in self.model.assumptions.drivers

        # Check that line items were added
        assert self.model.income_statement.get_line_item("Operations Headcount") is not None
        assert self.model.income_statement.get_line_item("Operations Cost per Head") is not None
        assert self.model.income_statement.get_line_item("Operations Expense") is not None

    def test_variance_analysis(self):
        budget_result = self.model.calculate()

        # Create fake actuals
        actuals = budget_result.income_statement.copy()
        actuals.loc["Revenue"] = actuals.loc["Revenue"] * 1.1  # 10% beat

        variances = self.model.variance_analysis(actuals, budget_result)

        assert isinstance(variances, pd.DataFrame)
        assert "Budget" in variances.columns
        assert "Actual" in variances.columns
        assert "Variance" in variances.columns
        assert "Variance %" in variances.columns

        # Check revenue variance
        rev_var = variances.loc["Revenue"]
        assert rev_var["Actual"] > rev_var["Budget"]
        assert rev_var["Variance"] > 0
        assert rev_var["Variance %"] > 0

    def test_balance_sheet_balances(self):
        result = self.model.calculate()

        # Should balance (Cash is a plug)
        assert result.bs_balance_result.balanced
        assert result.bs_balance_result.max_diff < 1.0

    def test_cash_flow_ties(self):
        result = self.model.calculate()

        # CF should tie to BS
        assert result.cf_balance_result.balanced
        assert result.cf_balance_result.max_diff < 1.0
