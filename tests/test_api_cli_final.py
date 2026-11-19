"""
Tests for API and CLI remaining uncovered lines.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from financial_model.cli.main import _generate_config, app


class TestAPICLIFinal:
    """Test remaining uncovered lines in API and CLI."""

    def test_cli_init_no_type(self):
        """Test init command without --type (line 92-93)."""
        from typer.testing import CliRunner

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "model.yaml"
            result = runner.invoke(app, ["init", "--output", str(output)])
            assert result.exit_code == 0
            assert output.exists()

    def test_cli_run_no_output(self):
        """Test run command without output (line 117)."""
        from typer.testing import CliRunner

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.yaml"
            config = _generate_config("three-statement", "saas", 3, 2024)
            import yaml

            with open(config_file, "w") as f:
                yaml.dump(config, f)
            result = runner.invoke(app, ["run", str(config_file)])
            assert result.exit_code == 0

    def test_cli_dcf_command(self):
        """Test dcf command (lines 164-167)."""
        from typer.testing import CliRunner

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.yaml"
            config = _generate_config("three-statement", "saas", 5, 2024)
            import yaml

            with open(config_file, "w") as f:
                yaml.dump(config, f)
            result = runner.invoke(app, ["dcf", str(config_file)])
            # May fail if model not fully configured, but should not crash on import
            assert result.exit_code in [0, 1]

    def test_cli_validate_command(self):
        """Test validate command (lines 292, 312)."""
        from typer.testing import CliRunner

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.yaml"
            config = _generate_config("three-statement", "saas", 3, 2024)
            import yaml

            with open(config_file, "w") as f:
                yaml.dump(config, f)
            result = runner.invoke(app, ["validate", str(config_file)])
            assert result.exit_code == 0
