"""
Main FinancialModel orchestrator.
Coordinates statements, assumptions, periods, and balancing.
"""

from dataclasses import dataclass, field
from typing import Any
from pathlib import Path
import pandas as pd
import yaml

from financial_model.core.statement import Statement, StatementType
from financial_model.core.line_item import LineItem, LineItemType, CalculationRule, Driver
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.core.assumptions import Assumptions
from financial_model.core.balancer import BalanceSheetBalancer, CashFlowBalancer, BalanceResult


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


class FinancialModel:
    """
    Main model class. Builds and calculates integrated three-statement model.
    """

    def __init__(
        self,
        name: str = "Financial Model",
        periods: PeriodIndex | None = None,
        assumptions: Assumptions | None = None,
    ):
        self.name = name
        self.periods = periods or PeriodIndex()
        self.assumptions = assumptions or Assumptions()

        # Statements
        self.income_statement = Statement(StatementType.INCOME_STATEMENT, periods=self.periods)
        self.balance_sheet = Statement(StatementType.BALANCE_SHEET, periods=self.periods)
        self.cash_flow = Statement(StatementType.CASH_FLOW, periods=self.periods)

        # Data frames (populated after calculate)
        self._is_df: pd.DataFrame | None = None
        self._bs_data: pd.DataFrame | None = None
        self._cf_data: pd.DataFrame | None = None

        # Balancers
        self.bs_balancer = BalanceSheetBalancer()
        self.cf_balancer = CashFlowBalancer()

    def add_income_statement_item(self, item: LineItem) -> "FinancialModel":
        self.income_statement.add_line_item(item)
        return self

    def add_balance_sheet_item(self, item: LineItem) -> "FinancialModel":
        self.balance_sheet.add_line_item(item)
        return self

    def add_cash_flow_item(self, item: LineItem) -> "FinancialModel":
        self.cash_flow.add_line_item(item)
        return self

    def set_periods(self, periods: PeriodIndex) -> "FinancialModel":
        self.periods = periods
        for stmt in [self.income_statement, self.balance_sheet, self.cash_flow]:
            stmt.periods = periods
        return self

    def set_assumptions(self, assumptions: Assumptions) -> "FinancialModel":
        self.assumptions = assumptions
        return self

    def calculate(self, scenario: str | None = None) -> ModelResult:
        """Run full model calculation."""
        # Initialize data frames with MultiIndex for cross-statement references
        is_df = self.income_statement.to_dataframe(self.periods)
        bs_df = self.balance_sheet.to_dataframe(self.periods)
        cf_df = self.cash_flow.to_dataframe(self.periods)

        # Create MultiIndex DataFrame for cross-statement LINK references
        # Combine all statements with statement type as first level
        all_dfs = []
        for stmt_name, df in [("income_statement", is_df), ("balance_sheet", bs_df), ("cash_flow", cf_df)]:
            df_copy = df.copy()
            df_copy.index = pd.MultiIndex.from_tuples([(stmt_name, idx) for idx in df_copy.index])
            all_dfs.append(df_copy)

        combined = pd.concat(all_dfs)

        # Calculate in order: IS -> BS -> CF -> Balance
        # Pass combined DataFrame for LINK resolution
        self._calculate_income_statement(is_df, combined, scenario)
        self._calculate_balance_sheet(bs_df, combined, scenario)
        self._calculate_cash_flow(cf_df, combined, scenario)

        # Update combined after calculations (keep simple-index versions for balancer)
        is_combined = is_df.copy()
        is_combined.index = pd.MultiIndex.from_tuples([("income_statement", idx) for idx in is_combined.index])
        bs_combined = bs_df.copy()
        bs_combined.index = pd.MultiIndex.from_tuples([("balance_sheet", idx) for idx in bs_combined.index])
        cf_combined = cf_df.copy()
        cf_combined.index = pd.MultiIndex.from_tuples([("cash_flow", idx) for idx in cf_combined.index])
        combined = pd.concat([is_combined, bs_combined, cf_combined])

        # Recalculate RE from NI first
        self._update_retained_earnings(bs_df, is_df)

        # Recalculate AGGREGATE items (Total Assets, Total Liabilities, Total Equity) before balancing
        self._recalculate_aggregates(bs_df)

        # Balance BS and CF using simple-index DataFrames
        bs_result = self.bs_balancer.balance(bs_df, cf_df, is_df, self.periods, self.assumptions, self.balance_sheet)
        cf_result = self.cf_balancer.balance(bs_df, cf_df, self.periods)

        # Recalculate AGGREGATE items after balancing (plugs may have changed)
        self._recalculate_aggregates(bs_df)
        self._recalculate_cf_aggregates(cf_df)

        return ModelResult(
            income_statement=is_df.copy(),
            balance_sheet=bs_df.copy(),
            cash_flow=cf_df.copy(),
            assumptions=self.assumptions,
            periods=self.periods,
            bs_balance_result=bs_result,
            cf_balance_result=cf_result,
            metadata={"model_name": self.name, "scenario": scenario},
        )

    def _recalculate_aggregates(self, df: pd.DataFrame) -> None:
        """Recalculate AGGREGATE line items after balancing."""
        # Only recalculate aggregates for the statement this df belongs to
        for item in self.balance_sheet.ordered_items():
            if item.line_type == LineItemType.AGGREGATE:
                for period_idx in range(len(self.periods)):
                    val = item.calculate(df, period_idx, self.assumptions.drivers)
                    df.loc[item.name, df.columns[period_idx]] = val

    def _recalculate_cf_aggregates(self, df: pd.DataFrame) -> None:
        """Recalculate AGGREGATE line items in cash flow after balancing."""
        for item in self.cash_flow.ordered_items():
            if item.line_type == LineItemType.AGGREGATE:
                for period_idx in range(len(self.periods)):
                    val = item.calculate(df, period_idx, self.assumptions.drivers)
                    df.loc[item.name, df.columns[period_idx]] = val

    def _calculate_income_statement(self, df: pd.DataFrame, combined: pd.DataFrame, scenario: str | None) -> None:
        for item in self.income_statement.ordered_items():
            for period_idx in range(len(self.periods)):
                val = item.calculate(combined, period_idx, self.assumptions.drivers)
                df.loc[item.name, df.columns[period_idx]] = val
                # Update combined for subsequent LINK references
                if isinstance(combined.index, pd.MultiIndex):
                    combined.loc[("income_statement", item.name), df.columns[period_idx]] = val

    def _calculate_balance_sheet(self, df: pd.DataFrame, combined: pd.DataFrame, scenario: str | None) -> None:
        # First pass: calculate non-plug items
        for item in self.balance_sheet.ordered_items():
            if item.line_type == LineItemType.PLUG:
                continue
            for period_idx in range(len(self.periods)):
                val = item.calculate(combined, period_idx, self.assumptions.drivers)
                df.loc[item.name, df.columns[period_idx]] = val
                # Update combined for subsequent LINK references
                if isinstance(combined.index, pd.MultiIndex):
                    combined.loc[("balance_sheet", item.name), df.columns[period_idx]] = val

    def _calculate_cash_flow(self, df: pd.DataFrame, combined: pd.DataFrame, scenario: str | None) -> None:
        for item in self.cash_flow.ordered_items():
            for period_idx in range(len(self.periods)):
                val = item.calculate(combined, period_idx, self.assumptions.drivers)
                df.loc[item.name, df.columns[period_idx]] = val
                # Update combined for subsequent LINK references
                if isinstance(combined.index, pd.MultiIndex):
                    combined.loc[("cash_flow", item.name), df.columns[period_idx]] = val

    def _update_retained_earnings(self, bs_df: pd.DataFrame, is_df: pd.DataFrame) -> None:
        re_line = "Retained Earnings"
        if re_line in bs_df.index:
            for period_idx in range(len(self.periods)):
                ni = is_df.loc["Net Income"].iloc[period_idx] if "Net Income" in is_df.index else 0
                dividends = is_df.loc["Dividends"].iloc[period_idx] if "Dividends" in is_df.index else 0
                prev_re = bs_df.loc[re_line].iloc[period_idx - 1] if period_idx > 0 else 0
                bs_df.loc[re_line, bs_df.columns[period_idx]] = prev_re + ni - dividends

    def export_excel(self, path: str, result: ModelResult | None = None) -> None:
        """Export model to formatted Excel workbook."""
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, Border, Side, PatternFill, numbers
        from openpyxl.utils import get_column_letter

        result = result or self.calculate()
        wb = Workbook()

        # Styles
        header_font = Font(bold=True, size=11)
        total_font = Font(bold=True, size=11)
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font_white = Font(bold=True, size=11, color="FFFFFF")
        thin_border = Border(
            bottom=Side(style="thin", color="B4C6E7")
        )
        thick_border = Border(
            bottom=Side(style="medium", color="4472C4")
        )
        pct_format = '0.0%'
        num_format = '#,##0'
        num_format_dec = '#,##0.0'

        def write_statement(ws, title: str, df: pd.DataFrame, start_row: int = 1) -> int:
            # Title
            ws.cell(row=start_row, column=1, value=title).font = Font(bold=True, size=14)
            start_row += 1

            # Headers
            ws.cell(row=start_row, column=1, value="Line Item").font = header_font_white
            ws.cell(row=start_row, column=1).fill = header_fill
            for col_idx, period in enumerate(self.periods):
                cell = ws.cell(row=start_row, column=col_idx + 2, value=period.label)
                cell.font = header_font_white
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal="center")
            start_row += 1

            # Data rows
            for row_idx, (line_name, row) in enumerate(df.iterrows()):
                ws.cell(row=start_row, column=1, value=line_name).font = Font(bold=row_idx in [0])  # Simplified
                for col_idx, val in enumerate(row):
                    cell = ws.cell(row=start_row, column=col_idx + 2, value=val if pd.notna(val) else 0)
                    cell.number_format = num_format
                    cell.border = thin_border
                # Check if total line
                item = self.income_statement.get_line_item(line_name) or \
                       self.balance_sheet.get_line_item(line_name) or \
                       self.cash_flow.get_line_item(line_name)
                if item and item.is_total:
                    for col_idx in range(len(self.periods) + 1):
                        ws.cell(row=start_row, column=col_idx + 1).font = total_font
                        ws.cell(row=start_row, column=col_idx + 1).border = thick_border
                start_row += 1

            return start_row + 1

        # Income Statement
        ws_is = wb.active
        ws_is.title = "Income Statement"
        write_statement(ws_is, "Income Statement", result.income_statement)

        # Balance Sheet
        ws_bs = wb.create_sheet("Balance Sheet")
        write_statement(ws_bs, "Balance Sheet", result.balance_sheet)

        # Cash Flow
        ws_cf = wb.create_sheet("Cash Flow")
        write_statement(ws_cf, "Cash Flow Statement", result.cash_flow)

        # Assumptions sheet
        ws_assumptions = wb.create_sheet("Assumptions")
        ws_assumptions.cell(row=1, column=1, value="Assumptions").font = Font(bold=True, size=14)
        ws_assumptions.cell(row=2, column=1, value="Driver").font = header_font_white
        ws_assumptions.cell(row=2, column=1).fill = header_fill
        ws_assumptions.cell(row=2, column=2, value="Value").font = header_font_white
        ws_assumptions.cell(row=2, column=2).fill = header_fill
        ws_assumptions.cell(row=2, column=3, value="Unit").font = header_font_white
        ws_assumptions.cell(row=2, column=3).fill = header_fill
        ws_assumptions.cell(row=2, column=4, value="Description").font = header_font_white
        ws_assumptions.cell(row=2, column=4).fill = header_fill

        for row_idx, (name, driver) in enumerate(result.assumptions.drivers.items()):
            r = row_idx + 3
            ws_assumptions.cell(row=r, column=1, value=name)
            val = driver.value
            ws_assumptions.cell(row=r, column=2, value=val if not isinstance(val, list) else val[0])
            ws_assumptions.cell(row=r, column=3, value=driver.unit)
            ws_assumptions.cell(row=r, column=4, value=driver.description)

        # Auto-fit columns
        for ws in [ws_is, ws_bs, ws_cf, ws_assumptions]:
            for col in ws.columns:
                max_length = max(len(str(cell.value or "")) for cell in col)
                ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_length + 4, 30)

        wb.save(path)

    def to_yaml(self, path: str) -> None:
        """Export model topology to YAML config."""
        config = {
            "name": self.name,
            "periods": {
                "start_year": self.periods[0].year if self.periods else 2020,
                "years": len(self.periods),
                "type": self.periods[0].period_type.value if self.periods else "annual",
            },
            "assumptions": {
                "drivers": {name: {
                    "name": d.name,
                    "value": d.value,
                    "description": d.description,
                    "unit": d.unit,
                    "source": d.source,
                } for name, d in self.assumptions.drivers.items()},
                "scenarios": self.assumptions.scenarios,
                "metadata": self.assumptions.metadata,
            },
            "statements": [
                {
                    "type": "income_statement",
                    "line_items": [self._line_item_to_dict(item) for item in self.income_statement.ordered_items()],
                },
                {
                    "type": "balance_sheet",
                    "line_items": [self._line_item_to_dict(item) for item in self.balance_sheet.ordered_items()],
                },
                {
                    "type": "cash_flow",
                    "line_items": [self._line_item_to_dict(item) for item in self.cash_flow.ordered_items()],
                },
            ],
        }
        with open(path, "w") as f:
            yaml.dump(config, f, sort_keys=False)

    def _line_item_to_dict(self, item: LineItem) -> dict:
        """Convert LineItem to dict for YAML serialization."""
        d = {
            "name": item.name,
            "line_type": item.line_type.value,
            "statement_type": item.statement_type,
            "order": item.order,
            "unit": item.unit,
            "format": item.format,
            "parent": item.parent,
            "children": item.children,
            "is_total": item.is_total,
            "description": item.description,
        }
        if item.calculation_rule:
            d["calculation_rule"] = item.calculation_rule.value
        if item.calculation_params:
            d["calculation_params"] = item.calculation_params
        if item.driver:
            d["driver"] = {
                "name": item.driver.name,
                "value": item.driver.value,
                "description": item.driver.description,
                "unit": item.driver.unit,
                "source": item.driver.source,
            }
        return d

    @classmethod
    def from_yaml(cls, path: str) -> "FinancialModel":
        """Load model topology from YAML config."""
        with open(path) as f:
            config = yaml.safe_load(f)

        model = cls(name=config.get("name", "Financial Model"))
        # Explicitly set the name from config to preserve it
        if "name" in config:
            model.name = config["name"]

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
            assumptions = Assumptions.from_yaml(config["assumptions"]) if isinstance(config["assumptions"], str) else \
                Assumptions(**config["assumptions"])
            model.set_assumptions(assumptions)

        # Line items from config
        for stmt_config in config.get("statements", []):
            stmt_type = StatementType(stmt_config["type"])
            for item_config in stmt_config.get("line_items", []):
                item = LineItem(**item_config)
                if stmt_type == StatementType.INCOME_STATEMENT:
                    model.add_income_statement_item(item)
                elif stmt_type == StatementType.BALANCE_SHEET:
                    model.add_balance_sheet_item(item)
                elif stmt_type == StatementType.CASH_FLOW:
                    model.add_cash_flow_item(item)

        return model