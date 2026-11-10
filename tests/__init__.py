"""
Test suite for financial model.
"""

import pytest


def test_imports():
    """Verify all modules import correctly."""
    from financial_model.core import (
        Statement, StatementType, LineItem, LineItemType, CalculationRule,
        FinancialModel, BalanceSheetBalancer, CashFlowBalancer,
        Period, PeriodType, Assumptions, Driver,
    )
    from financial_model.models import (
        ThreeStatementModel, DCFModel, LBOModel, MergerModel, BudgetModel,
    )
    from financial_model.data import (
        DataLoader, load_public_company_data,
        generate_saas_company, generate_consumer_company, generate_manufacturing_company,
    )
    assert True