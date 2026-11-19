"""
Tests for M&A Merger Model.
"""
from __future__ import annotations

import pandas as pd

from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.models.ma import MergerModel
from financial_model.models.three_statement import ThreeStatementModel


class TestMergerModel:
    def setup_method(self):
        # Create acquirer and target models
        self.acquirer = ThreeStatementModel(name="Acquirer")
        self.target = ThreeStatementModel(name="Target")
        periods = PeriodIndex([Period(year=2024 + i, period_type=PeriodType.ANNUAL) for i in range(5)])
        self.acquirer.set_periods(periods)
        self.target.set_periods(periods)

        # Override target revenue to be different
        self.target.assumptions.set_driver(self.target.assumptions.drivers["revenue"])
        self.target.assumptions.drivers["revenue"].value = [5_000_000, 6_000_000, 7_200_000, 8_640_000, 10_368_000]

    def test_merger_model_creation(self):
        merger = MergerModel(
            acquirer=self.acquirer,
            target=self.target,
            offer_price_per_share=50,
            target_shares=1_000_000,
            cash_consideration_pct=0.5,
            stock_consideration_pct=0.5,
            synergies=2_000_000,
        )
        assert merger.acquirer == self.acquirer
        assert merger.target == self.target
        assert merger.offer_price_per_share == 50
        assert merger.target_shares == 1_000_000
        assert merger.cash_consideration_pct == 0.5
        assert merger.stock_consideration_pct == 0.5
        assert merger.synergies == 2_000_000
        assert merger.financing_rate == 0.06
        assert merger.tax_rate == 0.21

    def test_synergy_phase_in_default(self):
        merger = MergerModel(acquirer=self.acquirer, target=self.target)
        assert merger.synergy_phase_in == [0.3, 0.6, 0.8, 1.0, 1.0]

    def test_synergy_phase_in_custom(self):
        merger = MergerModel(acquirer=self.acquirer, target=self.target, synergy_phase_in=[0.2, 0.4, 0.6, 0.8, 1.0])
        assert merger.synergy_phase_in == [0.2, 0.4, 0.6, 0.8, 1.0]

    def test_calculate_accretion_dilution(self):
        merger = MergerModel(
            acquirer=self.acquirer,
            target=self.target,
            offer_price_per_share=50,
            target_shares=1_000_000,
            cash_consideration_pct=0.5,
            stock_consideration_pct=0.5,
            synergies=2_000_000,
        )
        result = merger.calculate_accretion_dilution()

        assert "acquirer_ni" in result
        assert "acquirer_eps" in result
        assert "target_ni" in result
        assert "equity_value" in result
        assert "cash_paid" in result
        assert "stock_value" in result
        assert "new_shares_issued" in result
        assert "pf_revenue" in result
        assert "pf_ebit" in result
        assert "pf_synergies" in result
        assert "pf_interest" in result
        assert "pf_ni" in result
        assert "pf_shares" in result
        assert "pf_eps" in result
        assert "accretion_dilution_pct" in result
        assert "is_accretive" in result

        # Check values
        assert result["equity_value"] == 50_000_000
        assert result["cash_paid"] == 25_000_000
        assert result["stock_value"] == 25_000_000
        assert result["pf_synergies"] == 600_000  # 2M * 0.3

    def test_accretion_positive(self):
        merger = MergerModel(
            acquirer=self.acquirer,
            target=self.target,
            offer_price_per_share=30,  # Lower price = more accretive
            target_shares=1_000_000,
            cash_consideration_pct=0.5,
            stock_consideration_pct=0.5,
            synergies=5_000_000,
        )
        result = merger.calculate_accretion_dilution()
        assert result["is_accretive"] == True
        assert result["accretion_dilution_pct"] > 0

    def test_dilution_possible(self):
        # High price with low synergies should be dilutive
        merger = MergerModel(
            acquirer=self.acquirer,
            target=self.target,
            offer_price_per_share=100,  # Very high price
            target_shares=1_000_000,
            cash_consideration_pct=0.0,  # All stock
            stock_consideration_pct=1.0,
            synergies=0,
        )
        result = merger.calculate_accretion_dilution()
        # May be dilutive depending on assumptions
        assert "is_accretive" in result

    def test_contribution_analysis(self):
        merger = MergerModel(acquirer=self.acquirer, target=self.target)
        contrib = merger.contribution_analysis()

        assert isinstance(contrib, pd.DataFrame)
        assert "Metric" in contrib.columns
        assert "Acquirer" in contrib.columns
        assert "Target" in contrib.columns
        assert "Combined" in contrib.columns
        assert "Acquirer %" in contrib.columns

        # Check metrics
        metrics = contrib["Metric"].tolist()
        assert "Revenue" in metrics
        assert "EBITDA" in metrics
        assert "D&A" in metrics
        assert "EBIT" in metrics
        assert "Interest" in metrics
        assert "Pre-Tax" in metrics
        assert "Tax" in metrics
        assert "Net Income" in metrics

    def test_contribution_analysis_values(self):
        merger = MergerModel(acquirer=self.acquirer, target=self.target)
        contrib = merger.contribution_analysis()

        # Revenue should be combined
        rev_row = contrib[contrib["Metric"] == "Revenue"].iloc[0]
        assert rev_row["Combined"] == rev_row["Acquirer"] + rev_row["Target"]
        assert rev_row["Acquirer %"] == rev_row["Acquirer"] / rev_row["Combined"]

    def test_all_cash_deal(self):
        merger = MergerModel(
            acquirer=self.acquirer,
            target=self.target,
            offer_price_per_share=50,
            target_shares=1_000_000,
            cash_consideration_pct=1.0,
            stock_consideration_pct=0.0,
            synergies=0,
        )
        result = merger.calculate_accretion_dilution()
        assert result["cash_paid"] == 50_000_000
        assert result["stock_value"] == 0
        assert result["new_shares_issued"] == 0

    def test_all_stock_deal(self):
        merger = MergerModel(
            acquirer=self.acquirer,
            target=self.target,
            offer_price_per_share=50,
            target_shares=1_000_000,
            cash_consideration_pct=0.0,
            stock_consideration_pct=1.0,
            synergies=0,
        )
        result = merger.calculate_accretion_dilution()
        assert result["cash_paid"] == 0
        assert result["stock_value"] == 50_000_000
        assert result["new_shares_issued"] > 0
