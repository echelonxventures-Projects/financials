"""
Typer CLI for financial modeling toolkit.
"""

import typer
from pathlib import Path
from typing import Optional
import yaml

from financial_model.models.three_statement import ThreeStatementModel
from financial_model.models.dcf import DCFModel
from financial_model.models.lbo import LBOModel
from financial_model.models.budget import BudgetModel
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.core.assumptions import Assumptions, Driver
from financial_model.data.loader import load_public_company_data
from financial_model.data.synthetic import generate_saas_company

app = typer.Typer(
    name="financial-model",
    help="Financial modeling toolkit: 3-statement, DCF, LBO, M&A, Budget",
    add_completion=False,
)


@app.command()
def init(
    model_type: str = typer.Option("three-statement", "--type", "-t", help="Model type"),
    output: Path = typer.Option(Path("model.yaml"), "--output", "-o", help="Output config file"),
    industry: str = typer.Option("saas", "--industry", "-i", help="Industry template"),
    years: int = typer.Option(5, "--years", "-y", help="Projection years"),
    start_year: int = typer.Option(2024, "--start-year", "-s", help="Start year"),
) -> None:
    """Generate a starter YAML configuration file."""
    config = _generate_config(model_type, industry, years, start_year)
    with open(output, "w") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False)
    typer.echo(f"Created {output} with {model_type} template for {industry}")


@app.command()
def run(
    config: Path = typer.Argument(..., help="YAML config file"),
    scenario: Optional[str] = typer.Option(None, "--scenario", "-s", help="Scenario to run"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output Excel file"),
    format: str = typer.Option("excel", "--format", "-f", help="Output format: excel, csv, json"),
) -> None:
    """Run a model from config file."""
    model = _load_model(config)
    result = model.calculate(scenario=scenario)

    if output:
        if format == "excel":
            model.export_excel(str(output), result)
            typer.echo(f"Exported to {output}")
        elif format == "csv":
            _export_csv(result, output)
            typer.echo(f"Exported CSVs to {output}")
        elif format == "json":
            _export_json(result, output)
            typer.echo(f"Exported JSON to {output}")
    else:
        _print_summary(result)


@app.command()
def dcf(
    config: Path = typer.Argument(..., help="YAML config file"),
    scenario: Optional[str] = typer.Option(None, "--scenario", "-s"),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
) -> None:
    """Run DCF valuation on a model."""
    model = DCFModel.from_yaml(str(config))
    result = model.calculate(scenario=scenario)
    dcf_result = model.calculate_dcf(result)

    typer.echo(f"WACC: {dcf_result['wacc']:.2%}")
    typer.echo(f"Cost of Equity: {dcf_result['cost_of_equity']:.2%}")
    typer.echo(f"EV (Gordon): ${dcf_result['ev_gordon']:,.0f}")
    typer.echo(f"EV (Multiple): ${dcf_result['ev_multiple']:,.0f}")
    typer.echo(f"Equity Value (Gordon): ${dcf_result['equity_gordon']:,.0f}")
    typer.echo(f"Price/Share (Gordon): ${dcf_result['price_gordon']:,.2f}")

    # Sensitivity
    sens = model.sensitivity_analysis(result)
    typer.echo("\nSensitivity (Equity Value):")
    typer.echo(sens.to_string())

    if output:
        model.export_excel(str(output), result)
        typer.echo(f"\nFull model exported to {output}")


@app.command()
def lbo(
    config: Path = typer.Argument(..., help="YAML config file"),
    scenario: Optional[str] = typer.Option(None, "--scenario", "-s"),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
) -> None:
    """Run LBO analysis."""
    model = LBOModel.from_yaml(str(config))
    result = model.calculate(scenario=scenario)
    lbo_result = model.calculate_returns(result)

    typer.echo(f"Entry EV: ${lbo_result['entry_ev']:,.0f}")
    typer.echo(f"Equity Invested: ${lbo_result['equity_invested']:,.0f}")
    typer.echo(f"Exit EBITDA: ${lbo_result['exit_ebitda']:,.0f}")
    typer.echo(f"Exit EV: ${lbo_result['exit_ev']:,.0f}")
    typer.echo(f"Net Debt at Exit: ${lbo_result['net_debt_at_exit']:,.0f}")
    typer.echo(f"Equity Value at Exit: ${lbo_result['equity_value_at_exit']:,.0f}")
    typer.echo(f"MOIC: {lbo_result['moic']:.2f}x")
    typer.echo(f"IRR: {lbo_result['irr']:.2%}")

    if output:
        model.export_excel(str(output), result)


@app.command()
def fetch(
    ticker: str = typer.Argument(..., help="Stock ticker (e.g., SNOW)"),
    years: int = typer.Option(5, "--years", "-y"),
    output: Path = typer.Option(Path("data.yaml"), "--output", "-o"),
) -> None:
    """Fetch public company data and save as YAML config."""
    data = load_public_company_data(ticker, years)
    config = _data_to_config(ticker, data, years)
    with open(output, "w") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False)
    typer.echo(f"Saved {ticker} data to {output}")


@app.command()
def synthetic(
    industry: str = typer.Option("saas", "--industry", "-i"),
    years: int = typer.Option(5, "--years", "-y"),
    start_year: int = typer.Option(2020, "--start-year", "-s"),
    output: Path = typer.Option(Path("synthetic.yaml"), "--output", "-o"),
) -> None:
    """Generate synthetic company data."""
    if industry == "saas":
        data = generate_saas_company(start_year=start_year, years=years)
    else:
        typer.echo(f"Industry {industry} not yet implemented", err=True)
        raise typer.Exit(1)

    config = _synthetic_to_config(data, industry, years, start_year)
    with open(output, "w") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False)
    typer.echo(f"Generated synthetic {industry} data to {output}")


@app.command()
def validate(
    config: Path = typer.Argument(..., help="YAML config file"),
) -> None:
    """Validate a config file without running."""
    model = _load_model(config)
    errors = []
    for stmt in [model.income_statement, model.balance_sheet, model.cash_flow]:
        errors.extend(stmt.validate())
    if errors:
        typer.echo("Validation errors:", err=True)
        for e in errors:
            typer.echo(f"  - {e}", err=True)
        raise typer.Exit(1)
    else:
        typer.echo("Config is valid ✓")


def _generate_config(model_type: str, industry: str, years: int, start_year: int) -> dict:
    """Generate starter YAML config."""
    periods = {
        "start_year": start_year,
        "years": years,
        "type": "annual",
    }

    # Base assumptions by industry
    if industry == "saas":
        drivers = {
            "revenue": {"value": 10_000_000, "description": "Starting revenue", "unit": "$"},
            "revenue_growth": {"value": 0.30, "description": "YoY growth", "unit": "%"},
            "cogs_pct": {"value": 0.22, "description": "COGS % of revenue", "unit": "%"},
            "rd_pct": {"value": 0.20, "description": "R&D % of revenue", "unit": "%"},
            "sm_pct": {"value": 0.40, "description": "S&M % of revenue", "unit": "%"},
            "gna_pct": {"value": 0.12, "description": "G&A % of revenue", "unit": "%"},
            "tax_rate": {"value": 0.21, "description": "Tax rate", "unit": "%"},
            "ar_days": {"value": 45, "description": "AR days", "unit": "days"},
            "ap_days": {"value": 60, "description": "AP days", "unit": "days"},
            "deferred_rev_pct": {"value": 0.30, "description": "Deferred rev % of revenue", "unit": "%"},
            "capex": {"value": -500_000, "description": "Annual CapEx", "unit": "$"},
            "depreciation_rate": {"value": 0.20, "description": "Depreciation rate", "unit": "%"},
        }
    else:
        drivers = {
            "revenue": {"value": 50_000_000, "description": "Starting revenue", "unit": "$"},
            "revenue_growth": {"value": 0.10, "description": "YoY growth", "unit": "%"},
            "cogs_pct": {"value": 0.60, "description": "COGS % of revenue", "unit": "%"},
            "rd_pct": {"value": 0.05, "description": "R&D % of revenue", "unit": "%"},
            "sm_pct": {"value": 0.15, "description": "S&M % of revenue", "unit": "%"},
            "gna_pct": {"value": 0.08, "description": "G&A % of revenue", "unit": "%"},
            "tax_rate": {"value": 0.21, "description": "Tax rate", "unit": "%"},
        }

    return {
        "name": f"{model_type.replace('-', ' ').title()} Model",
        "periods": periods,
        "assumptions": {
            "drivers": drivers,
            "scenarios": {
                "base": {},
                "upside": {"revenue_growth": 0.40, "cogs_pct": 0.20},
                "downside": {"revenue_growth": 0.15, "cogs_pct": 0.25},
            },
        },
        "statements": [],
    }


def _load_model(config_path: Path) -> ThreeStatementModel:
    """Load model from YAML config."""
    return ThreeStatementModel.from_yaml(str(config_path))


def _data_to_config(ticker: str, data: dict, years: int) -> dict:
    """Convert fetched data to config."""
    # Simplified - would need more mapping
    return _generate_config("three-statement", "saas", years, 2024)


def _synthetic_to_config(data: dict, industry: str, years: int, start_year: int) -> dict:
    """Convert synthetic data to config."""
    return _generate_config("three-statement", industry, years, start_year)


def _print_summary(result) -> None:
    """Print model summary to console."""
    typer.echo(f"\n=== {result.metadata.get('model_name', 'Model')} ===")
    typer.echo(f"Periods: {len(result.periods)} years")
    typer.echo(f"BS Balanced: {result.bs_balance_result.balanced} (max diff: {result.bs_balance_result.max_diff:,.2f})")
    typer.echo(f"CF Balanced: {result.cf_balance_result.balanced} (max diff: {result.cf_balance_result.max_diff:,.2f})")

    typer.echo("\n--- Income Statement ---")
    typer.echo(result.income_statement.to_string())

    typer.echo("\n--- Balance Sheet ---")
    typer.echo(result.balance_sheet.to_string())

    typer.echo("\n--- Cash Flow ---")
    typer.echo(result.cash_flow.to_string())


def _export_csv(result, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    result.income_statement.to_csv(output / "income_statement.csv")
    result.balance_sheet.to_csv(output / "balance_sheet.csv")
    result.cash_flow.to_csv(output / "cash_flow.csv")


def _export_json(result, output: Path) -> None:
    import json
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump({
            "income_statement": result.income_statement.to_dict(),
            "balance_sheet": result.balance_sheet.to_dict(),
            "cash_flow": result.cash_flow.to_dict(),
            "periods": [p.label for p in result.periods],
        }, f, indent=2, default=str)


if __name__ == "__main__":
    app()