"""
Safe formula language for line items defined in YAML (model definitions and model configs).

A formula is one arithmetic expression, evaluated once per period::

    formula: "prev('Revenue', starting_revenue) * (1 + revenue_growth)"
    formula: "-line('Revenue') * cogs_pct"
    formula: "safe_div(line('Gross Profit'), line('Revenue'))"
    formula: "max(line('Cash & Cash Equivalents') - min_cash, 0) if t > 0 else 0"

Names
    A bare name is a driver's value in the current period (``revenue_growth``), one of
    ``t`` (period index, 0-based), ``n_periods``, ``periods_per_year``, ``year``, or a named
    expression from the definition's ``expressions`` section (drivers win on a clash).
Functions
    ``line(name, offset=0)``     a line's value (``offset=-1`` = previous period; 0 before the first)
    ``prev(name, default=0)``    previous period's value, ``default`` in the first period
    ``opening(name)``            the line's opening balance (before the first period)
    ``delta(name)``              change versus the previous period (or the opening balance)
    ``cumulative(name)``         sum of the line from the first period to this one
    ``driver(name, offset=0)``   a driver by name, optionally another period's value
    ``total(a, b, ...)``         sum of several lines
    ``running(expr, start=0)``   ``start`` plus ``expr`` evaluated in every period up to this one
                                 (e.g. ``running(abs(capex), opening('Goodwill'))``)
    ``safe_div(a, b, default=0)``, ``clamp(x, lo, hi)``, ``min``, ``max``, ``abs``, ``round``
Operators
    ``+ - * / // % **``, comparisons, ``and`` / ``or`` / ``not``, ``a if cond else b``.

Only those constructs are accepted: no attribute access, subscripts, imports, lambdas
or comprehensions, so formulas from untrusted configs (e.g. API requests) are safe.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

import pandas as pd

from financial_model.core.line_item import Driver, lookup_value

MAX_LENGTH = 4000  # characters per formula; guards against pathological inputs
MAX_DEPTH = 32  # nesting of named expressions


class FormulaError(ValueError):
    """A formula is invalid or failed to evaluate."""


_BINOPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type, Callable[[Any], Any]] = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Not: operator.not_}
_COMPARE: dict[type, Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}
FUNCTIONS = frozenset(
    {
        "line",
        "prev",
        "opening",
        "delta",
        "cumulative",
        "driver",
        "total",
        "running",
        "safe_div",
        "clamp",
        "min",
        "max",
        "abs",
        "round",
    }
)
SPECIAL_NAMES = frozenset({"t", "n_periods", "periods_per_year", "year"})


def _check(node: ast.AST, source: str) -> None:
    allowed = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Compare,
        ast.IfExp,
        ast.Call,
        ast.Name,
        ast.Constant,
        ast.Load,
        ast.And,
        ast.Or,
        *_BINOPS,
        *_UNARY,
        *_COMPARE,
    )
    for child in ast.walk(node):
        if not isinstance(child, allowed):
            raise FormulaError(f"'{type(child).__name__}' is not allowed in formulas: {source}")
        if isinstance(child, ast.Call):
            if not isinstance(child.func, ast.Name) or child.func.id not in FUNCTIONS:
                raise FormulaError(f"unknown function in formula (allowed: {', '.join(sorted(FUNCTIONS))}): {source}")
            if child.keywords:
                raise FormulaError(f"keyword arguments are not supported in formulas: {source}")
        if isinstance(child, ast.Constant) and not isinstance(child.value, (int, float, str, bool, type(None))):
            raise FormulaError(f"unsupported constant in formula: {source}")


@lru_cache(maxsize=4096)
def compile_formula(source: str) -> ast.Expression:
    """Parse and validate a formula (cached)."""
    if not isinstance(source, str) or not source.strip():
        raise FormulaError("formula must be a non-empty string")
    if len(source) > MAX_LENGTH:
        raise FormulaError(f"formula longer than {MAX_LENGTH} characters")
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"invalid formula syntax: {source} ({exc.msg})") from exc
    _check(tree, source)
    return tree


@dataclass
class Context:
    """What a formula can see in one period."""

    data: pd.DataFrame  # combined statements (MultiIndex: statement, line)
    idx: int
    drivers: dict[str, Driver]
    statement: str | None = None
    n_periods: int = 0
    periods_per_year: int = 1
    year: int = 0
    opening: Callable[[str], float] | None = None
    expressions: dict[str, str] | None = None  # named expressions usable as bare names
    depth: int = 0  # nesting of named expressions (guards against cycles)


class _Evaluator:
    def __init__(self, ctx: Context, source: str):
        self.ctx, self.source = ctx, source

    # -- data access ---------------------------------------------------------
    def _line(self, name: str, idx: int) -> float:
        if idx < 0:
            return 0.0
        data = self.ctx.data
        if self.ctx.statement and isinstance(data.index, pd.MultiIndex) and (self.ctx.statement, name) in data.index:
            val = data.loc[(self.ctx.statement, name)].iloc[idx]
            return 0.0 if pd.isna(val) else float(val)
        return float(lookup_value(data, name, idx, statement=None, default=0.0))

    def _driver(self, name: str, idx: int) -> float:
        drv = self.ctx.drivers.get(name)
        if drv is None:
            raise FormulaError(f"unknown driver '{name}' in formula: {self.source}")
        return drv.get_value(max(idx, 0))

    def _opening(self, name: str) -> float:
        return float(self.ctx.opening(name)) if self.ctx.opening else 0.0

    def call(self, fn: str, args: list[Any]) -> Any:
        i = self.ctx.idx

        def name_arg(k: int = 0) -> str:
            if len(args) <= k or not isinstance(args[k], str):
                raise FormulaError(f"{fn}() needs a line or driver name in quotes: {self.source}")
            return str(args[k])

        if fn == "line":
            return self._line(name_arg(), i + int(args[1] if len(args) > 1 else 0))
        if fn == "prev":
            default = float(args[1]) if len(args) > 1 else 0.0
            return self._line(name_arg(), i - 1) if i > 0 else default
        if fn == "opening":
            return self._opening(name_arg())
        if fn == "delta":
            name = name_arg()
            before = self._line(name, i - 1) if i > 0 else self._opening(name)
            return self._line(name, i) - before
        if fn == "cumulative":
            name = name_arg()
            return sum(self._line(name, k) for k in range(i + 1))
        if fn == "driver":
            return self._driver(name_arg(), i + int(args[1] if len(args) > 1 else 0))
        if fn == "total":
            return sum(self._line(str(a), i) for a in args)
        if fn == "safe_div":
            if len(args) < 2:
                raise FormulaError(f"safe_div(a, b[, default]) needs two arguments: {self.source}")
            return args[0] / args[1] if args[1] else (args[2] if len(args) > 2 else 0.0)
        if fn == "clamp":
            if len(args) != 3:
                raise FormulaError(f"clamp(x, lo, hi) needs three arguments: {self.source}")
            return min(max(args[0], args[1]), args[2])
        if fn == "min":
            return min(args)
        if fn == "max":
            return max(args)
        if fn == "abs":
            return abs(args[0])
        if fn == "round":
            return round(args[0], int(args[1]) if len(args) > 1 else 0)
        raise FormulaError(f"unknown function {fn}")  # pragma: no cover - rejected at compile time

    # -- tree walk -----------------------------------------------------------
    def eval(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return self.eval(node.body)
        if isinstance(node, ast.Constant):
            return (
                float(node.value)
                if isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
                else node.value
            )
        if isinstance(node, ast.Name):
            ctx = self.ctx
            special = {
                "t": ctx.idx,
                "n_periods": ctx.n_periods,
                "periods_per_year": ctx.periods_per_year,
                "year": ctx.year,
            }
            if node.id in special:
                return float(special[node.id])
            if node.id not in ctx.drivers and ctx.expressions and node.id in ctx.expressions:
                if ctx.depth >= MAX_DEPTH:
                    raise FormulaError(f"named expressions nested deeper than {MAX_DEPTH} (a cycle?): {self.source}")
                sub = ctx.expressions[node.id]
                return _Evaluator(replace(ctx, depth=ctx.depth + 1), sub).eval(compile_formula(sub))
            return self._driver(node.id, ctx.idx)
        if isinstance(node, ast.BinOp):
            left, right = self.eval(node.left), self.eval(node.right)
            try:
                return _BINOPS[type(node.op)](left, right)
            except ZeroDivisionError:
                return 0.0  # spreadsheet-style: x / 0 = 0 (use safe_div for another default)
            except OverflowError as exc:
                raise FormulaError(f"numeric overflow in formula: {self.source}") from exc
        if isinstance(node, ast.UnaryOp):
            return _UNARY[type(node.op)](self.eval(node.operand))
        if isinstance(node, ast.BoolOp):
            values = [self.eval(v) for v in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = self.eval(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right = self.eval(comparator)
                if not _COMPARE[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self.eval(node.body) if self.eval(node.test) else self.eval(node.orelse)
        if isinstance(node, ast.Call):
            assert isinstance(node.func, ast.Name)
            if node.func.id == "running":  # arguments evaluated lazily, once per period
                if not 1 <= len(node.args) <= 2:
                    raise FormulaError(f"running(expr[, start]) needs one or two arguments: {self.source}")
                total = self.eval(node.args[1]) if len(node.args) > 1 else 0.0
                for k in range(self.ctx.idx + 1):
                    total += _Evaluator(replace(self.ctx, idx=k), self.source).eval(node.args[0])
                return total
            return self.call(node.func.id, [self.eval(a) for a in node.args])
        raise FormulaError(f"unsupported expression: {self.source}")  # pragma: no cover


def evaluate(source: str, ctx: Context) -> float:
    """Evaluate ``source`` in ``ctx`` and return a float (booleans become 1/0)."""
    value = _Evaluator(ctx, source).eval(compile_formula(source))
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, str):
        raise FormulaError(f"formula evaluated to text, not a number: {source}")
    result = float(value)
    if math.isnan(result) or math.isinf(result):
        raise FormulaError(f"formula produced {result}: {source}")
    return result
