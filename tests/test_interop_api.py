"""`financial-model openapi`: the REST contract other languages generate clients from (needs [api])."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from financial_model.api.main import create_app
from financial_model.cli.main import app


def test_openapi_command_matches_the_served_spec(tmp_path):
    out = tmp_path / "openapi.json"
    result = CliRunner().invoke(app, ["openapi", "-o", str(out)])
    assert result.exit_code == 0, result.output
    spec = json.loads(out.read_text(encoding="utf-8"))
    assert spec == json.loads(json.dumps(create_app({}).openapi()))
    assert {"/calculate", "/dcf", "/lbo", "/ma", "/health"} <= set(spec["paths"])


def test_api_request_bodies_use_the_shared_config_schema():
    schemas = create_app({}).openapi()["components"]["schemas"]
    assert {"ModelConfig", "DriverConfig", "PeriodConfig"} <= set(schemas)
    assert "enum" in schemas["PeriodConfig"]["properties"]["type"]
