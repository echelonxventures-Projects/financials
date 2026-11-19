"""
Main FinancialModel orchestrator.
Coordinates statements, assumptions, periods, and balancing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from financial_model.core.assumptions import Assumptions
from financial_model.core.balancer import BalanceResult, BalanceSheetBalancer, CashFlowBalancer
from financial_model.core.line_item import Driver, LineItem, LineItemType
from financial_model.core.period import PeriodIndex, PeriodType
from financial_model.core.statement import Statement, StatementType


@dataclass
class ModelResult:
    """Container for model outputs."""

    income_statement: pd.DataFrame
    balance_sheet: pd.DataFrame
    cash_flow: pd.DataFrame
    assumptions: Assumptions
    periods: PeriodIndex
    bs_balance_result: BalanceResult
    cf_balance_result: BalanceResult
    metadata: dict[str, Any] = field(default_factory=dict)
    drivers: dict[str, Driver] = field(default_factory=dict)

    @property
    def labels(self) -> list[str]:
        return [p.label for p in self.periods]

    def labelled(self, df: pd.DataFrame) -> pd.DataFrame:
        """Copy of a statement with period labels (FY2024, Q1 FY2024, ...) as columns."""
        out = df.copy()
        if len(out.columns) == len(self.periods):
            out.columns = self.labels
        return out


class FinancialModel:
    """
    Main model class. Builds and calculates an integrated three-statement model.

    Calculation runs in passes: income statement -> balance sheet -> cash flow ->
    balancing. Passes repeat until values stop changing, which lets a line depend on
    a prior-period value produced later in the same pass (e.g. interest on last
    year's debt balance) without explicit circular references.
    """

    model_type = "three-statement"

    def __init__(
        self,
        name: str = "Financial Model",
        periods: PeriodIndex | None = None,
        assumptions: Assumptions | None = None,
    ):
        self.name = name
        self.periods = periods or PeriodIndex()
        self.assumptions = assumptions or Assumptions()

        self.income_statement = Statement(StatementType.INCOME_STATEMENT, periods=self.periods)
        self.balance_sheet = Statement(StatementType.BALANCE_SHEET, periods=self.periods)
        self.cash_flow = Statement(StatementType.CASH_FLOW, periods=self.periods)

        self.bs_balancer = BalanceSheetBalancer()
        self.cf_balancer = CashFlowBalancer()
        self.last_passes = 0

    # ------------------------------------------------------------------ #
    # Topology
    # ------------------------------------------------------------------ #
    def add_income_statement_item(self, item: LineItem) -> FinancialModel:
        self.income_statement.add_line_item(item)
        return self

    def add_balance_sheet_item(self, item: LineItem) -> FinancialModel:
        self.balance_sheet.add_line_item(item)
        return self

    def add_cash_flow_item(self, item: LineItem) -> FinancialModel:
        self.cash_flow.add_line_item(item)
        return self

    def set_periods(self, periods: PeriodIndex) -> FinancialModel:
        self.periods = periods
        for stmt in [self.income_statement, self.balance_sheet, self.cash_flow]:
            stmt.periods = periods
        return self

    def set_assumptions(self, assumptions: Assumptions) -> FinancialModel:
        """Replace the assumption set entirely."""
        self.assumptions = assumptions
        return self

    def merge_assumptions(self, assumptions: Assumptions) -> FinancialModel:
        """Overlay drivers/scenarios/metadata on top of the model's defaults."""
        self.assumptions.merge(assumptions)
        return self

    def validate(self) -> list[str]:
        errors: list[str] = []
        for stmt in [self.income_statement, self.balance_sheet, self.cash_flow]:
            errors.extend(f"{stmt.statement_type.value}: {e}" for e in stmt.validate())
        return errors

    # ------------------------------------------------------------------ #
    # Opening balance sheet hooks (overridden by concrete models)
    # ------------------------------------------------------------------ #
    def opening_balance(self, line: str, drivers: dict[str, Driver]) -> float:  # noqa: ARG002
        """Balance of ``line`` immediately before the first period (0 by default)."""
        return 0.0

    def opening_retained_earnings(self, drivers: dict[str, Driver]) -> float:  # noqa: ARG002
        return 0.0

    # ------------------------------------------------------------------ #
    # Calculation
    # ------------------------------------------------------------------ #
    def calculate(
        self, scenario: str | None = None, max_passes: int | None = None, tolerance: float = 1e-6
    ) -> ModelResult:
        """Run the full model calculation for the base case or a named scenario."""
        if (
            scenario
            and self.assumptions.scenarios
            and scenario not in self.assumptions.scenarios
            and scenario != "base"
        ):
            raise ValueError(
                f"Unknown scenario '{scenario}'. Available: {', '.join(self.assumptions.scenario_names())}"
            )

        drivers = self.assumptions.effective_drivers(scenario)
        effective_assumptions = Assumptions(drivers=drivers, scenarios={}, metadata=dict(self.assumptions.metadata))
        n_periods = len(self.periods)

        is_df = self.income_statement.to_dataframe(self.periods)
        bs_df = self.balance_sheet.to_dataframe(self.periods)
        cf_df = self.cash_flow.to_dataframe(self.periods)

        self.bs_balancer.opening_retained_earnings = self.opening_retained_earnings(drivers)

        passes = max_passes or max(2, n_periods + 3)
        previous: np.ndarray | None = None
        bs_result: BalanceResult | None = None
        cf_result: BalanceResult | None = None

        for pass_no in range(1, passes + 1):
            combined = self._combine(is_df, bs_df, cf_df)
            self._calculate_statement(self.income_statement, "income_statement", is_df, combined, drivers)
            self._calculate_statement(self.balance_sheet, "balance_sheet", bs_df, combined, drivers, skip_plugs=True)
            self._calculate_statement(self.cash_flow, "cash_flow", cf_df, combined, drivers)

            self._recalculate_aggregates(self.balance_sheet, bs_df, drivers)
            bs_result = self.bs_balancer.balance(
                bs_df, cf_df, is_df, self.periods, effective_assumptions, self.balance_sheet
            )
            cf_result = self.cf_balancer.balance(bs_df, cf_df, self.periods, self.cash_flow)
            self._recalculate_aggregates(self.balance_sheet, bs_df, drivers)
            self._recalculate_aggregates(self.cash_flow, cf_df, drivers)

            current = np.nan_to_num(
                np.concatenate(
                    [
                        is_df.to_numpy(dtype=float).ravel(),
                        bs_df.to_numpy(dtype=float).ravel(),
                        cf_df.to_numpy(dtype=float).ravel(),
                    ]
                )
            )
            self.last_passes = pass_no
            if previous is not None and np.allclose(current, previous, rtol=1e-9, atol=tolerance):
                break
            previous = current

        assert bs_result is not None and cf_result is not None
        return ModelResult(
            income_statement=is_df.copy(),
            balance_sheet=bs_df.copy(),
            cash_flow=cf_df.copy(),
            assumptions=self.assumptions,
            periods=self.periods,
            bs_balance_result=bs_result,
            cf_balance_result=cf_result,
            metadata={
                "model_name": self.name,
                "model_type": self.model_type,
                "scenario": scenario,
                "passes": self.last_passes,
            },
            drivers=drivers,
        )

    @staticmethod
    def _combine(is_df: pd.DataFrame, bs_df: pd.DataFrame, cf_df: pd.DataFrame) -> pd.DataFrame:
        frames = []
        for stmt_name, df in [("income_statement", is_df), ("balance_sheet", bs_df), ("cash_flow", cf_df)]:
            df_copy = df.copy()
            df_copy.index = pd.MultiIndex.from_tuples([(stmt_name, idx) for idx in df_copy.index])
            frames.append(df_copy)
        return pd.concat(frames)

    def _calculate_statement(
        self,
        stmt: Statement,
        key: str,
        df: pd.DataFrame,
        combined: pd.DataFrame,
        drivers: dict[str, Driver],
        skip_plugs: bool = False,
    ) -> None:
        for item in stmt.ordered_items():
            if skip_plugs and item.line_type == LineItemType.PLUG:
                continue
            for period_idx in range(len(self.periods)):
                val = item.calculate(combined, period_idx, drivers)
                col = df.columns[period_idx]
                df.loc[item.name, col] = val
                combined.loc[(key, item.name), col] = val

    def _recalculate_aggregates(self, stmt: Statement, df: pd.DataFrame, drivers: dict[str, Driver]) -> None:
        """Recalculate AGGREGATE line items in statement order (after plugs are set)."""
        for item in stmt.ordered_items():
            if item.line_type == LineItemType.AGGREGATE and item.name in df.index:
                for period_idx in range(len(self.periods)):
                    df.loc[item.name, df.columns[period_idx]] = item.calculate(df, period_idx, drivers)

    # ------------------------------------------------------------------ #
    # Outputs
    # ------------------------------------------------------------------ #
    def analysis_tables(self, result: ModelResult) -> dict[str, Any]:  # noqa: ARG002
        """Model-specific output tables (sheet/table name -> DataFrame or dict of scalars).

        Exported alongside the three statements by every exporter. Base models have none;
        DCF adds the valuation and sensitivity grid, LBO the deal, debt schedule and returns.
        """
        return {}

    def export_excel(self, path: str | Path, result: ModelResult | None = None) -> Path:
        """Export statements, assumptions and analysis tables to a formatted workbook."""
        from financial_model.exporters.excel_exporter import ExcelExporter

        result = result or self.calculate()
        return ExcelExporter().export_result(result, path, model=self, extras=self.analysis_tables(result))

    def export_csv(self, output_dir: str | Path, result: ModelResult | None = None) -> dict[str, Path]:
        """Export one CSV per statement and per analysis table into ``output_dir``."""
        from financial_model.exporters.csv_exporter import CSVExporter

        result = result or self.calculate()
        return CSVExporter().export_result(result, output_dir, extras=self.analysis_tables(result))

    def export_json(self, path: str | Path, result: ModelResult | None = None) -> Path:
        """Export statements, balance checks and analysis tables to JSON."""
        from financial_model.exporters.json_exporter import JSONExporter

        result = result or self.calculate()
        return JSONExporter().export_result(result, path, extras=self.analysis_tables(result))

    def to_yaml(self, path: str | Path) -> None:
        """
        Export model topology to a YAML config.

        Line items with Python ``custom_formula`` callables cannot be serialised and are
        omitted; the model class re-creates them when the config is loaded.
        """
        config: dict[str, Any] = {
            "name": self.name,
            "model_type": self.model_type,
            "periods": {
                "start_year": self.periods[0].year if len(self.periods) else 2020,
                "years": max(1, len(self.periods) // self.periods.period_type.periods_per_year)
                if len(self.periods)
                else 5,
                "type": self.periods.period_type.value,
            },
            "assumptions": self.assumptions.to_dict(),
            "statements": [
                {
                    "type": stmt.statement_type.value,
                    "line_items": [
                        self._line_item_to_dict(item) for item in stmt.ordered_items() if item.custom_formula is None
                    ],
                }
                for stmt in (self.income_statement, self.balance_sheet, self.cash_flow)
            ],
        }
        if len(self.periods) and len(self.periods) % self.periods.period_type.periods_per_year:
            config["periods"]["count"] = len(self.periods)
        with Path(path).open("w", encoding="utf-8") as f:
            yaml.dump(config, f, sort_keys=False)

    def _line_item_to_dict(self, item: LineItem) -> dict[str, Any]:
        """Convert LineItem to dict for YAML serialization."""
        d: dict[str, Any] = {
            "name": item.name,
            "line_type": item.line_type.value,
            "statement_type": item.statement_type,
            "order": item.order,
            "unit": item.unit,
            "format": item.format,
            "parent": item.parent,
            "children": list(item.children),
            "is_total": item.is_total,
            "description": item.description,
        }
        if item.calculation_rule:
            d["calculation_rule"] = item.calculation_rule.value
        if item.calculation_params:
            d["calculation_params"] = dict(item.calculation_params)
        if item.driver:
            d["driver"] = {
                "name": item.driver.name,
                "value": item.driver.value,
                "description": item.driver.description,
                "unit": item.driver.unit,
                "source": item.driver.source,
            }
        return d

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    @staticmethod
    def load_config(path: str | Path) -> dict[str, Any]:
        with Path(path).open(encoding="utf-8") as f:
            config = yaml.safe_load(f)
        if not isinstance(config, dict):
            raise ValueError(f"{path}: expected a mapping at the top level")
        return config

    @classmethod
    def from_config(cls, config: dict[str, Any], base_dir: Path | None = None) -> FinancialModel:
        """Build a model from a parsed YAML mapping. YAML assumptions overlay the class defaults."""
        model = cls(name=config.get("name", cls.__name__))

        period_config = config.get("periods", {}) or {}
        periods = PeriodIndex.generate(
            start_year=int(period_config.get("start_year", 2020)),
            years=int(period_config.get("years", 5)),
            period_type=PeriodType(period_config.get("type", "annual")),
            count=period_config.get("count"),
        )
        model.set_periods(periods)

        assumptions_cfg = config.get("assumptions")
        if isinstance(assumptions_cfg, str):
            assumptions_path = Path(assumptions_cfg)
            if not assumptions_path.is_absolute() and base_dir is not None and not assumptions_path.exists():
                assumptions_path = base_dir / assumptions_path
            model.merge_assumptions(Assumptions.from_yaml(assumptions_path))
        elif isinstance(assumptions_cfg, dict):
            model.merge_assumptions(Assumptions.from_dict(assumptions_cfg))

        for stmt_config in config.get("statements", []) or []:
            stmt_type = StatementType(stmt_config["type"])
            for item_config in stmt_config.get("line_items", []) or []:
                item = LineItem.from_dict(item_config)
                if stmt_type == StatementType.INCOME_STATEMENT:
                    model.add_income_statement_item(item)
                elif stmt_type == StatementType.BALANCE_SHEET:
                    model.add_balance_sheet_item(item)
                else:
                    model.add_cash_flow_item(item)

        return model

    @classmethod
    def from_yaml(cls, path: str | Path) -> FinancialModel:
        """Load model topology from a YAML config file."""
        path = Path(path)
        return cls.from_config(cls.load_config(path), base_dir=path.parent)
