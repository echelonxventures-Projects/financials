"""
FastAPI REST API for financial modeling.
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from pathlib import Path
import tempfile
import json

from financial_model.models.three_statement import ThreeStatementModel
from financial_model.models.dcf import DCFModel
from financial_model.models.lbo import LBOModel
from financial_model.core.period import Period, PeriodIndex, PeriodType
from financial_model.core.assumptions import Assumptions, Driver
from financial_model.data.loader import load_public_company_data
from financial_model.data.synthetic import generate_saas_company

app = FastAPI(
    title="Financial Model API",
    description="REST API for three-statement, DCF, LBO, and budget models",
    version="0.1.0",
)


class PeriodConfig(BaseModel):
    start_year: int = 2024
    years: int = 5
    type: str = "annual"


class DriverConfig(BaseModel):
    value: float | List[float]
    description: str = ""
    unit: str = ""


class AssumptionsConfig(BaseModel):
    drivers: Dict[str, DriverConfig] = {}
    scenarios: Dict[str, Dict[str, float | List[float]]] = {}


class LineItemConfig(BaseModel):
    name: str
    line_type: str
    statement_type: str
    parent: Optional[str] = None
    children: List[str] = []
    calculation_rule: Optional[str] = None
    calculation_params: Dict[str, Any] = {}
    driver: Optional[DriverConfig] = None
    format: str = "{:,.0f}"
    unit: str = "$"
    order: int = 0
    is_total: bool = False
    description: str = ""


class StatementConfig(BaseModel):
    type: str
    line_items: List[LineItemConfig] = []


class ModelConfig(BaseModel):
    name: str = "Financial Model"
    periods: PeriodConfig = PeriodConfig()
    assumptions: AssumptionsConfig = AssumptionsConfig()
    statements: List[StatementConfig] = []


class CalculateRequest(BaseModel):
    config: ModelConfig
    scenario: Optional[str] = None


class DCFRequest(BaseModel):
    config: ModelConfig
    scenario: Optional[str] = None
    wacc_range: Optional[List[float]] = None
    growth_range: Optional[List[float]] = None


class LBORequest(BaseModel):
    config: ModelConfig
    scenario: Optional[str] = None


class FetchRequest(BaseModel):
    ticker: str
    years: int = 5


class SyntheticRequest(BaseModel):
    industry: str = "saas"
    years: int = 5
    start_year: int = 2020
    base_arr: float = 10_000_000
    arr_growth: float = 0.35


@app.post("/calculate")
async def calculate_model(request: CalculateRequest) -> Dict[str, Any]:
    """Run three-statement model calculation."""
    try:
        model = _config_to_model(request.config)
        result = model.calculate(scenario=request.scenario)
        return _result_to_dict(result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/dcf")
async def calculate_dcf(request: DCFRequest) -> Dict[str, Any]:
    """Run DCF valuation."""
    try:
        model = _config_to_dcf_model(request.config)
        result = model.calculate(scenario=request.scenario)
        dcf_result = model.calculate_dcf(result)

        response = {
            "model_result": _result_to_dict(result),
            "dcf": dcf_result,
        }

        if request.wacc_range and request.growth_range:
            sens = model.sensitivity_analysis(
                result,
                wacc_range=(request.wacc_range[0], request.wacc_range[1]),
                growth_range=(request.growth_range[0], request.growth_range[1]),
            )
            response["sensitivity"] = sens.to_dict()

        return response
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/lbo")
async def calculate_lbo(request: LBORequest) -> Dict[str, Any]:
    """Run LBO analysis."""
    try:
        model = _config_to_lbo_model(request.config)
        result = model.calculate(scenario=request.scenario)
        lbo_result = model.calculate_returns(result)
        return {
            "model_result": _result_to_dict(result),
            "lbo": lbo_result,
            "sources_uses": model.sources_uses().to_dict() if hasattr(model.sources_uses(), 'to_dict') else {},
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/fetch")
async def fetch_company(request: FetchRequest) -> Dict[str, Any]:
    """Fetch public company financial data."""
    try:
        data = load_public_company_data(request.ticker, request.years)
        return {
            "ticker": request.ticker,
            "income_statement": data["income_statement"].to_dict(),
            "balance_sheet": data["balance_sheet"].to_dict(),
            "cash_flow": data["cash_flow"].to_dict(),
            "key_stats": data["key_stats"],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/synthetic")
async def generate_synthetic(request: SyntheticRequest) -> Dict[str, Any]:
    """Generate synthetic company data."""
    try:
        if request.industry == "saas":
            data = generate_saas_company(
                start_year=request.start_year,
                years=request.years,
                base_arr=request.base_arr,
                arr_growth=request.arr_growth,
            )
        else:
            raise HTTPException(status_code=400, detail=f"Industry {request.industry} not supported")

        return {
            "industry": request.industry,
            "income_statement": data["income_statement"].to_dict(),
            "balance_sheet": data["balance_sheet"].to_dict(),
            "cash_flow": data["cash_flow"].to_dict(),
            "periods": [p.label for p in data["periods"]],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/export/excel")
async def export_excel(request: CalculateRequest) -> FileResponse:
    """Export model to Excel file."""
    try:
        model = _config_to_model(request.config)
        result = model.calculate(scenario=request.scenario)

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            model.export_excel(tmp.name, result)
            return FileResponse(
                tmp.name,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=f"{request.config.name.replace(' ', '_')}.xlsx",
            )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "healthy"}


def _config_to_model(config: ModelConfig) -> ThreeStatementModel:
    model = ThreeStatementModel(name=config.name)

    # Periods
    periods = PeriodIndex()
    for i in range(config.periods.years):
        periods.add(Period(year=config.periods.start_year + i, period_type=PeriodType(config.periods.type)))
    model.set_periods(periods)

    # Assumptions
    drivers = {}
    for name, dc in config.assumptions.drivers.items():
        drivers[name] = Driver(name=name, value=dc.value, description=dc.description, unit=dc.unit)
    assumptions = Assumptions(drivers=drivers, scenarios=config.assumptions.scenarios)
    model.set_assumptions(assumptions)

    # Line items
    from financial_model.core.statement import StatementType
    from financial_model.core.line_item import LineItem, LineItemType, CalculationRule

    for stmt_config in config.statements:
        stmt_type = StatementType(stmt_config.type)
        for item_config in stmt_config.line_items:
            item = LineItem(
                name=item_config.name,
                line_type=LineItemType(item_config.line_type),
                statement_type=item_config.statement_type,
                parent=item_config.parent,
                children=item_config.children,
                calculation_rule=CalculationRule(item_config.calculation_rule) if item_config.calculation_rule else None,
                calculation_params=item_config.calculation_params,
                driver=Driver(**item_config.driver.model_dump()) if item_config.driver else None,
                format=item_config.format,
                unit=item_config.unit,
                order=item_config.order,
                is_total=item_config.is_total,
                description=item_config.description,
            )
            if stmt_type == StatementType.INCOME_STATEMENT:
                model.add_income_statement_item(item)
            elif stmt_type == StatementType.BALANCE_SHEET:
                model.add_balance_sheet_item(item)
            elif stmt_type == StatementType.CASH_FLOW:
                model.add_cash_flow_item(item)

    return model


def _config_to_dcf_model(config: ModelConfig) -> DCFModel:
    model = _config_to_model(config)
    dcf_model = DCFModel(
        name=model.name,
        periods=model.periods,
        assumptions=model.assumptions,
    )
    # Copy line items
    dcf_model.income_statement = model.income_statement
    dcf_model.balance_sheet = model.balance_sheet
    dcf_model.cash_flow = model.cash_flow
    return dcf_model


def _config_to_lbo_model(config: ModelConfig) -> LBOModel:
    model = _config_to_model(config)
    lbo_model = LBOModel(
        name=model.name,
        periods=model.periods,
        assumptions=model.assumptions,
    )
    lbo_model.income_statement = model.income_statement
    lbo_model.balance_sheet = model.balance_sheet
    lbo_model.cash_flow = model.cash_flow
    return lbo_model


def _result_to_dict(result) -> Dict[str, Any]:
    return {
        "income_statement": result.income_statement.to_dict(),
        "balance_sheet": result.balance_sheet.to_dict(),
        "cash_flow": result.cash_flow.to_dict(),
        "periods": [p.label for p in result.periods],
        "bs_balanced": result.bs_balance_result.balanced,
        "bs_max_diff": result.bs_balance_result.max_diff,
        "cf_balanced": result.cf_balance_result.balanced,
        "cf_max_diff": result.cf_balance_result.max_diff,
        "metadata": result.metadata,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)