"""
FastAPI REST API for financial modeling.
"""

from __future__ import annotations

import hmac
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from financial_model import __version__, config_loader
from financial_model.core.assumptions import Assumptions
from financial_model.core.line_item import LineItem
from financial_model.core.model import FinancialModel, ModelResult
from financial_model.core.period import PeriodIndex, PeriodType
from financial_model.core.statement import StatementType
from financial_model.data.loader import config_from_statements
from financial_model.data.providers import available_providers, fetch_statements, statements_from_mapping
from financial_model.data.synthetic import generate_company, profiles
from financial_model.exporters.json_exporter import result_to_dict, table_to_jsonable, to_jsonable
from financial_model.models.dcf import DCFModel
from financial_model.models.lbo import LBOModel
from financial_model.models.ma import MergerModel
from financial_model.models.registry import MODEL_CLASSES, normalize_model_type
from financial_model.schema import (  # noqa: F401 - re-exported: request bodies share the config schema
    AssumptionsConfig,
    DriverConfig,
    LineItemConfig,
    ModelConfig,
    PeriodConfig,
    StatementConfig,
)
from financial_model.utils.error_handler import DataFetchError
from financial_model.utils.optional import MissingDependencyError, require
from financial_model.utils.rate_limiter import CircuitBreakerOpen, RateLimitExceeded
from financial_model.utils.validation import ValidationError as InputValidationError

logger = logging.getLogger("financial_model.api")

# Paths that stay reachable without an API key (liveness probes, docs).
PUBLIC_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}


def create_app(settings: dict[str, Any] | None = None) -> FastAPI:
    """Build the API app from runtime settings (``config_loader.get_config()`` by default)."""
    cfg = settings if settings is not None else config_loader.get_config()
    api_cfg = cfg.get("api", {})
    logging.basicConfig(level=str(cfg.get("logging", {}).get("level", "INFO")).upper())

    application = FastAPI(
        title="Financial Model API",
        description="REST API for three-statement, DCF, LBO, budget and M&A models",
        version=__version__,
    )
    application.state.settings = cfg

    origins = list(api_cfg.get("cors_origins") or [])
    if origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type", "X-API-Key", "Authorization"],
        )

    platform_cfg = cfg.get("platform", {}) or {}
    platform_mode = bool(platform_cfg.get("enabled"))
    # In platform mode tenants authenticate with their own keys; the shared key is not used.
    api_key = "" if platform_mode else str(api_cfg.get("api_key") or os.environ.get("FIN_API_KEY", ""))
    max_bytes = int(float(api_cfg.get("max_request_mb", 10)) * 1024 * 1024)

    @application.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if api_key and request.url.path not in PUBLIC_PATHS:
            supplied = request.headers.get("x-api-key", "")
            if not hmac.compare_digest(supplied.encode(), api_key.encode()):
                return JSONResponse({"detail": "invalid or missing X-API-Key"}, status_code=401)
        length = request.headers.get("content-length")
        if length is not None and length.isdigit() and int(length) > max_bytes:
            return JSONResponse({"detail": f"request body exceeds {max_bytes} bytes"}, status_code=413)
        return await call_next(request)

    if platform_mode:
        from financial_model.platform.api import install_platform
        from financial_model.platform.service import PlatformService

        service = PlatformService.from_settings(platform_cfg)
        if service.secret_is_ephemeral:
            logger.warning("platform.secret_key is not set: login tokens will not survive a restart")
        install_platform(application, router, service)
    else:
        application.include_router(router)
    return application


def _http_error(exc: Exception) -> HTTPException:
    """Map domain errors to HTTP status codes (anything else is a 400 bad request)."""
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, InputValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, MissingDependencyError):
        return HTTPException(status_code=501, detail=str(exc))
    if isinstance(exc, RateLimitExceeded):
        return HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, CircuitBreakerOpen):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, DataFetchError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


router = APIRouter()


class CalculateRequest(BaseModel):
    config: ModelConfig
    scenario: str | None = None


class DCFRequest(BaseModel):
    config: ModelConfig
    scenario: str | None = None
    wacc_range: list[float] | None = None
    growth_range: list[float] | None = None


class LBORequest(BaseModel):
    config: ModelConfig
    scenario: str | None = None


class MARequest(BaseModel):
    """M&A deal: ``acquirer`` / ``target`` are inline model configs (paths are not accepted)."""

    config: dict[str, Any]
    scenario: str | None = None


class ChartRequest(BaseModel):
    config: dict[str, Any]
    scenario: str | None = None
    theme: str = "light"
    offline: bool = True


class FetchRequest(BaseModel):
    ticker: str = Field(..., description="Source identifier for the provider (a ticker for yfinance)")
    years: int | None = Field(None, description="History years; default: settings defaults.history_years")
    provider: str | None = Field(None, description="Remote data provider; default: settings data.default_provider")


class ImportRequest(BaseModel):
    """Your own historical statements, inline: ``{period: {line: value}}`` per statement."""

    name: str = "Imported Company"
    income_statement: dict[str, dict[str, Any]]
    balance_sheet: dict[str, dict[str, Any]] = Field(default_factory=dict)
    cash_flow: dict[str, dict[str, Any]] = Field(default_factory=dict)
    key_stats: dict[str, Any] = Field(default_factory=dict)
    years: int | None = Field(None, description="Projection years; default: settings defaults.years")


class SyntheticRequest(BaseModel):
    """Omitted fields use settings defaults.industry and the profile's parameters (data/synthetic.yaml)."""

    industry: str | None = None
    years: int | None = None
    start_year: int | None = None
    base_arr: float | None = None
    arr_growth: float | None = None


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.post("/calculate")
async def calculate_model(request: CalculateRequest) -> dict[str, Any]:
    """Run a model calculation (model class chosen by ``config.model_type``)."""
    try:
        model = _config_to_model(request.config)
        result = model.calculate(scenario=request.scenario)
        return _result_to_dict(result, model.all_tables(result))
    except Exception as e:
        raise _http_error(e) from e


@router.post("/ma")
async def calculate_ma(request: MARequest) -> dict[str, Any]:
    """M&A accretion/dilution, deal summary and contribution analysis."""
    try:
        model = _config_to_merger(request.config)
        tables = model.analysis_tables(request.scenario)
        return {name: table_to_jsonable(table) for name, table in tables.items()}
    except Exception as e:
        raise _http_error(e) from e


@router.post("/charts", response_class=HTMLResponse)
async def charts(request: ChartRequest) -> HTMLResponse:
    """Interactive HTML chart dashboard for any model config (including M&A)."""
    try:
        require("plotly", "POST /charts")
        from financial_model.visualization import ChartBuilder

        cfg = request.config
        if normalize_model_type(cfg.get("model_type")) == "ma":
            model: Any = _config_to_merger(cfg)
        else:
            model = _config_to_model(ModelConfig.model_validate(cfg))
        builder = ChartBuilder(theme=request.theme)
        figs = builder.model_charts(model, scenario=request.scenario)
        with tempfile.TemporaryDirectory() as tmp:
            path = builder.export_dashboard(figs, f"{tmp}/charts.html", title=model.name, offline=request.offline)
            return HTMLResponse(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise _http_error(e) from e


@router.post("/dcf")
async def calculate_dcf(request: DCFRequest) -> dict[str, Any]:
    """Run DCF valuation."""
    try:
        model = _config_to_dcf_model(request.config)
        result = model.calculate(scenario=request.scenario)
        dcf_result = model.calculate_dcf(result)

        response: dict[str, Any] = {
            "model_result": _result_to_dict(result),
            "dcf": _jsonable(dcf_result),
            "free_cash_flow": table_to_jsonable(model.analysis_tables(result)["Free Cash Flow"]),
        }
        if request.wacc_range and request.growth_range:
            sens = model.sensitivity_analysis(
                result,
                wacc_range=(request.wacc_range[0], request.wacc_range[1]),
                growth_range=(request.growth_range[0], request.growth_range[1]),
            )
            response["sensitivity"] = _jsonable(sens.to_dict())
        return response
    except Exception as e:
        raise _http_error(e) from e


@router.post("/lbo")
async def calculate_lbo(request: LBORequest) -> dict[str, Any]:
    """Run LBO analysis."""
    try:
        model = _config_to_lbo_model(request.config)
        result = model.calculate(scenario=request.scenario)
        lbo_result = model.calculate_returns(result)
        su = model.sources_uses(result)
        return {
            "model_result": _result_to_dict(result),
            "lbo": _jsonable(lbo_result),
            "sources_uses": {
                "sources": _jsonable(su["sources"].to_dict(orient="records")),
                "uses": _jsonable(su["uses"].to_dict(orient="records")),
                "entry_ev": su["entry_ev"],
                "total_uses": su["total_uses"],
                "equity_amt": su["equity_amt"],
                "debt_amt": su["debt_amt"],
            },
            "debt_schedule": _jsonable(model.debt_schedule(result).to_dict(orient="index")),
        }
    except Exception as e:
        raise _http_error(e) from e


@router.post("/fetch")
async def fetch_company(request: FetchRequest) -> dict[str, Any]:
    """Fetch historical statements from a remote data provider."""
    try:
        years = request.years or int(config_loader.default("history_years"))
        data = fetch_statements(request.ticker, years, request.provider, for_api=True)
        return {
            "ticker": request.ticker,
            "provider": data.get("provider"),
            "income_statement": _jsonable(data["income_statement"].to_dict()),
            "balance_sheet": _jsonable(data["balance_sheet"].to_dict()),
            "cash_flow": _jsonable(data["cash_flow"].to_dict()),
            "key_stats": _jsonable(data["key_stats"]),
        }
    except Exception as e:
        raise _http_error(e) from e


@router.post("/import")
async def import_statements(request: ImportRequest) -> dict[str, Any]:
    """Derive a model config from statements you supply (no data provider or network needed).

    The returned ``config`` can be posted straight to /calculate, /dcf, /lbo or /charts.
    """
    try:
        years = request.years if request.years is not None else int(config_loader.default("years"))
        if years < 1:
            raise ValueError("years must be >= 1")
        data = statements_from_mapping(
            {
                "income_statement": request.income_statement,
                "balance_sheet": request.balance_sheet,
                "cash_flow": request.cash_flow,
                "key_stats": request.key_stats,
            },
            years,
        )
        if data["income_statement"].empty:
            raise ValueError("income_statement has no numeric data")
        config = config_from_statements(data, request.name, years, metadata={"source": "import"})
        return {"config": _jsonable(config)}
    except Exception as e:
        raise _http_error(e) from e


@router.get("/providers")
async def list_providers() -> dict[str, list[dict[str, Any]]]:
    """Data providers selectable via /fetch."""
    return {
        "providers": [
            {"name": name, "remote": p.remote, "description": p.description}
            for name, p in available_providers().items()
            if p.allow_in_api
        ]
    }


@router.post("/synthetic")
async def generate_synthetic(request: SyntheticRequest) -> dict[str, Any]:
    """Generate synthetic company data."""
    industry = request.industry or str(config_loader.default("industry"))
    if industry not in profiles():
        raise HTTPException(
            status_code=400, detail=f"Industry {industry} not supported. Available: {', '.join(profiles())}"
        )
    try:
        data = generate_company(
            industry,
            start_year=request.start_year,
            years=request.years,
            base_arr=request.base_arr,
            arr_growth=request.arr_growth,
        )
        return {
            "industry": industry,
            "income_statement": _jsonable(data["income_statement"].to_dict()),
            "balance_sheet": _jsonable(data["balance_sheet"].to_dict()),
            "cash_flow": _jsonable(data["cash_flow"].to_dict()),
            "periods": [p.label for p in data["periods"]],
        }
    except Exception as e:
        raise _http_error(e) from e


@router.post("/export/excel")
async def export_excel(request: CalculateRequest) -> FileResponse:
    """Export model to an Excel file."""
    try:
        model = _config_to_model(request.config)
        result = model.calculate(scenario=request.scenario)
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            path = tmp.name
        try:
            model.export_excel(path, result)
        except Exception:
            Path(path).unlink(missing_ok=True)
            raise
        safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in request.config.name) or "model"
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=f"{safe_name}.xlsx",
            background=BackgroundTask(Path(path).unlink, missing_ok=True),
        )
    except Exception as e:
        raise _http_error(e) from e


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy", "version": __version__}


@router.get("/models")
async def list_models() -> dict[str, list[str]]:
    return {"model_types": [*MODEL_CLASSES, "ma"]}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _config_to_model(config: ModelConfig, cls: type[FinancialModel] | None = None) -> FinancialModel:
    if cls is None:
        key = normalize_model_type(config.model_type)
        if key not in MODEL_CLASSES:
            raise ValueError(f"model_type '{config.model_type}' is not supported here; use the /{key} endpoint")
        cls = MODEL_CLASSES[key]
    model = cls(name=config.name)

    periods = PeriodIndex.generate(
        start_year=config.periods.start_year,
        years=config.periods.years,
        period_type=PeriodType(config.periods.type),
        count=config.periods.count,
    )
    model.set_periods(periods)

    drivers = {
        name: {"value": dc.value, "description": dc.description, "unit": dc.unit}
        for name, dc in config.assumptions.drivers.items()
    }
    model.merge_assumptions(
        Assumptions.from_dict(
            {"drivers": drivers, "scenarios": config.assumptions.scenarios, "metadata": config.assumptions.metadata}
        )
    )

    for stmt_config in config.statements:
        stmt_type = StatementType(stmt_config.type)
        for item_config in stmt_config.line_items:
            payload = item_config.model_dump()
            if payload.get("driver") is not None:
                payload["driver"] = {"name": item_config.name, **payload["driver"]}
            item = LineItem.from_dict(payload)
            if stmt_type == StatementType.INCOME_STATEMENT:
                model.add_income_statement_item(item)
            elif stmt_type == StatementType.BALANCE_SHEET:
                model.add_balance_sheet_item(item)
            else:
                model.add_cash_flow_item(item)

    return model


def _config_to_dcf_model(config: ModelConfig) -> DCFModel:
    model = _config_to_model(config, DCFModel)
    assert isinstance(model, DCFModel)
    return model


def _config_to_lbo_model(config: ModelConfig) -> LBOModel:
    model = _config_to_model(config, LBOModel)
    assert isinstance(model, LBOModel)
    return model


def _config_to_merger(config: dict[str, Any]) -> MergerModel:
    """M&A model from an inline deal config. File paths are rejected so requests cannot read server files."""
    for side in ("acquirer", "target"):
        if not isinstance(config.get(side), dict):
            raise ValueError(f"'{side}' must be an inline model config (file paths are not accepted by the API)")
        ModelConfig.model_validate(config[side])  # same schema checks as /calculate
    return MergerModel.from_config(config)


def _jsonable(obj: Any) -> Any:
    """Convert pandas/numpy containers to JSON-safe python values (NaN -> None, keys -> str)."""
    return to_jsonable(obj)


def _result_to_dict(result: ModelResult, extras: dict[str, Any] | None = None) -> dict[str, Any]:
    return result_to_dict(result, extras)


app = create_app()


if __name__ == "__main__":
    import uvicorn

    _api = config_loader.get_config()["api"]
    uvicorn.run(app, host=_api["host"], port=int(_api["port"]))
