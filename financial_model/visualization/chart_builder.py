"""
Plotly charts for model results.

``ChartBuilder.model_charts(model, result)`` returns ``{name: Figure}`` for any model
type; ``export_dashboard`` writes them to one self-contained HTML page (plotly.js
embedded, so it works offline / self-hosted) and ``export_all`` writes one file each.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go

from financial_model.core.model import FinancialModel, ModelResult

# Categorical palette, assigned in order; one meaning per colour within a chart.
PALETTE = ["#2563eb", "#16a34a", "#ea580c", "#9333ea", "#0891b2", "#dc2626", "#ca8a04", "#64748b"]
POSITIVE = "#16a34a"
NEGATIVE = "#dc2626"
TOTAL = "#2563eb"

THEMES = {"light": "plotly_white", "dark": "plotly_dark"}


@dataclass
class ChartConfig:
    """Shared chart styling."""

    width: int | None = None  # None = responsive
    height: int = 460
    font_family: str = "Inter, system-ui, -apple-system, sans-serif"
    font_size: int = 12
    theme: str = "light"

    @property
    def template(self) -> str:
        return THEMES.get(self.theme, self.theme)

    def apply_layout(self, fig: go.Figure, title: str, yaxis_title: str = "", yformat: str = ",.0f") -> go.Figure:
        fig.update_layout(
            title={"text": title, "x": 0.01, "xanchor": "left"},
            width=self.width,
            height=self.height,
            autosize=self.width is None,
            font={"family": self.font_family, "size": self.font_size},
            template=self.template,
            legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
            margin={"l": 70, "r": 30, "t": 80, "b": 50},
            hovermode="x unified",
        )
        fig.update_yaxes(title_text=yaxis_title, tickformat=yformat, zeroline=True)
        return fig


def _row(df: pd.DataFrame, line: str) -> list[float] | None:
    if line not in df.index:
        return None
    return [0.0 if pd.isna(v) else float(v) for v in df.loc[line].tolist()]


def _safe_div(a: list[float], b: list[float]) -> list[float | None]:
    return [x / y if y else None for x, y in zip(a, b, strict=False)]


class ChartBuilder:
    """Build interactive Plotly charts from model results."""

    def __init__(self, config: ChartConfig | None = None, theme: str | None = None) -> None:
        self.config = config or ChartConfig()
        if theme is not None:
            self.config.theme = theme

    @property
    def theme(self) -> str:
        return self.config.theme

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    def model_charts(
        self, model: Any, result: ModelResult | None = None, scenario: str | None = None
    ) -> dict[str, go.Figure]:
        """All charts that apply to ``model`` (statements, plus DCF / LBO / budget / M&A views)."""
        from financial_model.models.budget import BudgetModel
        from financial_model.models.dcf import DCFModel
        from financial_model.models.lbo import LBOModel
        from financial_model.models.ma import MergerModel

        if isinstance(model, MergerModel):
            return self.merger_charts(model, scenario)
        if not isinstance(model, FinancialModel):
            raise TypeError(f"Unsupported model: {type(model).__name__}")

        result = result or model.calculate(scenario=scenario)
        if isinstance(model, BudgetModel):
            charts = self.budget_charts(result)
        else:
            charts = self.statement_charts(result)
        if isinstance(model, DCFModel):
            charts.update(self.dcf_charts(model, result))
        if isinstance(model, LBOModel):
            charts.update(self.lbo_charts(model, result))
        return charts

    # ------------------------------------------------------------------ #
    # Statements
    # ------------------------------------------------------------------ #
    def statement_charts(self, result: ModelResult) -> dict[str, go.Figure]:
        labels = result.labels
        is_, bs, cf = result.income_statement, result.balance_sheet, result.cash_flow
        charts: dict[str, go.Figure] = {}

        lines = ["Revenue", "Gross Profit", "Operating Income", "Net Income"]
        fig = self.bar_chart(labels, {line: _row(is_, line) for line in lines}, "Revenue & Profitability", "$")
        if fig is not None:
            charts["profitability"] = fig

        revenue = _row(is_, "Revenue")
        if revenue:
            margins = {
                f"{name} Margin": _safe_div(vals, revenue)
                for name, vals in (
                    ("Gross", _row(is_, "Gross Profit")),
                    ("Operating", _row(is_, "Operating Income")),
                    ("Net", _row(is_, "Net Income")),
                )
                if vals is not None
            }
            fig = self.line_chart(labels, margins, "Margins", "% of revenue", yformat=".0%")
            if fig is not None:
                charts["margins"] = fig

        flows = {
            name: _row(cf, line)
            for name, line in (
                ("Operating", "Operating Cash Flow"),
                ("Investing", "Investing Cash Flow"),
                ("Financing", "Financing Cash Flow"),
            )
        }
        fig = self.bar_chart(labels, flows, "Cash Flow by Activity", "$", barmode="relative")
        cash = _row(bs, "Cash & Cash Equivalents")
        if fig is not None:
            if cash is not None:
                fig.add_trace(
                    go.Scatter(x=labels, y=cash, name="Ending Cash", mode="lines+markers", line={"color": "#0f172a"})
                )
            charts["cash_flow"] = fig

        totals = {"Total Assets": _row(bs, "Total Assets"), "Total Liabilities": _row(bs, "Total Liabilities")}
        totals["Total Equity"] = _row(bs, "Total Equity")
        fig = self.bar_chart(labels, totals, "Balance Sheet", "$")
        if fig is not None:
            charts["balance_sheet"] = fig
        return charts

    def budget_charts(self, result: ModelResult) -> dict[str, go.Figure]:
        labels = result.labels
        is_ = result.income_statement
        charts: dict[str, go.Figure] = {}

        arr = {line: _row(is_, line) for line in ("New ARR", "Expansion ARR", "Churned ARR")}
        fig = self.bar_chart(labels, arr, "ARR Movements", "$", barmode="relative")
        ending = _row(is_, "Ending ARR")
        if fig is not None:
            if ending is not None:
                fig.add_trace(go.Scatter(x=labels, y=ending, name="Ending ARR", mode="lines+markers", yaxis="y2"))
                fig.update_layout(yaxis2={"overlaying": "y", "side": "right", "tickformat": ",.0f", "showgrid": False})
            charts["arr"] = fig

        expense_lines = [n for n in is_.index if str(n).endswith(" Expense") and not str(n).startswith("Total")]
        headcount_lines = [n for n in is_.index if str(n).endswith(" Headcount")]
        fig = self.bar_chart(
            labels,
            {n: [abs(v) for v in (_row(is_, n) or [])] for n in expense_lines},
            "Operating Expense by Department",
            "$",
            barmode="stack",
        )
        if fig is not None:
            charts["opex"] = fig
        fig = self.bar_chart(
            labels,
            {n.replace(" Headcount", ""): _row(is_, n) for n in headcount_lines},
            "Headcount by Department",
            "FTE",
            barmode="stack",
        )
        if fig is not None:
            charts["headcount"] = fig

        fig = self.bar_chart(labels, {"Revenue": _row(is_, "Revenue")}, "Revenue vs Operating Income", "$")
        if fig is not None:
            oi = _row(is_, "Operating Income")
            if oi is not None:
                fig.add_trace(go.Scatter(x=labels, y=oi, name="Operating Income", mode="lines+markers"))
            charts["revenue"] = fig
        return charts

    # ------------------------------------------------------------------ #
    # Valuation / transactions
    # ------------------------------------------------------------------ #
    def dcf_charts(self, model: Any, result: ModelResult) -> dict[str, go.Figure]:
        dcf = model.calculate_dcf(result)
        charts: dict[str, go.Figure] = {}

        fcf = self.bar_chart(
            dcf["periods"], {"Free Cash Flow": dcf["fcf"], "PV of FCF": dcf["pv_fcf"]}, "Unlevered Free Cash Flow", "$"
        )
        if fcf is not None:
            charts["dcf_fcf"] = fcf

        steps = [
            ("PV of FCF", sum(dcf["pv_fcf"]), "relative"),
            ("PV of Terminal Value", dcf["pv_terminal_gordon"], "relative"),
            ("Enterprise Value", dcf["ev_gordon"], "total"),
            ("Less: Net Debt", -dcf["net_debt"], "relative"),
            ("Equity Value", dcf["equity_gordon"], "total"),
        ]
        charts["dcf_bridge"] = self.waterfall(steps, "EV to Equity Bridge (Gordon growth)")

        sens = model.sensitivity_analysis(result)
        heat = go.Figure(
            go.Heatmap(
                z=sens.to_numpy(dtype=float),
                x=[str(c) for c in sens.columns],
                y=[str(i) for i in sens.index],
                colorscale="Blues",
                text=[[f"{v:,.0f}" for v in row] for row in sens.to_numpy(dtype=float)],
                texttemplate="%{text}",
                hovertemplate="WACC %{x}<br>Growth %{y}<br>Equity %{z:,.0f}<extra></extra>",
                colorbar={"title": "Equity"},
            )
        )
        self.config.apply_layout(heat, "Equity Value Sensitivity", "Terminal growth")
        heat.update_xaxes(title_text="WACC")
        heat.update_layout(hovermode="closest")
        charts["dcf_sensitivity"] = heat
        return charts

    def lbo_charts(self, model: Any, result: ModelResult) -> dict[str, go.Figure]:
        from financial_model.models.lbo import TRANCHES

        schedule = model.debt_schedule(result)
        labels = list(schedule.index)
        charts: dict[str, go.Figure] = {}
        series = {name: schedule[name].tolist() for name in [*TRANCHES, "Revolver"] if name in schedule}
        fig = self.bar_chart(labels, series, "Debt Paydown by Tranche", "$", barmode="stack")
        if fig is not None:
            fig.add_trace(
                go.Scatter(
                    x=labels, y=schedule["Cash"].tolist(), name="Cash", mode="lines+markers", line={"color": "#0f172a"}
                )
            )
            charts["lbo_debt"] = fig

        su = model.sources_uses(result)
        sources = su["sources"]
        sources = sources[sources["Amount"] > 0]
        pie = go.Figure(
            go.Pie(labels=sources["Source"], values=sources["Amount"], hole=0.5, marker={"colors": PALETTE}, sort=False)
        )
        self.config.apply_layout(pie, "Sources of Funds")
        charts["lbo_sources"] = pie

        ret = model.calculate_returns(result)
        steps = [
            ("Equity Invested", ret["equity_invested"], "absolute"),
            ("Value Created", ret["equity_value_at_exit"] - ret["equity_invested"], "relative"),
            ("Equity at Exit", ret["equity_value_at_exit"], "total"),
        ]
        fig = self.waterfall(steps, f"Sponsor Returns — {ret['moic']:.2f}x MOIC, {ret['irr']:.1%} IRR")
        charts["lbo_returns"] = fig
        return charts

    def merger_charts(self, model: Any, scenario: str | None = None) -> dict[str, go.Figure]:
        tables = model.analysis_tables(scenario)
        ad = tables["Accretion Dilution"]
        labels = [str(i) for i in ad.index]
        pct = ad["accretion_dilution_pct"].tolist()
        bar = go.Figure(
            go.Bar(
                x=labels,
                y=pct,
                marker={"color": [POSITIVE if v >= 0 else NEGATIVE for v in pct]},
                name="Accretion / (Dilution)",
                text=[f"{v:+.1%}" for v in pct],
                textposition="outside",
            )
        )
        self.config.apply_layout(bar, "EPS Accretion / (Dilution)", "% vs standalone", yformat=".0%")
        eps = self.line_chart(
            labels,
            {"Standalone EPS": ad["acquirer_eps"].tolist(), "Pro Forma EPS": ad["pf_eps"].tolist()},
            "EPS",
            "$",
            ",.2f",
        )
        contrib = tables["Contribution"]
        mix = self.bar_chart(
            [str(i) for i in contrib.index],
            {"Acquirer": contrib["Acquirer"].tolist(), "Target": contrib["Target"].tolist()},
            "Year-1 Contribution",
            "$",
            barmode="stack",
        )
        charts = {"ma_accretion": bar}
        if eps is not None:
            charts["ma_eps"] = eps
        if mix is not None:
            charts["ma_contribution"] = mix
        return charts

    # ------------------------------------------------------------------ #
    # Primitives
    # ------------------------------------------------------------------ #
    def bar_chart(
        self,
        x: list[str],
        series: Mapping[str, Sequence[Any] | None],
        title: str,
        yaxis_title: str = "",
        barmode: str = "group",
        yformat: str = ",.0f",
    ) -> go.Figure | None:
        """Bar chart of the non-empty series (None when there is nothing to plot)."""
        present = {k: v for k, v in series.items() if v}
        if not present:
            return None
        fig = go.Figure()
        for i, (name, values) in enumerate(present.items()):
            fig.add_trace(go.Bar(x=x, y=values, name=name, marker_color=PALETTE[i % len(PALETTE)]))
        fig.update_layout(barmode=barmode)
        return self.config.apply_layout(fig, title, yaxis_title, yformat)

    def line_chart(
        self,
        x: list[str],
        series: Mapping[str, Sequence[Any] | None],
        title: str,
        yaxis_title: str = "",
        yformat: str = ",.0f",
    ) -> go.Figure | None:
        present = {k: v for k, v in series.items() if v}
        if not present:
            return None
        fig = go.Figure()
        for i, (name, values) in enumerate(present.items()):
            fig.add_trace(
                go.Scatter(x=x, y=values, name=name, mode="lines+markers", line={"color": PALETTE[i % len(PALETTE)]})
            )
        return self.config.apply_layout(fig, title, yaxis_title, yformat)

    def waterfall(self, steps: list[tuple[str, float, str]], title: str) -> go.Figure:
        """Waterfall from ``(label, value, measure)`` with measure in absolute/relative/total."""
        fig = go.Figure(
            go.Waterfall(
                x=[s[0] for s in steps],
                y=[s[1] for s in steps],
                measure=[s[2] for s in steps],
                text=[f"{s[1]:,.0f}" for s in steps],
                textposition="outside",
                increasing={"marker": {"color": POSITIVE}},
                decreasing={"marker": {"color": NEGATIVE}},
                totals={"marker": {"color": TOTAL}},
                connector={"line": {"color": "#94a3b8"}},
            )
        )
        self.config.apply_layout(fig, title, "$")
        fig.update_layout(showlegend=False, hovermode="closest")
        return fig

    # ------------------------------------------------------------------ #
    # Output
    # ------------------------------------------------------------------ #
    @staticmethod
    def to_html(fig: go.Figure, filename: str | Path | None = None, offline: bool = True) -> str:
        """HTML for one figure; written to ``filename`` when given."""
        markup = fig.to_html(full_html=True, include_plotlyjs=True if offline else "cdn")
        if filename is not None:
            Path(filename).parent.mkdir(parents=True, exist_ok=True)
            Path(filename).write_text(markup, encoding="utf-8")
        return markup

    def export_dashboard(
        self, charts: dict[str, go.Figure], path: str | Path, title: str = "Model Charts", offline: bool = True
    ) -> Path:
        """Write every chart to one HTML page (plotly.js embedded once when ``offline``)."""
        if not charts:
            raise ValueError("no charts to export")
        parts = []
        for i, fig in enumerate(charts.values()):
            include = (True if offline else "cdn") if i == 0 else False
            parts.append(f'<section class="chart">{fig.to_html(full_html=False, include_plotlyjs=include)}</section>')
        bg, fg = ("#0b1120", "#e2e8f0") if self.theme == "dark" else ("#f8fafc", "#0f172a")
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ margin: 0; padding: 24px 16px; background: {bg}; color: {fg}; font-family: {self.config.font_family}; }}
h1 {{ font-size: 22px; margin: 0 auto 16px; max-width: 1100px; }}
.chart {{ max-width: 1100px; margin: 0 auto 24px; }}
</style></head>
<body><h1>{html.escape(title)}</h1>
{"".join(parts)}
</body></html>"""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding="utf-8")
        return out.resolve()

    def export_all(self, model: Any, output_dir: str | Path = "charts", offline: bool = True) -> dict[str, Path]:
        """Write one HTML file per chart into ``output_dir``."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        saved: dict[str, Path] = {}
        for name, fig in self.model_charts(model).items():
            path = out / f"{name}.html"
            self.to_html(fig, path, offline=offline)
            saved[name] = path
        return saved
