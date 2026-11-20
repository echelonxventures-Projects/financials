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
        typer.echo(f"financial-model {__version__}")
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
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(1)


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
    typer.echo(f"Created {output} with {model_type} template for {industry}")


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
    result = model.calculate(scenario=scenario)

    if _json_to_stdout(output, format):
        typer.echo(JSONExporter().dumps_result(result, model.all_tables(result)))
    elif output:
        fmt = _resolve_format(output, format)
        if fmt == "excel":
            model.export_excel(str(output), result)
            typer.echo(f"Exported to {output}")
        elif fmt == "csv":
            _export_csv(result, output, model.all_tables(result))
            typer.echo(f"Exported CSVs to {output}")
        else:
            _export_json(result, output, model.all_tables(result))
            typer.echo(f"Exported JSON to {output}")
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
    result = model.calculate(scenario=scenario)
    dcf_result = model.calculate_dcf(result)

    typer.echo(f"WACC: {dcf_result['wacc']:.2%}")
    typer.echo(f"Cost of Equity: {dcf_result['cost_of_equity']:.2%}")
    typer.echo(f"Terminal EBITDA: ${dcf_result['terminal_ebitda']:,.0f}")
    typer.echo(f"EV (Gordon): ${dcf_result['ev_gordon']:,.0f}")
    typer.echo(f"EV (Multiple): ${dcf_result['ev_multiple']:,.0f}")
    typer.echo(f"Net Debt: ${dcf_result['net_debt']:,.0f}")
    typer.echo(f"Equity Value (Gordon): ${dcf_result['equity_gordon']:,.0f}")
    typer.echo(f"Equity Value (Multiple): ${dcf_result['equity_multiple']:,.0f}")
    typer.echo(f"Price/Share (Gordon): ${dcf_result['price_gordon']:,.2f}")
    typer.echo(f"Price/Share (Multiple): ${dcf_result['price_multiple']:,.2f}")

    typer.echo("\nFree cash flow:")
    fcf = pd.DataFrame(dcf_result["fcf_detail"], index=dcf_result["periods"]).T
    typer.echo(fcf.to_string(float_format=lambda x: f"{x:,.0f}"))

    sens = model.sensitivity_analysis(result)
    typer.echo("\nSensitivity (Equity Value, Gordon growth):")
    typer.echo(sens.to_string(float_format=lambda x: f"{x:,.0f}"))

    if output:
        model.export_excel(str(output), result)
        typer.echo(f"\nFull model exported to {output}")


@app.command()
def lbo(
    config: Path = typer.Argument(..., help="YAML config file"),
    scenario: Optional[str] = typer.Option(None, "--scenario", "-s"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Export the full model to Excel"),
) -> None:
    """Run LBO analysis."""
    model = _load_model(config, "lbo")
    assert isinstance(model, LBOModel)
    result = model.calculate(scenario=scenario)
    lbo_result = model.calculate_returns(result)
    su = model.sources_uses(result)

    typer.echo("Sources & Uses:")
    typer.echo(su["sources"].to_string(index=False, float_format=lambda x: f"{x:,.2f}" if abs(x) < 1 else f"{x:,.0f}"))
    typer.echo(su["uses"].to_string(index=False, float_format=lambda x: f"{x:,.2f}" if abs(x) < 1 else f"{x:,.0f}"))

    typer.echo("\nDebt schedule:")
    typer.echo(model.debt_schedule(result).T.to_string(float_format=lambda x: f"{x:,.0f}"))

    typer.echo("")
    typer.echo(f"Entry EBITDA: ${lbo_result['entry_ebitda']:,.0f}")
    typer.echo(f"Entry EV: ${lbo_result['entry_ev']:,.0f}")
    typer.echo(f"Equity Invested: ${lbo_result['equity_invested']:,.0f}")
    typer.echo(f"Exit ({lbo_result['exit_period']}) EBITDA: ${lbo_result['exit_ebitda']:,.0f}")
    typer.echo(f"Exit EV: ${lbo_result['exit_ev']:,.0f}")
    typer.echo(f"Net Debt at Exit: ${lbo_result['net_debt_at_exit']:,.0f}")
    typer.echo(f"Equity Value at Exit: ${lbo_result['equity_value_at_exit']:,.0f}")
    typer.echo(f"MOIC: {lbo_result['moic']:.2f}x")
    typer.echo(f"IRR: {lbo_result['irr']:.2%}")

    if output:
        model.export_excel(str(output), result)
        typer.echo(f"\nFull model exported to {output}")


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
    charts = builder.model_charts(model, scenario=scenario)
    if output.suffix.lower() in (".html", ".htm"):
        title = getattr(model, "name", "Model") + (f" — {scenario}" if scenario else "")
        path = builder.export_dashboard(charts, output, title=title, offline=not cdn)
        typer.echo(f"Wrote {len(charts)} charts to {path}")
    else:
        output.mkdir(parents=True, exist_ok=True)
        for name, fig in charts.items():
            builder.to_html(fig, output / f"{name}.html", offline=not cdn)
        typer.echo(f"Wrote {len(charts)} chart files to {output}")


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
    typer.echo(f"Saved {config['name']} ({data.get('provider', provider)}) to {output}")


@app.command()
def providers() -> None:
    """List the data providers available to `fetch` (built-in and plugins)."""
    for name, prov in available_providers().items():
        flags = ["remote" if prov.remote else "local"]
        if not prov.allow_in_api:
            flags.append("CLI only")
        typer.echo(f"{name:12s} [{', '.join(flags)}] {prov.description}")


@app.command()
def schema(
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout"),
) -> None:
    """Print the JSON Schema for config files (models and M&A deals), for editors and validators in any language."""
    from financial_model.schema import config_json_schema_text

    text = config_json_schema_text()
    if output:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"Wrote {output}", err=True)
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
        typer.echo(f"Wrote {output}", err=True)
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
    typer.echo(f"Generated synthetic {industry} data to {output}")


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
        typer.echo(f"Failed to load config: {exc}", err=True)
        raise typer.Exit(1) from exc

    if isinstance(loaded, MergerModel):
        for side, sub in (("acquirer", loaded.acquirer), ("target", loaded.target)):
            res = sub.calculate()
            if sub.validate() or not (res.bs_balance_result.balanced and res.cf_balance_result.balanced):
                typer.echo(f"{side} model does not validate/balance", err=True)
                raise typer.Exit(1)
        loaded.calculate_accretion_dilution()
        typer.echo("Config is valid: OK (ma, acquirer and target balance)")
        return
    model = loaded

    errors = model.validate()
    if errors:
        typer.echo("Validation errors:", err=True)
        for e in errors:
            typer.echo(f"  - {e}", err=True)
        raise typer.Exit(1)

    result = model.calculate()
    if not result.bs_balance_result.balanced or not result.cf_balance_result.balanced:
        typer.echo("Model runs but does not balance:", err=True)
        for e in [*result.bs_balance_result.errors, *result.cf_balance_result.errors]:
            typer.echo(f"  - {e}", err=True)
        raise typer.Exit(1)
    typer.echo(
        f"Config is valid: OK ({model.model_type}, {len(model.periods)} periods, "
        f"scenarios: {', '.join(model.assumptions.scenario_names()) or 'base only'})"
    )


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
        _fail(f"config file not found: {config_path}")
    else:
        try:
            config = FinancialModel.load_config(config_path)
        except (ValueError, yaml.YAMLError) as exc:
            _fail(f"invalid config {config_path.name}: {exc}")
    declared = _normalize_type(config.get("model_type"))
    key = _normalize_type(model_type or declared)
    if (key == "ma") != (declared == "ma"):
        raise typer.BadParameter(
            f"{config_path} is a '{declared}' config; this command needs "
            + ("an M&A deal config" if key == "ma" else "a three-statement, dcf, lbo, budget or business-model config")
        )
    try:
        return build_model(config, base_dir=config_path.parent, model_type=key)
    except (ValueError, KeyError, TypeError) as exc:
        # One line on stderr + exit 1, so scripts in any language can surface it.
        detail = f"missing key {exc}" if isinstance(exc, KeyError) else str(exc)
        _fail(f"invalid config {config_path.name}: {detail}")


def _load_model(config_path: Path, model_type: str | None = None) -> FinancialModel:
    """Load a single-entity model (three-statement / dcf / lbo / budget) from YAML."""
    model = _load_any(config_path, model_type)
    if not isinstance(model, FinancialModel):
        raise typer.BadParameter("this command needs a three-statement, dcf, lbo, budget or business-model config")
    return model


def _run_merger(model: MergerModel, scenario: str | None, output: Path | None, fmt: str | None) -> None:
    tables = model.analysis_tables(scenario)
    if _json_to_stdout(output, fmt):
        typer.echo(JSONExporter().dumps_tables(tables))
        return
    summary = tables["Deal Summary"]
    typer.echo(f"\n=== {model.name} ===")
    if scenario:
        typer.echo(f"Scenario: {scenario}")
    typer.echo(f"Equity purchase price: ${summary['equity_value']:,.0f}")
    typer.echo(f"  Cash (debt-funded):  ${summary['cash_paid']:,.0f}")
    typer.echo(
        f"  Stock:               ${summary['stock_value']:,.0f} "
        f"({summary['new_shares_issued']:,.0f} new shares at ${summary['acquirer_share_price']:,.2f})"
    )
    typer.echo(f"Acquisition interest:  ${summary['pf_interest']:,.0f} / yr")
    typer.echo("\nAccretion / (dilution):")
    ad = tables["Accretion Dilution"][["acquirer_eps", "pf_eps", "pf_synergies", "accretion_dilution_pct"]].copy()
    ad.columns = ["Standalone EPS", "Pro Forma EPS", "Synergies", "Accretion %"]
    typer.echo(
        ad.to_string(
            formatters={
                "Standalone EPS": "{:,.3f}".format,
                "Pro Forma EPS": "{:,.3f}".format,
                "Synergies": "{:,.0f}".format,
                "Accretion %": "{:+.1%}".format,
            }
        )
    )
    typer.echo("\nContribution (year 1):")
    typer.echo(
        tables["Contribution"].to_string(
            formatters={
                "Acquirer": "{:,.0f}".format,
                "Target": "{:,.0f}".format,
                "Combined": "{:,.0f}".format,
                "Acquirer %": "{:.1%}".format,
            }
        )
    )
    if output:
        resolved = _resolve_format(output, fmt)
        if resolved == "excel":
            from financial_model.exporters.excel_exporter import ExcelExporter

            ExcelExporter().export_tables(tables, output)
        elif resolved == "csv":
            CSVExporter().export_tables(tables, output)
        else:
            JSONExporter().export_tables(tables, output)
        typer.echo(f"\nExported to {output}")


STDIN = STDOUT = "-"


def _read_stdin_config() -> dict[str, Any]:
    """A config piped on stdin: JSON or YAML (YAML is a superset of JSON)."""
    text = sys.stdin.read()
    if not text.strip():
        _fail("no config on stdin")
    try:
        config = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        _fail(f"stdin is not valid JSON or YAML: {exc}")
    if not isinstance(config, dict):
        _fail("stdin: expected a mapping at the top level")
    return config


def _json_to_stdout(output: Path | None, fmt: str | None) -> bool:
    """`-o -`, or `-f json` without `-o`, writes JSON to stdout (nothing else is printed)."""
    if output is not None and str(output) == STDOUT:
        if fmt and fmt.lower() != "json":
            raise typer.BadParameter("only json can be written to stdout (-o -)")
        return True
    return output is None and bool(fmt) and str(fmt).lower() == "json"


def _resolve_format(output: Path, fmt: str | None) -> str:
    if fmt:
        fmt = fmt.lower()
        if fmt in ("xlsx", "xls"):
            fmt = "excel"
        if fmt not in ("excel", "csv", "json"):
            raise typer.BadParameter("format must be excel, csv or json")
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
    typer.echo(f"BS Balanced: {result.bs_balance_result.balanced} (max diff: {result.bs_balance_result.max_diff:,.2f})")
    typer.echo(f"CF Balanced: {result.cf_balance_result.balanced} (max diff: {result.cf_balance_result.max_diff:,.2f})")
    for err in [*result.bs_balance_result.errors, *result.cf_balance_result.errors][:5]:
        typer.echo(f"  ! {err}")


def _format_statement(df: pd.DataFrame, result: ModelResult, stmt: Any = None) -> str:
    out = result.labelled(df) if hasattr(result, "labelled") else df
    formatters: dict[str, Any] = {}
    if stmt is not None:
        pct_lines = {item.name for item in stmt.ordered_items() if item.unit == "%"}
    else:
        pct_lines = set()

    def fmt_row(name: Any) -> Any:
        if name in pct_lines:
            return lambda x: f"{x:.1%}"
        return lambda x: f"{x:,.0f}"

    rows = []
    header = f"{'':40s}" + "".join(f"{str(c):>16s}" for c in out.columns)
    rows.append(header)
    for name, row in out.iterrows():
        f = fmt_row(name)
        cells = "".join(f"{(f(v) if pd.notna(v) else '-'):>16s}" for v in row)
        rows.append(f"{str(name)[:40]:40s}{cells}")
    del formatters
    return "\n".join(rows)


def _print_summary(result: ModelResult, model: FinancialModel | None = None) -> None:
    """Print model summary to console."""
    typer.echo(f"\n=== {result.metadata.get('model_name', 'Model')} ===")
    typer.echo(f"Periods: {len(result.periods)} ({', '.join(p.label for p in result.periods)})")
    if result.metadata.get("scenario"):
        typer.echo(f"Scenario: {result.metadata['scenario']}")
    _print_balance_status(result)

    for title, df, stmt in (
        ("Income Statement", result.income_statement, model.income_statement if model else None),
        ("Balance Sheet", result.balance_sheet, model.balance_sheet if model else None),
        ("Cash Flow", result.cash_flow, model.cash_flow if model else None),
    ):
        typer.echo(f"\n--- {title} ---")
        typer.echo(_format_statement(df, result, stmt))


def _export_csv(result: ModelResult, output: Path, extras: dict[str, Any] | None = None) -> None:
    CSVExporter().export_result(result, output, extras=extras)


def _export_json(result: Any, output: Path, extras: dict[str, Any] | None = None) -> None:
    JSONExporter().export_result(result, output, extras=extras)


if __name__ == "__main__":
    app()
