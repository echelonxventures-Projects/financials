"""
Main FinancialModel orchestrator.
Coordinates statements, assumptions, periods, and balancing.
"""

from __future__ import annotations

import copy
import functools
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from financial_model.core.assumptions import Assumptions
from financial_model.core.balancer import BalanceResult, BalanceSheetBalancer, CashFlowBalancer
from financial_model.core.definitions import (
    DefinitionError,
    definition_drivers,
    engine_section,
    engine_value,
    line_role,
    load_definition,
)
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

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Apply the definition's ``line_items`` once the outermost constructor has finished."""
        super().__init_subclass__(**kwargs)
        original = cls.__dict__.get("__init__")
        if original is None or getattr(original, "_applies_definition", False):
            return

        @functools.wraps(original)
        def init(self: FinancialModel, *args: Any, **kw: Any) -> None:
            original(self, *args, **kw)
            if type(self).__init__ is init:
                self.apply_definition_structure()

        init._applies_definition = True  # type: ignore[attr-defined]
        cls.__init__ = init  # type: ignore[method-assign]

    def __init__(
        self,
        name: str | None = None,
        periods: PeriodIndex | None = None,
        assumptions: Assumptions | None = None,
    ):
        self.name = name or ""
        self.periods = periods or PeriodIndex()
        self.assumptions = assumptions or Assumptions()

        self.income_statement = Statement(StatementType.INCOME_STATEMENT, periods=self.periods)
        self.balance_sheet = Statement(StatementType.BALANCE_SHEET, periods=self.periods)
        self.cash_flow = Statement(StatementType.CASH_FLOW, periods=self.periods)

        self.last_passes = 0

        # Defaults are data: models/definitions/<model_type>.yaml (see core/definitions.py).
        # Drivers already in `assumptions` win over the definition's defaults.
        # A subclass without its own definition file (e.g. a plugin) inherits its parent's.
        self.definition: dict[str, Any] = {}
        for klass in type(self).__mro__:
            if klass is FinancialModel or not issubclass(klass, FinancialModel):
                break
            key = klass.__dict__.get("model_type")
            if key:
                self.definition = load_definition(key, missing_ok=True)
                if self.definition:
                    break
        # Engine line roles, line groups and balancing limits (definition, over three-statement's).
        self.lines: dict[str, Any] = engine_section("lines", self.definition)
        self.line_groups: dict[str, Any] = engine_section("line_groups", self.definition)
        balancing = engine_section("balancing", self.definition)
        self.bs_balancer = BalanceSheetBalancer(lines=self.lines, groups=self.line_groups, balancing=balancing)
        self.cf_balancer = CashFlowBalancer(lines=self.lines, balancing=balancing)
        self._default_drivers = definition_drivers(self.definition)
        for driver_name, driver in self._default_drivers.items():
            if driver_name not in self.assumptions.drivers:
                self.assumptions.set_driver(copy.deepcopy(driver))
        if name is None:
            self.name = str(self.definition.get("name") or type(self).__name__)
        # The base layout (`structure`) is built first; engines may then add their own lines and
        # the definition's `line_items` are applied last (after the outermost constructor).
        self.apply_definition_structure("structure")
        # Classes whose constructor is this one (e.g. definition-only types built on the base engine)
        # get their definition's line items here; subclasses do it after their own constructor.
        if type(self).__init__ is FinancialModel.__init__:
            self.apply_definition_structure()

    # ------------------------------------------------------------------ #
    # Definition access
    # ------------------------------------------------------------------ #
    def ref(self, driver_name: str) -> Driver:
        """The definition's default for ``driver_name`` (for line items that carry a driver)."""
        driver = self._default_drivers.get(driver_name)
        if driver is None:
            raise DefinitionError(
                f"{self.model_type}: driver '{driver_name}' has no default in "
                f"{self.definition.get('_source', 'its definition file')}"
            )
        return copy.deepcopy(driver)

    def __getattr__(self, attr: str) -> Any:
        """Drivers read as attributes (``dcf.risk_free_rate``); only consulted when normal lookup fails."""
        if attr.startswith("_"):
            raise AttributeError(attr)
        assumptions = self.__dict__.get("assumptions")
        if assumptions is not None and attr in assumptions.drivers:
            return assumptions.drivers[attr].value
        raise AttributeError(f"{type(self).__name__!s} has no attribute or driver '{attr}'")

    def _apply_overrides(self, overrides: dict[str, Any]) -> None:
        """Keyword overrides of definition drivers (``DCFModel(beta=1.1)``); ``None`` keeps the default."""
        for key, value in overrides.items():
            if value is None:
                continue
            current = self.assumptions.drivers.get(key)
            if current is None:
                raise TypeError(f"{type(self).__name__}: unknown driver '{key}'")
            self.assumptions.set_driver(Driver(key, value, current.description, current.unit))

    def apply_definition_structure(self, section: str = "line_items") -> None:
        """Add, change or remove line items as the definition's ``line_items`` (or ``structure``) says.

        ``line_items: {<statement>: {<line name>: {formula: ..., order: ..., add_to: [...], ...}}}``
        A spec for an existing line changes only the fields it gives; ``null`` removes the line.
        ``structure`` has the same shape and is the base layout, built when the model is created.
        """
        statements = {
            StatementType.INCOME_STATEMENT.value: self.income_statement,
            StatementType.BALANCE_SHEET.value: self.balance_sheet,
            StatementType.CASH_FLOW.value: self.cash_flow,
        }
        for stmt_key, lines in (self.definition.get(section) or {}).items():
            stmt = statements.get(stmt_key)
            if stmt is None:
                raise DefinitionError(f"{self.model_type}: unknown statement '{stmt_key}' in {section}")
            for line_name, spec in (lines or {}).items():
                if spec is None:
                    stmt.remove_line_item(line_name)
                    continue
                stmt.add_line_item(self._line_from_spec(stmt_key, line_name, dict(spec), stmt.get_line_item(line_name)))
                for parent in spec.get("add_to") or []:
                    target = stmt.get_line_item(parent)
                    if target is None:
                        raise DefinitionError(f"{self.model_type}: add_to '{parent}' is not a line of {stmt_key}")
                    if line_name not in target.children:
                        target.children.append(line_name)

    def _line_from_spec(self, statement: str, name: str, spec: dict[str, Any], existing: LineItem | None) -> LineItem:
        spec.pop("add_to", None)
        base: dict[str, Any] = {}
        if existing is not None:
            base = {
                f.name: getattr(existing, f.name)
                for f in dataclass_fields(LineItem)
                if f.name not in ("name", "statement_type")
            }
        if "formula" in spec:
            base.update(line_type=LineItemType.CALCULATED, calculation_rule=None, custom_formula=None)
        if isinstance(spec.get("driver"), str):
            spec["driver"] = self.ref(spec["driver"])
        base.update(spec)
        base.setdefault("line_type", LineItemType.CALCULATED)
        return LineItem(name=name, statement_type=statement, **base)

    def formula_context(self, data: pd.DataFrame, idx: int, drivers: dict[str, Driver], statement: str | None) -> Any:
        from financial_model.core.expressions import Context

        period = self.periods[idx] if len(self.periods) > idx else None
        return Context(
            data,
            idx,
            drivers,
            statement,
            len(self.periods),
            self.periods.period_type.periods_per_year,
            period.year if period is not None else 0,
            lambda line: self.opening_balance(line, drivers),
            self.definition.get("expressions") or None,
        )

    def definition_tables(self, result: ModelResult) -> dict[str, Any]:
        """Tables from the definition's ``analysis`` section: ``{table: {row label: formula}}``."""
        from financial_model.core.expressions import evaluate

        specs = self.definition.get("analysis") or {}
        if not specs:
            return {}
        data = self._combine(result.income_statement, result.balance_sheet, result.cash_flow)
        tables: dict[str, Any] = {}
        for table, rows in specs.items():
            values = {
                label: [
                    evaluate(src, self.formula_context(data, i, result.drivers, None))
                    for i in range(len(result.periods))
                ]
                for label, src in (rows or {}).items()
            }
            frame = pd.DataFrame(values, index=result.labels).T
            frame.index.name = engine_value("analysis_index", self.definition)
            tables[table] = frame
        return tables

    def all_tables(self, result: ModelResult) -> dict[str, Any]:
        """Everything exporters and the API publish: the engine's tables plus the definition's."""
        return {**self.analysis_tables(result), **self.definition_tables(result)}

    def setting(self, key: str, default: Any = None) -> Any:
        """Any other section of the model's definition (labels, starters, departments...)."""
        return copy.deepcopy(self.definition.get(key, default))

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
    def line_name(self, role: str) -> str:
        """The statement line playing engine ``role`` (definition ``lines``: ``cash``, ``net_income``...)."""
        return line_role(self.lines, role)

    def line_group(self, name: str) -> list[str]:
        """A list of lines from the definition's ``line_groups`` (empty when not defined)."""
        return [str(n) for n in self.line_groups.get(name) or []]

    def opening_lines(self) -> dict[str, str]:
        """Definition ``opening_balances``: line -> driver whose period-0 value opens it."""
        cached = self.__dict__.get("_opening_lines")
        if cached is None:
            cached = {str(k): str(v) for k, v in (self.definition.get("opening_balances") or {}).items()}
            self.__dict__["_opening_lines"] = cached
        return cached

    def opening_balance(self, line: str, drivers: dict[str, Driver]) -> float:
        """Balance of ``line`` immediately before the first period (0 unless ``opening_balances`` lists it)."""
        driver = drivers.get(self.opening_lines().get(line, ""))
        return driver.get_value(0) if driver is not None else 0.0

    def opening_retained_earnings(self, drivers: dict[str, Driver]) -> float:
        """Definition ``opening_retained_earnings`` formula (0 without one)."""
        source = self.definition.get("opening_retained_earnings")
        if source is None:
            return 0.0
        if not isinstance(source, str):
            return float(source)
        from financial_model.core.expressions import evaluate

        return evaluate(source, self.formula_context(pd.DataFrame(), 0, drivers, None))

    # ------------------------------------------------------------------ #
    # Calculation
    # ------------------------------------------------------------------ #
    def calculate(
        self, scenario: str | None = None, max_passes: int | None = None, tolerance: float | None = None
    ) -> ModelResult:
        """Run the full model calculation for the base case or a named scenario.

        Pass count and convergence tolerances default to the definition's ``balancing`` section.
        """
        solve = engine_section("balancing", self.definition)
        if tolerance is None:
            tolerance = float(solve.get("solve_tolerance", 0.0))
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

        passes = max_passes or max(int(solve.get("min_passes", 1)), n_periods + int(solve.get("extra_passes", 0)))
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
            if previous is not None and np.allclose(
                current, previous, rtol=float(solve.get("solve_rtol", 0.0)), atol=tolerance
            ):
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
                if item.formula is not None and item.line_type == LineItemType.CALCULATED:
                    from financial_model.core.expressions import evaluate

                    ctx = self.formula_context(combined, period_idx, drivers, key)
                    val = evaluate(item.formula, ctx)
                else:
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
        return ExcelExporter().export_result(result, path, model=self, extras=self.all_tables(result))

    def export_csv(self, output_dir: str | Path, result: ModelResult | None = None) -> dict[str, Path]:
        """Export one CSV per statement and per analysis table into ``output_dir``."""
        from financial_model.exporters.csv_exporter import CSVExporter

        result = result or self.calculate()
        return CSVExporter().export_result(result, output_dir, extras=self.all_tables(result))

    def export_json(self, path: str | Path, result: ModelResult | None = None) -> Path:
        """Export statements, balance checks and analysis tables to JSON."""
        from financial_model.exporters.json_exporter import JSONExporter

        result = result or self.calculate()
        return JSONExporter().export_result(result, path, extras=self.all_tables(result))

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
        if item.formula is not None:
            d["formula"] = item.formula
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
        model = cls(name=config.get("name"))

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
