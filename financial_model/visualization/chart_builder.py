"""
Plotly charts for model results.

Which charts a model has, and what each one plots, is data: the ``charts`` section of the
model's definition (``models/definitions/<model_type>.yaml``, inherited through ``extends``).
``ChartBuilder.model_charts(model, result)`` returns ``{name: Figure}`` for any model
type; ``export_dashboard`` writes them to one self-contained HTML page (plotly.js
embedded, so it works offline / self-hosted) and ``export_all`` writes one file each.
"""

from __future__ import annotations

import html
import string
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go

from financial_model.core.expressions import evaluate_values
from financial_model.core.model import FinancialModel, ModelResult


def chart_style() -> dict[str, Any]:
    """The ``presentation.charts`` settings (financial_model/settings.yaml, overridable)."""
    from financial_model.config_loader import get_config

    return dict((get_config().get("presentation") or {}).get("charts") or {})


@dataclass
class ChartConfig:
    """Shared chart styling. Unset fields come from the ``presentation.charts`` settings."""

    width: int | None = None  # None = responsive
    height: int | None = None
    font_family: str | None = None
    font_size: int | None = None
    theme: str | None = None
    style: dict[str, Any] = field(default_factory=chart_style)

    def __post_init__(self) -> None:
        st = self.style
        if self.width is None:
            self.width = st.get("width")
        self.height = self.height if self.height is not None else st.get("height")
        self.font_family = self.font_family or st.get("font_family")
        self.font_size = self.font_size if self.font_size is not None else st.get("font_size")
        self.theme = self.theme or st.get("theme") or "light"

    @property
    def template(self) -> str:
        themes = self.style.get("themes") or {}
        return str(themes.get(self.theme, self.theme))

    @property
    def palette(self) -> list[str]:
        return list(self.style.get("palette") or [None])

    def color(self, role: str) -> Any:
        return self.style.get(role)

    def apply_layout(self, fig: go.Figure, title: str, yaxis_title: str = "", yformat: str | None = None) -> go.Figure:
        st = self.style
        fig.update_layout(
            title={"text": title, "x": 0.01, "xanchor": "left"},
            width=self.width,
            height=self.height,
            autosize=self.width is None,
            font={"family": self.font_family, "size": self.font_size},
            template=self.template,
            legend=st.get("legend"),
            margin=st.get("margin"),
            hovermode=st.get("hovermode"),
        )
        fig.update_yaxes(title_text=yaxis_title, tickformat=yformat or st.get("value_format"), zeroline=True)
        return fig


def _row(df: pd.DataFrame, line: str) -> list[float] | None:
    if line not in df.index:
        return None
    return [0.0 if pd.isna(v) else float(v) for v in df.loc[line].tolist()]


def _safe_div(a: list[float], b: list[float]) -> list[float | None]:
    # zip(strict=) is Python 3.10+; unequal lengths truncate, as intended here.
    return [x / y if y else None for x, y in zip(a, b)]  # noqa: B905


# ---------------------------------------------------------------------- #
# Chart sources: where a chart spec's numbers come from
# ---------------------------------------------------------------------- #
#: ``name -> (fn(model, result, scenario) -> DataFrame | dict, orientation)``. ``rows``: series are
#: the table's rows and x is the result's period labels (statements); ``columns``: series are columns
#: and x is the index; a dict is a set of named values (``x:`` names the key holding the x labels).
ChartSourceFn = Callable[[Any, Any, "str | None"], Any]
CHART_SOURCES: dict[str, tuple[ChartSourceFn, str]] = {}


def register_chart_source(name: str, fn: ChartSourceFn, orientation: str = "columns") -> None:
    """Make ``source: <name>`` available to chart specs (``orientation``: ``rows`` or ``columns``)."""
    if orientation not in ("rows", "columns"):
        raise ValueError("orientation must be 'rows' or 'columns'")
    CHART_SOURCES[name] = (fn, orientation)


def _statement_source(attr: str) -> ChartSourceFn:
    return lambda model, result, scenario: getattr(result, attr)  # noqa: ARG005


for _name in ("income_statement", "balance_sheet", "cash_flow"):
    register_chart_source(_name, _statement_source(_name), "rows")
register_chart_source("dcf", lambda model, result, scenario: model.calculate_dcf(result))  # noqa: ARG005
register_chart_source("dcf_sensitivity", lambda model, result, scenario: model.sensitivity_analysis(result))  # noqa: ARG005
register_chart_source("debt_schedule", lambda model, result, scenario: model.debt_schedule(result))  # noqa: ARG005
register_chart_source("sources_uses", lambda model, result, scenario: model.sources_uses(result))  # noqa: ARG005
register_chart_source("returns", lambda model, result, scenario: model.calculate_returns(result))  # noqa: ARG005
register_chart_source(
    "analysis",
    lambda model, result, scenario: model.analysis_tables(result if isinstance(model, FinancialModel) else scenario),
)


class ChartSpecError(ValueError):
    """A chart spec in a model definition is invalid."""


class _Data:
    """One resolved source: ``x`` labels, ``get(key)`` series, ``keys`` in order, ``values`` for text."""

    def __init__(self, raw: Any, orientation: str, labels: list[Any], x_key: str | None) -> None:
        self.raw, self.values = raw, raw if isinstance(raw, Mapping) else {}
        if isinstance(raw, pd.DataFrame):
            if orientation == "rows":
                self.x, self.keys = list(labels), [str(k) for k in raw.index]
            else:
                self.x, self.keys = [str(i) for i in raw.index], [str(c) for c in raw.columns]
        else:
            self.x = list(raw.get(x_key) or []) if x_key else []
            self.keys = [str(k) for k in raw]
        self.orientation = orientation

    def get(self, key: str) -> list[float] | None:
        raw = self.raw
        if isinstance(raw, pd.DataFrame):
            if self.orientation == "rows":
                return _row(raw, key)
            return raw[key].tolist() if key in raw.columns else None
        value = raw.get(key)
        return None if value is None else list(value)


def _format(template: Any, values: Mapping[str, Any]) -> str:
    """``str.format`` restricted to plain names (no attribute or index access)."""
    text = str(template or "")
    fields = [f for _, f, _, _ in string.Formatter().parse(text) if f is not None]
    if not fields:
        return text
    for f in fields:
        if not f.isidentifier() or f not in values:
            raise ChartSpecError(f"unknown name '{f}' in chart text: {text}")
    return text.format_map(values)


def _as_item(item: Any) -> dict[str, Any]:
    return {"line": item} if isinstance(item, str) else dict(item or {})


def _key(item: Mapping[str, Any]) -> str | None:
    for k in ("line", "column", "key"):
        if item.get(k) is not None:
            return str(item[k])
    return None


def _matches(key: str, rule: Mapping[str, Any]) -> bool:
    checks = (
        ("prefix", lambda v: key.startswith(v)),
        ("suffix", lambda v: key.endswith(v)),
        ("contains", lambda v: v in key),
        ("exclude_prefix", lambda v: not key.startswith(v)),
        ("exclude_suffix", lambda v: not key.endswith(v)),
    )
    return all(test(str(rule[name])) for name, test in checks if rule.get(name) is not None)


class ChartBuilder:
    """Build interactive Plotly charts from model results, as described by each model's ``charts`` definition."""

    def __init__(self, config: ChartConfig | None = None, theme: str | None = None) -> None:
        self.config = config or ChartConfig()
        if theme is not None:
            self.config.theme = theme

    @property
    def theme(self) -> str:
        return str(self.config.theme)

    # ------------------------------------------------------------------ #
    # Entry points
    # ------------------------------------------------------------------ #
    def model_charts(
        self, model: Any, result: ModelResult | None = None, scenario: str | None = None
    ) -> dict[str, go.Figure]:
        """Every chart in the model definition's ``charts`` section that has data."""
        return self.charts_for(model, result, scenario)

    def charts_for(
        self,
        model: Any,
        result: ModelResult | None = None,
        scenario: str | None = None,
        group: str | None = None,
        specs: Mapping[str, Any] | None = None,
    ) -> dict[str, go.Figure]:
        """Charts of ``specs`` (default: the model's definition), optionally only one ``group``."""
        definition = getattr(model, "definition", None)
        if not isinstance(definition, Mapping):
            raise TypeError(f"Unsupported model: {type(model).__name__}")
        if isinstance(model, FinancialModel):
            result = result or model.calculate(scenario=scenario)
        specs = definition.get("charts") if specs is None else specs
        cache: dict[tuple[str, str | None], _Data] = {}
        charts: dict[str, go.Figure] = {}
        for name, spec in (specs or {}).items():
            if not isinstance(spec, Mapping) or (group is not None and spec.get("group") != group):
                continue
            fig = self.chart_from_spec(spec, lambda sp: self._data(model, result, scenario, sp, cache), model)
            if fig is not None:
                charts[str(name)] = fig
        return charts

    def _data(
        self, model: Any, result: Any, scenario: str | None, spec: Mapping[str, Any], cache: dict[Any, _Data]
    ) -> _Data:
        source = str(spec.get("source") or "")
        if source not in CHART_SOURCES:
            raise ChartSpecError(f"unknown chart source '{source}' (available: {', '.join(sorted(CHART_SOURCES))})")
        table = spec.get("table")
        key = (source, None if table is None else str(table), spec.get("x"))
        if key not in cache:
            raw_key = (source, None, None)
            if raw_key not in cache:
                fn, orientation = CHART_SOURCES[source]
                labels = result.labels if result is not None else []
                cache[raw_key] = _Data(fn(model, result, scenario), orientation, labels, None)
            base = cache[raw_key]
            raw = base.raw[str(table)] if table is not None else base.raw
            cache[key] = _Data(raw, base.orientation, base.x if table is None else [], spec.get("x"))
        return cache[key]

    def chart_from_spec(
        self, spec: Mapping[str, Any], data: Callable[[Mapping[str, Any]], _Data], model: Any = None
    ) -> go.Figure | None:
        """One figure from a chart spec (None when it has nothing to plot)."""
        kind = str(spec.get("kind") or "bar")
        builder = {
            "bar": self._spec_series,
            "line": self._spec_series,
            "waterfall": self._spec_waterfall,
            "heatmap": self._spec_heatmap,
            "pie": self._spec_pie,
        }.get(kind)
        if builder is None:
            raise ChartSpecError(f"unknown chart kind '{kind}' (bar, line, waterfall, heatmap, pie)")
        d = data(spec)
        if any(not d.get(str(k)) for k in spec.get("requires") or []):
            return None
        return builder(spec, d, data, model)

    def _series(self, spec: Mapping[str, Any], d: _Data, model: Any) -> dict[str, list[Any] | None]:
        groups = model.chart_groups() if hasattr(model, "chart_groups") else {}
        out: dict[str, list[Any] | None] = {}
        for raw_item in spec.get("series") or []:
            item = _as_item(raw_item)
            if item.get("group") is not None:
                keys = [str(k) for k in groups.get(str(item["group"])) or []]
            elif item.get("match") is not None:
                keys = [k for k in d.keys if _matches(k, item["match"])]
            else:
                keys = [k for k in [_key(item)] if k is not None]
            single = item.get("group") is None and item.get("match") is None
            for key in keys:
                values: list[Any] | None = d.get(key)
                if values is not None and item.get("divide_by") is not None:
                    den = d.get(str(item["divide_by"]))
                    values = _safe_div(values, den) if den is not None else None
                if values is not None and item.get("abs"):
                    values = [abs(v) for v in values]
                label = str(item["name"]) if single and item.get("name") is not None else key
                for old, new in (item.get("rename") or {}).items():
                    label = label.replace(str(old), str(new))
                out[label] = values
        return out

    def _spec_series(
        self, spec: Mapping[str, Any], d: _Data, data: Callable[[Mapping[str, Any]], _Data], model: Any
    ) -> go.Figure | None:
        series = self._series(spec, d, model)
        title, yaxis, yformat = _format(spec.get("title"), d.values), str(spec.get("yaxis") or ""), spec.get("yformat")
        if spec.get("kind") == "line":
            fig = self.line_chart(d.x, series, title, yaxis, yformat)
        elif spec.get("color_by_sign") or spec.get("text_format"):
            fig = self._labelled_bars(spec, d.x, series, title, yaxis, yformat)
        else:
            fig = self.bar_chart(d.x, series, title, yaxis, spec.get("barmode", "group"), yformat)
        if fig is None:
            return None
        for raw_overlay in spec.get("overlays") or []:
            ov = _as_item(raw_overlay)
            od = data(
                {**spec, "source": ov.get("source", spec.get("source")), "table": ov.get("table", spec.get("table"))}
            )
            key = _key(ov)
            values = od.get(key) if key else None
            if values is None:
                continue
            kwargs: dict[str, Any] = {"mode": ov.get("mode", "lines+markers")}
            if ov.get("color") is not None:
                kwargs["line"] = {"color": self.config.color(str(ov["color"]))}
            if ov.get("axis") is not None:
                kwargs["yaxis"] = str(ov["axis"])
            fig.add_trace(go.Scatter(x=od.x, y=values, name=str(ov.get("name") or key), **kwargs))
            if ov.get("layout"):
                fig.update_layout(**dict(ov["layout"]))
        return fig

    def _labelled_bars(
        self,
        spec: Mapping[str, Any],
        x: list[Any],
        series: Mapping[str, list[Any] | None],
        title: str,
        yaxis: str,
        yformat: str | None,
    ) -> go.Figure | None:
        present = {k: v for k, v in series.items() if v}
        if not present:
            return None
        fig = go.Figure()
        palette = self.config.palette
        text_format = spec.get("text_format")
        for i, (name, values) in enumerate(present.items()):
            if spec.get("color_by_sign"):
                color: Any = [self.config.color("positive" if v >= 0 else "negative") for v in values]
            else:
                color = palette[i % len(palette)]
            extra: dict[str, Any] = {}
            if text_format:
                extra = {
                    "text": [format(v, str(text_format)) for v in values],
                    "textposition": spec.get("text_position", "outside"),
                }
            fig.add_trace(go.Bar(x=x, y=values, name=name, marker={"color": color}, **extra))
        if spec.get("barmode", "group") is not None:
            fig.update_layout(barmode=spec.get("barmode", "group"))
        return self.config.apply_layout(fig, title, yaxis, yformat)

    def _spec_waterfall(
        self,
        spec: Mapping[str, Any],
        d: _Data,
        data: Callable[[Mapping[str, Any]], _Data],  # noqa: ARG002
        model: Any,  # noqa: ARG002
    ) -> go.Figure:
        steps = []
        for step in spec.get("steps") or []:
            value = evaluate_values(str(step["value"]), d.values)
            steps.append((_format(step.get("label"), d.values), value, str(step.get("measure") or "relative")))
        return self.waterfall(
            steps, _format(spec.get("title"), d.values), str(spec.get("yaxis") or ""), spec.get("text_format")
        )

    def _spec_heatmap(
        self,
        spec: Mapping[str, Any],
        d: _Data,
        data: Callable[[Mapping[str, Any]], _Data],  # noqa: ARG002
        model: Any,  # noqa: ARG002
    ) -> go.Figure | None:
        table = d.raw
        if not isinstance(table, pd.DataFrame) or table.empty:
            return None
        z = table.to_numpy(dtype=float)
        text_format = str(spec.get("text_format") or self.config.style.get("text_format") or "")
        heat = go.Figure(
            go.Heatmap(
                z=z,
                x=[str(c) for c in table.columns],
                y=[str(i) for i in table.index],
                colorscale=self.config.color(str(spec.get("colorscale") or "heatmap_scale")),
                text=[[format(v, text_format) for v in row] for row in z],
                texttemplate="%{text}",
                hovertemplate=spec.get("hovertemplate"),
                colorbar={"title": spec.get("colorbar")},
            )
        )
        self.config.apply_layout(heat, _format(spec.get("title"), d.values), str(spec.get("yaxis") or ""))
        heat.update_xaxes(title_text=spec.get("xaxis"))
        if spec.get("hovermode"):
            heat.update_layout(hovermode=spec["hovermode"])
        return heat

    def _spec_pie(
        self,
        spec: Mapping[str, Any],
        d: _Data,
        data: Callable[[Mapping[str, Any]], _Data],  # noqa: ARG002
        model: Any,  # noqa: ARG002
    ) -> go.Figure | None:
        table = d.raw
        labels_col, values_col = str(spec.get("labels")), str(spec.get("values"))
        if not isinstance(table, pd.DataFrame) or labels_col not in table or values_col not in table:
            return None
        if spec.get("positive_only"):
            table = table[table[values_col] > 0]
        pie = go.Figure(
            go.Pie(
                labels=table[labels_col],
                values=table[values_col],
                hole=spec.get("hole"),
                marker={"colors": self.config.palette},
                sort=bool(spec.get("sort", False)),
            )
        )
        self.config.apply_layout(pie, _format(spec.get("title"), d.values))
        return pie

    # ------------------------------------------------------------------ #
    # Chart groups of one definition (kept for callers of the earlier API)
    # ------------------------------------------------------------------ #
    def statement_charts(self, result: ModelResult) -> dict[str, go.Figure]:
        return self._definition_charts("three-statement", result)

    def budget_charts(self, result: ModelResult) -> dict[str, go.Figure]:
        return self._definition_charts("budget", result)

    def dcf_charts(self, model: Any, result: ModelResult) -> dict[str, go.Figure]:
        return self.charts_for(model, result, group="dcf")

    def lbo_charts(self, model: Any, result: ModelResult) -> dict[str, go.Figure]:
        return self.charts_for(model, result, group="lbo")

    def merger_charts(self, model: Any, scenario: str | None = None) -> dict[str, go.Figure]:
        return self.charts_for(model, None, scenario)

    def _definition_charts(self, model_type: str, result: ModelResult) -> dict[str, go.Figure]:
        from financial_model.core.definitions import load_definition

        holder = type("_Holder", (), {"definition": load_definition(model_type)})()
        return self.charts_for(holder, result)

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
        yformat: str | None = None,
    ) -> go.Figure | None:
        """Bar chart of the non-empty series (None when there is nothing to plot)."""
        present = {k: v for k, v in series.items() if v}
        if not present:
            return None
        fig = go.Figure()
        for i, (name, values) in enumerate(present.items()):
            fig.add_trace(
                go.Bar(x=x, y=values, name=name, marker_color=self.config.palette[i % len(self.config.palette)])
            )
        fig.update_layout(barmode=barmode)
        return self.config.apply_layout(fig, title, yaxis_title, yformat)

    def line_chart(
        self,
        x: list[str],
        series: Mapping[str, Sequence[Any] | None],
        title: str,
        yaxis_title: str = "",
        yformat: str | None = None,
    ) -> go.Figure | None:
        present = {k: v for k, v in series.items() if v}
        if not present:
            return None
        fig = go.Figure()
        for i, (name, values) in enumerate(present.items()):
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=values,
                    name=name,
                    mode="lines+markers",
                    line={"color": self.config.palette[i % len(self.config.palette)]},
                )
            )
        return self.config.apply_layout(fig, title, yaxis_title, yformat)

    def waterfall(
        self, steps: list[tuple[str, float, str]], title: str, yaxis_title: str = "", text_format: str | None = None
    ) -> go.Figure:
        """Waterfall from ``(label, value, measure)`` with measure in absolute/relative/total."""
        text_format = str(text_format or self.config.style.get("text_format") or "")
        fig = go.Figure(
            go.Waterfall(
                x=[s[0] for s in steps],
                y=[s[1] for s in steps],
                measure=[s[2] for s in steps],
                text=[format(s[1], text_format) for s in steps],
                textposition="outside",
                increasing={"marker": {"color": self.config.color("positive")}},
                decreasing={"marker": {"color": self.config.color("negative")}},
                totals={"marker": {"color": self.config.color("total")}},
                connector={"line": {"color": self.config.color("connector")}},
            )
        )
        self.config.apply_layout(fig, title, yaxis_title)
        fig.update_layout(showlegend=False, hovermode=self.config.style.get("waterfall_hovermode"))
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
        page_style = self.config.style.get("page") or {}
        colors = page_style.get(self.theme) or page_style.get("light") or {}
        bg, fg = colors.get("background", "white"), colors.get("text", "black")
        max_width, title_size = page_style.get("max_width", "100%"), page_style.get("title_size", "1.5em")
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ margin: 0; padding: 24px 16px; background: {bg}; color: {fg}; font-family: {self.config.font_family}; }}
h1 {{ font-size: {title_size}; margin: 0 auto 16px; max-width: {max_width}; }}
.chart {{ max-width: {max_width}; margin: 0 auto 24px; }}
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
