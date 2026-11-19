"""
Test suite for financial model.
"""

import pytest


def test_imports():
    """Verify all modules import correctly."""
    from financial_model.core import (
        Assumptions,
        BalanceSheetBalancer,
        CalculationRule,
        CashFlowBalancer,
        Driver,
        FinancialModel,
        LineItem,
        LineItemType,
        Period,
        PeriodType,
        Statement,
        StatementType,
    )
    from financial_model.data import (
        DataLoader,
        generate_consumer_company,
        generate_manufacturing_company,
        generate_saas_company,
        load_public_company_data,
    )
    from financial_model.models import (
        BudgetModel,
        DCFModel,
        LBOModel,
        MergerModel,
        ThreeStatementModel,
    )

    assert True
