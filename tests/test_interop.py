"""
Language-neutral interfaces: the config JSON Schema and the CLI's stdin/stdout JSON mode.

Any language can drive the toolkit by piping a JSON config to `financial-model run - -o -`
and validating configs against schemas/config.schema.json; these tests pin both contracts.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from financial_model.cli.main import app
from financial_model.schema import config_json_schema

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT / "schemas" / "config.schema.json"
EXAMPLE_CONFIGS = sorted((ROOT / "config").glob("*_saas.yaml"))

DCF = {
    "name": "Interop DCF",
    "model_type": "dcf",
    "periods": {"start_year": 2025, "years": 3, "type": "annual"},
    "assumptions": {"drivers": {"revenue": {"value": 10_000_000}, "revenue_growth": {"value": [0.3, 0.2, 0.1]}}},
}

runner = CliRunner()


def _shape(schema: dict) -> dict:
    """What a client relies on: each definition's property names and required fields."""
    return {
        name: (sorted(d.get("properties", {})), sorted(d.get("required", [])))
        for name, d in schema["$defs"].items()
    }


# --------------------------------------------------------------------------- #
# JSON Schema
# --------------------------------------------------------------------------- #
class TestSchema:
    def test_committed_schema_is_current(self):
        committed = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        generated = config_json_schema()
        assert _shape(committed) == _shape(generated), "run: financial-model schema -o schemas/config.schema.json"
        assert committed["if"] == generated["if"]

    def test_schema_command_prints_the_schema(self):
        result = runner.invoke(app, ["schema"])
        assert result.exit_code == 0
        assert json.loads(result.output) == config_json_schema()

    def test_schema_is_valid_draft_2020_12(self):
        jsonschema = pytest.importorskip("jsonschema")
        jsonschema.Draft202012Validator.check_schema(config_json_schema())

    @pytest.mark.parametrize("path", EXAMPLE_CONFIGS, ids=lambda p: p.name)
    def test_example_configs_validate(self, path):
        jsonschema = pytest.importorskip("jsonschema")
        jsonschema.validate(yaml.safe_load(path.read_text(encoding="utf-8")), config_json_schema())

    @pytest.mark.parametrize(
        "bad",
        [
            {"periods": {"type": "weekly"}},
            {"assumptions": {"drivers": {"revenue": {"value": "ten"}}}},
            {"model_type": "ma", "acquirer": {}},
            {"model_type": "merger", "acquirer": {}, "target": {}, "deal": {"bogus_term": 1}},
            {"statements": [{"type": "notes"}]},
        ],
    )
    def test_invalid_configs_are_rejected(self, bad):
        jsonschema = pytest.importorskip("jsonschema")
        assert not jsonschema.Draft202012Validator(config_json_schema()).is_valid(bad)


# --------------------------------------------------------------------------- #
# CLI as a JSON filter
# --------------------------------------------------------------------------- #
class TestStdioJson:
    def test_json_config_on_stdin_result_on_stdout(self):
        result = runner.invoke(app, ["run", "-", "-o", "-"], input=json.dumps(DCF))
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)  # stdout is pure JSON: nothing else printed
        assert list(payload["income_statement"]) == ["FY2025", "FY2026", "FY2027"]
        assert payload["analysis"]["DCF Valuation"]["ev_gordon"] > 0
        assert payload["bs_balanced"] and payload["cf_balanced"]

    def test_yaml_on_stdin(self):
        result = runner.invoke(app, ["run", "-", "-o", "-"], input=yaml.safe_dump(DCF))
        assert result.exit_code == 0, result.output
        assert "DCF Valuation" in json.loads(result.output)["analysis"]

    def test_format_json_without_output_prints_json(self):
        result = runner.invoke(app, ["run", str(ROOT / "config" / "lbo_saas.yaml"), "-f", "json"])
        assert result.exit_code == 0, result.output
        assert "income_statement" in json.loads(result.output)

    def test_stdout_matches_file_export(self, tmp_path):
        out = tmp_path / "r.json"
        cfg = str(ROOT / "config" / "three_statement_saas.yaml")
        assert runner.invoke(app, ["run", cfg, "-o", str(out)]).exit_code == 0
        result = runner.invoke(app, ["run", cfg, "-o", "-"])
        assert json.loads(result.output) == json.loads(out.read_text(encoding="utf-8"))

    def test_merger_deal_on_stdin(self):
        deal = yaml.safe_load((ROOT / "config" / "ma_saas.yaml").read_text(encoding="utf-8"))
        result = runner.invoke(app, ["ma", "-", "-o", "-"], input=json.dumps(deal))
        assert result.exit_code == 0, result.output
        assert set(json.loads(result.output)) == {"Deal Summary", "Accretion Dilution", "Contribution"}

    def test_validate_from_stdin(self):
        result = runner.invoke(app, ["validate", "-"], input=json.dumps(DCF))
        assert result.exit_code == 0, result.output
        assert "Config is valid" in result.output

    def test_only_json_goes_to_stdout(self):
        result = runner.invoke(app, ["run", "-", "-o", "-", "-f", "csv"], input=json.dumps(DCF))
        assert result.exit_code == 2

    @pytest.mark.parametrize(
        ("stdin", "message"),
        [
            (json.dumps({"periods": {"type": "weekly"}}), "invalid config <stdin>: 'weekly'"),
            (json.dumps({"model_type": "ma", "acquirer": {}}), "invalid config <stdin>"),
            (": : not yaml", "stdin is not valid JSON or YAML"),
            ("", "no config on stdin"),
            ("[1, 2]", "expected a mapping"),
        ],
    )
    def test_errors_are_one_line_and_exit_1(self, stdin, message):
        result = runner.invoke(app, ["run", "-", "-o", "-"], input=stdin)
        assert result.exit_code == 1
        assert message in result.output
        assert "Traceback" not in result.output

    def test_real_process_pipe(self):
        """The contract other languages use: a real subprocess, bytes in, bytes out."""
        proc = subprocess.run(
            [sys.executable, "-m", "financial_model.cli", "run", "-", "-o", "-"],
            input=json.dumps(DCF).encode("utf-8"),
            capture_output=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
        assert json.loads(proc.stdout.decode("utf-8"))["analysis"]["DCF Valuation"]["ev_gordon"] > 0
        bad = subprocess.run(
            [sys.executable, "-m", "financial_model.cli", "run", "-", "-o", "-"],
            input=b'{"periods": {"type": "weekly"}}',
            capture_output=True,
            timeout=120,
            check=False,
        )
        assert bad.returncode == 1
        assert bad.stdout == b""
        # Last line: third-party import warnings (e.g. urllib3 on LibreSSL) may precede it.
        assert bad.stderr.decode("utf-8").strip().splitlines()[-1].startswith("Error: invalid config")
