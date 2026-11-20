"""
Typer CLI for the financial modeling toolkit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NoReturn, Optional

import pandas as pd
import typer
import yaml

from financial_model import __version__, config_loader
from financial_model.cli.messages import console_style, say
from financial_model.core.expressions import format_text
from financial_model.core.model import FinancialModel, ModelResult
from financial_model.data.loader import config_from_statements, derive_drivers_from_financials
from financial_model.data.providers import available_providers, fetch_statements
from financial_model.data.synthetic import generate_company
from financial_model.exporters.csv_exporter import CSVExporter
from financial_model.exporters.json_exporter import JSONExporter
from financial_model.models.dcf import DCFModel
from financial_model.models.lbo import LBOModel
from financial_model.models.ma import MergerModel
from financial_model.models.registry import MODEL_CLASSES, build_model, normalize_model_type
from financial_model.utils.error_handler import DataFetchError
from financial_model.utils.optional import MissingDependencyError, require
from financial_model.utils.rate_limiter import CircuitBreakerOpen, RateLimitExceeded
from financial_model.utils.validation import ValidationError

app = typer.Typer(
    name="financial-model",
    help="Financial modeling toolkit: 3-statement, DCF, LBO, M&A, Budget",
    add_completion=False,
)

__all__ = ["app", "MODEL_CLASSES"]


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(say("version", version=__version__))
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True, help="Show the version and exit."
    ),
) -> None:
    """Financial modeling toolkit: 3-statement, DCF, LBO, M&A, Budget."""


def _normalize_type(model_type: str | None) -> str:
    try:
        return normalize_model_type(model_type)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _fail(message: str) -> NoReturn:
    typer.echo(say("error", message=message), err=True)
    raise typer.Exit(1)


def _calculate(model: Any, scenario: str | None) -> ModelResult:
    """``model.calculate``; an unknown scenario or invalid input is a one-line error, not a traceback."""
    try:
        return model.calculate(scenario=scenario)
    except ValueError as exc:
        _fail(str(exc))


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
@app.command()
def init(
    model_type: str = typer.Option(
        "three-statement",
        "--type",
        "-t",
        help="three-statement | dcf | lbo | budget | paas | b2b-saas | b2c-subscription | ma",
    ),
    output: Path = typer.Option(Path("model.yaml"), "--output", "-o", help="Output config file"),
    industry: Optional[str] = typer.Option(
        None, "--industry", "-i", help="Starter variant in the model definition (default: settings defaults.industry)"
    ),
    years: Optional[int] = typer.Option(None, "--years", "-y", help="Projection years (default: defaults.years)"),
    start_year: Optional[int] = typer.Option(
        None, "--start-year", "-s", help="First year (default: defaults.start_year, else this year)"
    ),
) -> None:
    """Generate a starter YAML configuration file."""
    model_type = _normalize_type(model_type)
    industry = industry or str(config_loader.default("industry"))
    years = years or int(config_loader.default("years"))
    start_year = start_year or int(config_loader.default("start_year"))
    config = _generate_config(model_type, industry, years, start_year)
    with output.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False)
    typer.echo(say("created", output=output, model_type=model_type, industry=industry))


@app.command()
def run(
    config: Path = typer.Argument(..., help="YAML/JSON config file, or - to read it from stdin"),
    scenario: Optional[str] = typer.Option(None, "--scenario", "-s", help="Scenario to run"),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output file (.xlsx/.json), directory (csv), or - for JSON on stdout"
    ),
    format: Optional[str] = typer.Option(
        None, "--format", "-f", help="excel | csv | json (inferred from --output; json alone prints to stdout)"
    ),
    model_type: Optional[str] = typer.Option(None, "--type", "-t", help="Override the config's model_type"),
) -> None:
    """Run a model from a config file (exports include DCF/LBO analysis sheets).

    From any language: `financial-model run - -o -` reads the config (JSON or YAML) on stdin
    and writes the full result as JSON on stdout; errors go to stderr with a non-zero exit.
    """
    model = _load_any(config, model_type)
    if isinstance(model, MergerModel):
        _run_merger(model, scenario, output, format)
        return
    result = _calculate(model, scenario)

    if _json_to_stdout(output, format):
        typer.echo(JSONExporter().dumps_result(result, model.all_tables(result)))
    elif output:
        fmt = _resolve_format(output, format)
        if fmt == "excel":
            model.export_excel(str(output), result)
            typer.echo(say("exported", output=output))
        elif fmt == "csv":
            _export_csv(result, output, model.all_tables(result))
            typer.echo(say("exported_csv", output=output))
        else:
            _export_json(result, output, model.all_tables(result))
            typer.echo(say("exported_json", output=output))
        _print_balance_status(result)
    else:
        _print_summary(result, model)


@app.command()
def dcf(
    config: Path = typer.Argument(..., help="YAML config file"),
    scenario: Optional[str] = typer.Option(None, "--scenario", "-s"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Export the full model to Excel"),
) -> None:
    """Run DCF valuation on a model."""
    model = _load_model(config, "dcf")
    assert isinstance(model, DCFModel)
    result = _calculate(model, scenario)
    dcf_result = model.calculate_dcf(result)

    fcf = pd.DataFrame(dcf_result["fcf_detail"], index=dcf_result["periods"])
    tables = {"fcf": fcf, "sensitivity": model.sensitivity_analysis(result)}
    _print_console(model.setting("console") or [], dcf_result, tables)

    if output:
        model.export_excel(str(output), result)
        typer.echo(say("full_model_exported", output=output))


@app.command()
def lbo(
    config: Path = typer.Argument(..., help="YAML config file"),
    scenario: Optional[str] = typer.Option(None, "--scenario", "-s"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Export the full model to Excel"),
) -> None:
    """Run LBO analysis."""
    model = _load_model(config, "lbo")
    assert isinstance(model, LBOModel)
    result = _calculate(model, scenario)
    lbo_result = model.calculate_returns(result)
    su = model.sources_uses(result)

    tables = {"sources": su["sources"], "uses": su["uses"], "debt_schedule": model.debt_schedule(result)}
    _print_console(model.setting("console") or [], lbo_result, tables)

    if output:
        model.export_excel(str(output), result)
        typer.echo(say("full_model_exported", output=output))


@app.command()
def ma(
    config: Path = typer.Argument(..., help="M&A deal YAML/JSON (acquirer, target, deal terms), or - for stdin"),
    scenario: Optional[str] = typer.Option(None, "--scenario", "-s", help="Applied to each side that defines it"),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Export tables (.xlsx/.json or a CSV directory), or - for JSON on stdout"
    ),
    format: Optional[str] = typer.Option(None, "--format", "-f", help="excel | csv | json (inferred from --output)"),
) -> None:
    """Run M&A accretion/dilution and contribution analysis."""
    model = _load_any(config, "ma")
    assert isinstance(model, MergerModel)
    _run_merger(model, scenario, output, format)


@app.command()
def chart(
    config: Path = typer.Argument(..., help="Model YAML config"),
    output: Path = typer.Option(Path("charts.html"), "--output", "-o", help="HTML dashboard file (or a directory)"),
    scenario: Optional[str] = typer.Option(None, "--scenario", "-s"),
    model_type: Optional[str] = typer.Option(None, "--type", "-t", help="Override the config's model_type"),
    theme: str = typer.Option("light", "--theme", help="light | dark"),
    cdn: bool = typer.Option(False, "--cdn", help="Load plotly.js from a CDN instead of embedding it (smaller files)"),
) -> None:
    """Render interactive charts to a self-contained HTML dashboard."""
    try:
        require("plotly", "`financial-model chart`")
    except MissingDependencyError as exc:
        _fail(str(exc))
    from financial_model.visualization import ChartBuilder

    model = _load_any(config, model_type)
    builder = ChartBuilder(theme=theme)
    try:
        charts = builder.model_charts(model, scenario=scenario)
    except ValueError as exc:
        _fail(str(exc))
    if output.suffix.lower() in (".html", ".htm"):
        title = getattr(model, "name", "Model") + (f" — {scenario}" if scenario else "")
        path = builder.export_dashboard(charts, output, title=title, offline=not cdn)
        typer.echo(say("charts_written", count=len(charts), path=path))
    else:
        output.mkdir(parents=True, exist_ok=True)
        for name, fig in charts.items():
            builder.to_html(fig, output / f"{name}.html", offline=not cdn)
        typer.echo(say("chart_files_written", count=len(charts), output=output))


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, "--host", help="Bind address (default: settings api.host, 127.0.0.1)"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="Port (default: settings api.port, 8000)"),
    settings: Optional[Path] = typer.Option(
        None, "--settings", help="Runtime settings YAML (see docs/self-hosting.md)"
    ),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (development)"),
) -> None:
    """Start the REST API server (any ASGI server also works: see docs/deployment.md)."""
    import os

    try:
        require("fastapi", "The REST API")
        uvicorn = require("uvicorn", "`financial-model serve`")
    except MissingDependencyError as exc:
        _fail(str(exc))

    from financial_model import config_loader

    if settings is not None:
        os.environ["FIN_CONFIG_PATH"] = str(settings.resolve())
        config_loader._reset_cache()
    cfg = config_loader.get_config()["api"]
    uvicorn.run(
        "financial_model.api.main:app",
        host=host or cfg["host"],
        port=port or int(cfg["port"]),
        reload=reload,
        workers=None if reload else int(cfg.get("workers", 1)),
    )


@app.command()
def fetch(
    source: str = typer.Argument(
        ..., help="Ticker (remote providers) or a path to your own statements (.xlsx, .json, or a directory)"
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        "-p",
        help="Data provider (see `financial-model providers`). Default: 'file' when SOURCE is a path, "
        "else settings data.default_provider",
    ),
    years: Optional[int] = typer.Option(
        None, "--years", "-y", help="Projection years and history to use (default: defaults.history_years)"
    ),
    output: Path = typer.Option(Path("data.yaml"), "--output", "-o"),
) -> None:
    """Load historical statements from any provider and derive a model config from them."""
    years = years or int(config_loader.default("history_years"))
    try:
        data = fetch_statements(source, years, provider)
    except (
        ValidationError,
        DataFetchError,
        RateLimitExceeded,
        CircuitBreakerOpen,
        MissingDependencyError,
        ValueError,
    ) as exc:
        _fail(str(exc))
    config = _data_to_config(source, data, years)
    with output.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False)
    typer.echo(say("fetched", name=config["name"], provider=data.get("provider", provider), output=output))


@app.command()
def providers() -> None:
    """List the data providers available to `fetch` (built-in and plugins)."""
    for name, prov in available_providers().items():
        flags = [say("provider_remote" if prov.remote else "provider_local")]
        if not prov.allow_in_api:
            flags.append(say("provider_cli_only"))
        typer.echo(say("provider", name=name, flags=", ".join(flags), description=prov.description))


@app.command()
def schema(
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout"),
) -> None:
    """Print the JSON Schema for config files (models and M&A deals), for editors and validators in any language."""
    from financial_model.schema import config_json_schema_text

    text = config_json_schema_text()
    if output:
        output.write_text(text, encoding="utf-8")
        typer.echo(say("wrote", output=output), err=True)
    else:
        typer.echo(text, nl=False)


@app.command()
def openapi(
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout"),
) -> None:
    """Print the REST API's OpenAPI spec (generate a client for any language from it)."""
    try:
        require("fastapi", "The REST API")
    except MissingDependencyError as exc:
        _fail(str(exc))
    from financial_model.api.main import create_app

    text = json.dumps(create_app({}).openapi(), indent=2) + "\n"
    if output:
        output.write_text(text, encoding="utf-8")
        typer.echo(say("wrote", output=output), err=True)
    else:
        typer.echo(text, nl=False)


@app.command()
def synthetic(
    industry: Optional[str] = typer.Option(
        None, "--industry", "-i", help="Synthetic profile (data/synthetic.yaml; default: defaults.industry)"
    ),
    years: Optional[int] = typer.Option(None, "--years", "-y", help="Years (default: the profile's)"),
    start_year: Optional[int] = typer.Option(None, "--start-year", "-s", help="First year (default: the profile's)"),
    output: Path = typer.Option(Path("synthetic.yaml"), "--output", "-o"),
) -> None:
    """Generate synthetic company data and a matching model config."""
    industry = industry or str(config_loader.default("industry"))
    try:
        data = generate_company(industry, start_year=start_year, years=years)
    except ValueError as exc:
        _fail(str(exc))
    periods = data["periods"]
    config = _synthetic_to_config(data, industry, len(periods), periods[0].year)
    with output.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False)
    typer.echo(say("synthetic_written", industry=industry, output=output))


@app.command()
def validate(
    config: Path = typer.Argument(..., help="YAML/JSON config file, or - to read it from stdin"),
) -> None:
    """Validate a config file (structure + a dry run) without exporting."""
    try:
        loaded = _load_any(config)
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - surface any parse/build error to the user
        typer.echo(say("load_failed", error=exc), err=True)
        raise typer.Exit(1) from exc

    if isinstance(loaded, MergerModel):
        for side, sub in (("acquirer", loaded.acquirer), ("target", loaded.target)):
            res = sub.calculate()
            if sub.validate() or not (res.bs_balance_result.balanced and res.cf_balance_result.balanced):
                typer.echo(say("side_invalid", side=side), err=True)
                raise typer.Exit(1)
        loaded.calculate_accretion_dilution()
        typer.echo(say("valid_ma"))
        return
    model = loaded

    errors = model.validate()
    if errors:
        typer.echo(say("validation_errors"), err=True)
        for e in errors:
            typer.echo(say("list_item", item=e), err=True)
        raise typer.Exit(1)

    result = model.calculate()
    if not result.bs_balance_result.balanced or not result.cf_balance_result.balanced:
        typer.echo(say("does_not_balance"), err=True)
        for e in [*result.bs_balance_result.errors, *result.cf_balance_result.errors]:
            typer.echo(say("list_item", item=e), err=True)
        raise typer.Exit(1)
    scenarios = ", ".join(model.assumptions.scenario_names()) or say("base_only")
    typer.echo(say("valid", model_type=model.model_type, periods=len(model.periods), scenarios=scenarios))


from financial_model.cli.platform_cmds import platform_app  # noqa: E402

app.add_typer(platform_app, name="platform")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _generate_config(model_type: str, industry: str, years: int, start_year: int) -> dict[str, Any]:
    """Starter config from the model's definition (``starter`` section of models/definitions/<type>.yaml)."""
    from financial_model.core.starters import starter_config

    return starter_config(_normalize_type(model_type), years=years, start_year=start_year, variant=industry)


def _load_any(config_path: Path, model_type: str | None = None) -> Any:
    """Load any model (including M&A) from YAML; ``model_type`` argument > config key > three-statement."""
    config_path = Path(config_path)
    if str(config_path) == STDIN:
        config = _read_stdin_config()
        config_path = Path.cwd() / "<stdin>"  # relative paths in the config resolve from the cwd
    elif not config_path.exists():
        _fail(say("config_not_found", path=config_path))
    else:
        try:
            config = FinancialModel.load_config(config_path)
        except (ValueError, yaml.YAMLError) as exc:
            _fail(say("invalid_config", file=config_path.name, detail=exc))
    declared = _normalize_type(config.get("model_type"))
    key = _normalize_type(model_type or declared)
    if (key == "ma") != (declared == "ma"):
        needs = say("needs_ma" if key == "ma" else "needs_single")
        raise typer.BadParameter(say("wrong_config_type", path=config_path, declared=declared, needs=needs))
    try:
        return build_model(config, base_dir=config_path.parent, model_type=key)
    except (ValueError, KeyError, TypeError) as exc:
        # One line on stderr + exit 1, so scripts in any language can surface it.
        detail = say("missing_key", key=exc) if isinstance(exc, KeyError) else str(exc)
        _fail(say("invalid_config", file=config_path.name, detail=detail))


def _load_model(config_path: Path, model_type: str | None = None) -> FinancialModel:
    """Load a single-entity model (three-statement / dcf / lbo / budget) from YAML."""
    model = _load_any(config_path, model_type)
    if not isinstance(model, FinancialModel):
        raise typer.BadParameter(say("command_needs", needs=say("needs_single")))
    return model


def _formatter(spec: str) -> Any:
    return lambda value: format(value, spec)


def _float_format(block: dict[str, Any]) -> Any:
    """``float_format`` spec, with an optional ``small: {below, format}`` for values under a magnitude."""
    spec, small = block.get("float_format"), block.get("small") or {}
    if not spec:
        return None
    if small:
        below, small_spec = float(small["below"]), str(small["format"])
        return lambda x: format(x, small_spec) if abs(x) < below else format(x, str(spec))
    return _formatter(str(spec))


def _print_console(blocks: list[Any], values: dict[str, Any], tables: dict[str, Any]) -> None:
    """Print a definition's ``console`` blocks: ``{text}`` lines ({name} = a result value) and
    ``{table}`` blocks (``title``, ``columns`` [{column, label, format}], ``formats``, ``float_format``,
    ``small``, ``transpose``, ``index``). ``when: <name>`` prints a block only when that value is set."""
    for block in blocks or []:
        if block.get("when") and not values.get(str(block["when"])):
            continue
        if "text" in block:
            typer.echo(format_text(block["text"], values))
            continue
        if block.get("title") is not None:
            typer.echo(format_text(block["title"], values))
        frame = tables[str(block["table"])]
        formats = dict(block.get("formats") or {})
        if block.get("columns"):
            frame = frame[[c["column"] for c in block["columns"]]].copy()
            frame.columns = [c["label"] for c in block["columns"]]
            formats.update({c["label"]: c["format"] for c in block["columns"] if c.get("format")})
        if block.get("transpose"):
            frame = frame.T
        kwargs: dict[str, Any] = {"index": bool(block.get("index", True))}
        if formats:
            kwargs["formatters"] = {col: _formatter(f) for col, f in formats.items()}
        if _float_format(block) is not None:
            kwargs["float_format"] = _float_format(block)
        typer.echo(frame.to_string(**kwargs))


def _run_merger(model: MergerModel, scenario: str | None, output: Path | None, fmt: str | None) -> None:
    tables = model.analysis_tables(scenario)
    if _json_to_stdout(output, fmt):
        typer.echo(JSONExporter().dumps_tables(tables))
        return
    # What is printed, and how, is the `console` section of the ma definition.
    names = model.definition.get("tables") or {}
    values = {**tables[names["summary"]], "name": model.name, "scenario": scenario or ""}
    _print_console(model.definition.get("console") or [], values, {k: tables[v] for k, v in names.items()})
    if output:
        resolved = _resolve_format(output, fmt)
        if resolved == "excel":
            from financial_model.exporters.excel_exporter import ExcelExporter

            ExcelExporter().export_tables(tables, output)
        elif resolved == "csv":
            CSVExporter().export_tables(tables, output)
        else:
            JSONExporter().export_tables(tables, output)
        typer.echo(say("exported_tables", output=output))


STDIN = STDOUT = "-"


def _read_stdin_config() -> dict[str, Any]:
    """A config piped on stdin: JSON or YAML (YAML is a superset of JSON)."""
    text = sys.stdin.read()
    if not text.strip():
        _fail(say("stdin_empty"))
    try:
        config = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        _fail(say("stdin_invalid", error=exc))
    if not isinstance(config, dict):
        _fail(say("stdin_not_mapping"))
    return config


def _json_to_stdout(output: Path | None, fmt: str | None) -> bool:
    """`-o -`, or `-f json` without `-o`, writes JSON to stdout (nothing else is printed)."""
    if output is not None and str(output) == STDOUT:
        if fmt and fmt.lower() != "json":
            raise typer.BadParameter(say("stdout_json_only"))
        return True
    return output is None and bool(fmt) and str(fmt).lower() == "json"


def _resolve_format(output: Path, fmt: str | None) -> str:
    if fmt:
        fmt = fmt.lower()
        if fmt in ("xlsx", "xls"):
            fmt = "excel"
        if fmt not in ("excel", "csv", "json"):
            raise typer.BadParameter(say("bad_format"))
        return fmt
    suffix = output.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        return "excel"
    if suffix == ".json":
        return "json"
    if suffix == ".csv" or suffix == "":
        return "csv"
    return "excel"


def _data_to_config(source: str, data: dict[str, Any], years: int) -> dict[str, Any]:
    """Convert fetched statements (any provider) into a three-statement config with derived drivers."""
    provider = data.get("provider", "yfinance")
    if provider == "file":
        label, metadata = Path(source).stem, {"source": "file", "path": str(source)}
    else:
        label, metadata = source.strip().upper(), {"ticker": source.strip().upper(), "source": provider}
    template = _generate_config("three-statement", "saas", years, pd.Timestamp.today().year)
    return config_from_statements(
        data, f"{label} Three Statement Model", years, metadata=metadata, base_config=template
    )


def _synthetic_to_config(data: dict[str, Any], industry: str, years: int, start_year: int) -> dict[str, Any]:
    """Convert synthetic statements into a config whose drivers reproduce them."""
    config = _generate_config("three-statement", industry, years, start_year)
    derived = derive_drivers_from_financials(
        data.get("income_statement"),
        data.get("balance_sheet"),
        data.get("cash_flow"),
        projection_years=years,
    )
    config["assumptions"]["drivers"].update(derived)
    config["assumptions"]["metadata"] = {"source": "synthetic", "industry": industry}
    return config


def _print_balance_status(result: ModelResult) -> None:
    bs, cf = result.bs_balance_result, result.cf_balance_result
    typer.echo(say("bs_balanced", balanced=bs.balanced, max_diff=bs.max_diff))
    typer.echo(say("cf_balanced", balanced=cf.balanced, max_diff=cf.max_diff))
    for err in [*bs.errors, *cf.errors][: int(console_style().get("max_balance_errors", 0))]:
        typer.echo(say("balance_error", error=err))


def _format_statement(df: pd.DataFrame, result: ModelResult, stmt: Any = None) -> str:
    out = result.labelled(df) if hasattr(result, "labelled") else df
    formatters: dict[str, Any] = {}
    if stmt is not None:
        pct_lines = {item.name for item in stmt.ordered_items() if item.unit == "%"}
    else:
        pct_lines = set()

    style = console_style()
    label_w, col_w = int(style["label_width"]), int(style["column_width"])
    pct_spec, value_spec, missing = str(style["percent_format"]), str(style["value_format"]), str(style["missing"])

    def fmt_row(name: Any) -> Any:
        spec = pct_spec if name in pct_lines else value_spec
        return lambda x: format(x, spec)

    rows = []
    header = " " * label_w + "".join(f"{str(c):>{col_w}s}" for c in out.columns)
    rows.append(header)
    for name, row in out.iterrows():
        f = fmt_row(name)
        cells = "".join(f"{(f(v) if pd.notna(v) else missing):>{col_w}s}" for v in row)
        rows.append(f"{str(name)[:label_w]:{label_w}s}{cells}")
    del formatters
    return "\n".join(rows)


def _print_summary(result: ModelResult, model: FinancialModel | None = None) -> None:
    """Print model summary to console."""
    name = result.metadata.get("model_name") or say("model_fallback_name")
    typer.echo(say("model_title", name=name))
    labels = ", ".join(p.label for p in result.periods)
    typer.echo(say("periods", count=len(result.periods), labels=labels))
    if result.metadata.get("scenario"):
        typer.echo(say("scenario", scenario=result.metadata["scenario"]))
    _print_balance_status(result)

    titles = console_style().get("statement_titles") or {}
    for key, df, stmt in (
        ("income_statement", result.income_statement, model.income_statement if model else None),
        ("balance_sheet", result.balance_sheet, model.balance_sheet if model else None),
        ("cash_flow", result.cash_flow, model.cash_flow if model else None),
    ):
        typer.echo(say("statement_title", title=titles.get(key, key)))
        typer.echo(_format_statement(df, result, stmt))


def _export_csv(result: ModelResult, output: Path, extras: dict[str, Any] | None = None) -> None:
    CSVExporter().export_result(result, output, extras=extras)


def _export_json(result: Any, output: Path, extras: dict[str, Any] | None = None) -> None:
    JSONExporter().export_result(result, output, extras=extras)


if __name__ == "__main__":
    app()
