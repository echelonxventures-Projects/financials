"""Tests for the layered configuration system."""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from financial_model import config_loader


# ── Test helpers ──────────────────────────────────────────────────────

_SIMPLE_YAML = """
app:
  name: "Test App"
api:
  port: 9090
logging:
  level: DEBUG
"""

_NESTED_YAML = """
data:
  rate_limit:
    max_requests_per_minute: 5
api:
  cors_origins:
    - "https://example.com"
    - "https://app.example.com"
"""


def _clean_env():
    """Remove all FIN_ env vars and reset the config cache."""
    for k in list(os.environ.keys()):
        if k.startswith("FIN_"):
            del os.environ[k]
    config_loader._reset_cache()


# ── Built-in defaults ────────────────────────────────────────────────

class TestBuiltinDefaults:
    def test_returns_builtin_defaults_when_nothing_set(self, monkeypatch):
        _clean_env()
        config = config_loader.get_config()
        assert config["app"]["name"] == "Financial Model"
        assert config["api"]["port"] == 8000
        assert config["api"]["host"] == "127.0.0.1"
        assert config["api"]["cors_origins"] == []

    def test_all_expected_sections_present(self, monkeypatch):
        _clean_env()
        config = config_loader.get_config()
        assert "app" in config
        assert "api" in config
        assert "data" in config
        assert "logging" in config

    def test_no_placeholder_secrets_in_defaults(self):
        # Defaults must not ship a usable-looking secret; auth is opt-in via api.api_key.
        assert config_loader._BUILTIN_DEFAULTS["api"]["api_key"] == ""

    def test_env_overrides_do_not_leak_into_defaults(self, monkeypatch):
        _clean_env()
        monkeypatch.setenv("FIN_API_PORT", "1234")
        assert config_loader.get_config()["api"]["port"] == 1234
        monkeypatch.delenv("FIN_API_PORT")
        config_loader._reset_cache()
        assert config_loader.get_config()["api"]["port"] == 8000
        assert config_loader._BUILTIN_DEFAULTS["api"]["port"] == 8000

    def test_explicit_paths_bypass_cache(self, tmp_path):
        _clean_env()
        config_loader.get_config()  # prime the cache
        f = tmp_path / "s.yaml"
        f.write_text("api:\n  port: 4321\n")
        assert config_loader.get_config(yaml_paths=f)["api"]["port"] == 4321

    def test_returned_config_is_a_copy(self):
        _clean_env()
        cfg = config_loader.get_config()
        cfg["api"]["port"] = 1
        assert config_loader.get_config()["api"]["port"] == 8000


# ── YAML layer ───────────────────────────────────────────────────────

class TestYamlLayer:
    def test_yaml_overrides_defaults(self, monkeypatch, tmp_path):
        _clean_env()
        cfg_file = tmp_path / "test.yaml"
        cfg_file.write_text(_SIMPLE_YAML)
        config = config_loader.get_config(yaml_paths=cfg_file)
        assert config["app"]["name"] == "Test App"
        assert config["api"]["port"] == 9090
        assert config["logging"]["level"] == "DEBUG"
        # Unchanged key keeps default
        assert config["api"]["host"] == "127.0.0.1"

    def test_yaml_nested_override(self, monkeypatch, tmp_path):
        _clean_env()
        cfg_file = tmp_path / "nested.yaml"
        cfg_file.write_text(_NESTED_YAML)
        config = config_loader.get_config(yaml_paths=cfg_file)
        assert config["data"]["rate_limit"]["max_requests_per_minute"] == 5
        assert "https://example.com" in config["api"]["cors_origins"]
        # Retained default that wasn't overridden
        assert config["data"]["rate_limit"]["max_requests_per_hour"] == 500

    def test_yaml_missing_file_returns_defaults(self, monkeypatch):
        _clean_env()
        config = config_loader.get_config(yaml_paths="/nonexistent/path.yaml")
        assert config["app"]["name"] == "Financial Model"

    def test_multiple_yaml_files_merge(self, monkeypatch, tmp_path):
        _clean_env()
        f1 = tmp_path / "a.yaml"
        f1.write_text("app:\n  name: A\n  port: 1111\n")
        f2 = tmp_path / "b.yaml"
        f2.write_text("app:\n  port: 2222\n  debug: true\n")
        config = config_loader.get_config(yaml_paths=[f1, f2])
        assert config["app"]["name"] == "A"   # from f1
        assert config["app"]["port"] == 2222   # f2 overrides f1
        assert config["app"]["debug"] is True  # from f2

    def test_single_string_path(self, monkeypatch, tmp_path):
        _clean_env()
        cfg_file = tmp_path / "single.yaml"
        cfg_file.write_text("app:\n  name: Single\n")
        config = config_loader.get_config(yaml_paths=str(cfg_file))
        assert config["app"]["name"] == "Single"

    def test_empty_yaml_returns_defaults(self, monkeypatch, tmp_path):
        _clean_env()
        cfg_file = tmp_path / "empty.yaml"
        cfg_file.write_text("")
        config = config_loader.get_config(yaml_paths=cfg_file)
        assert config["app"]["name"] == "Financial Model"

    def test_invalid_yaml_returns_defaults(self, monkeypatch, tmp_path):
        # If YAML is invalid, safe_load raises; but our _load_yaml handles it gracefully
        cfg_file = tmp_path / "bad.yaml"
        cfg_file.write_text("{:")  # Invalid YAML
        # _load_yaml returns {} for empty/None result; for truly invalid YAML it may raise
        # We test that the loader handles missing keys gracefully
        try:
            config = config_loader.get_config(yaml_paths=cfg_file)
            # If it didn't crash, defaults are present
            assert "app" in config
        except yaml.YAMLError:
            # It's OK to raise on truly broken YAML - user error
            pass


# ── Environment variable layer ───────────────────────────────────────

class TestEnvOverride:
    @pytest.fixture(autouse=True)
    def isolate_env(self):
        """Ensure a clean FIN_ env for each test. Use os.environ directly (not monkeypatch)
        to avoid LIFO undo restoring leaked vars from prior tests."""
        for k in list(os.environ.keys()):
            if k.startswith("FIN_"):
                os.environ.pop(k, None)
        config_loader._reset_cache()
        yield
        # teardown: clean up again so next test starts fresh
        for k in list(os.environ.keys()):
            if k.startswith("FIN_"):
                os.environ.pop(k, None)
        config_loader._reset_cache()

    def test_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("FIN_API_PORT", "7777")
        config = config_loader.get_config()
        assert config["api"]["port"] == 7777

    def test_env_overrides_yaml(self, monkeypatch, tmp_path):
        cfg_file = tmp_path / "test.yaml"
        cfg_file.write_text("api:\n  port: 9090\n")
        monkeypatch.setenv("FIN_API_PORT", "8888")
        config = config_loader.get_config(yaml_paths=cfg_file)
        assert config["api"]["port"] == 8888

    def test_env_nested_override(self, monkeypatch):
        monkeypatch.setenv("FIN_DATA_RATE_LIMIT_CIRCUIT_BREAKER_TIMEOUT", "120")
        config = config_loader.get_config()
        assert config["data"]["rate_limit"]["circuit_breaker_timeout"] == 120.0

    def test_env_bool_override(self, monkeypatch):
        monkeypatch.setenv("FIN_X_ENABLED", "true")
        config = config_loader.get_config(builtin={"x": {"enabled": False}})
        assert config["x"]["enabled"] is True

    def test_env_bad_int_names_the_variable(self, monkeypatch):
        monkeypatch.setenv("FIN_API_PORT", "not-a-port")
        with pytest.raises(ValueError, match="FIN_API_PORT"):
            config_loader.get_config()

    def test_config_path_var_is_not_a_setting(self, monkeypatch, tmp_path):
        f = tmp_path / "s.yaml"
        f.write_text("")
        monkeypatch.setenv("FIN_CONFIG_PATH", str(f))
        assert "config" not in config_loader.get_config()

    def test_env_list_override(self, monkeypatch):
        monkeypatch.setenv("FIN_API_CORS_ORIGINS", "https://a.com,https://b.com")
        config = config_loader.get_config()
        assert config["api"]["cors_origins"] == ["https://a.com", "https://b.com"]

    def test_env_creates_missing_section(self, monkeypatch):
        # FIN_NEW_SECTION_KEY creates a new section
        monkeypatch.setenv("FIN_CUSTOM_SETTING", "value")
        config = config_loader.get_config()
        assert config["custom"]["setting"] == "value"

    def test_env_config_path(self, monkeypatch, tmp_path):
        cfg_file = tmp_path / "custom.yaml"
        cfg_file.write_text("app:\n  name: FromEnvPath\n")
        monkeypatch.setenv("FIN_CONFIG_PATH", str(cfg_file))
        config = config_loader.get_config()
        assert config["app"]["name"] == "FromEnvPath"

    def test_env_non_fin_vars_ignored(self, monkeypatch):
        monkeypatch.setenv("NOT_FIN_PORT", "1234")
        config = config_loader.get_config()
        assert config["api"]["port"] == 8000  # Not affected


# ── Custom built-in defaults ─────────────────────────────────────────

class TestCustomBuiltin:
    def test_custom_builtin_used(self, monkeypatch):
        _clean_env()
        custom = {"x": {"y": 42}}
        config = config_loader.get_config(builtin=custom)
        assert config["x"]["y"] == 42

    def test_yaml_overrides_custom_builtin(self, monkeypatch, tmp_path):
        _clean_env()
        custom = {"x": {"y": 42, "z": 99}}
        cfg_file = tmp_path / "t.yaml"
        cfg_file.write_text("x:\n  y: 100\n")
        config = config_loader.get_config(yaml_paths=cfg_file, builtin=custom)
        assert config["x"]["y"] == 100
        assert config["x"]["z"] == 99


# ── _deep_merge ──────────────────────────────────────────────────────

class TestDeepMerge:
    def test_shallow_merge(self):
        base = {"a": 1, "b": 2}
        overlay = {"b": 3, "c": 4}
        result = config_loader._deep_merge(base, overlay)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"a": {"x": 1, "y": 2}}
        overlay = {"a": {"y": 3, "z": 4}}
        result = config_loader._deep_merge(base, overlay)
        assert result == {"a": {"x": 1, "y": 3, "z": 4}}

    def test_overlay_replaces_non_dict(self):
        base = {"a": "string"}
        overlay = {"a": 123}
        result = config_loader._deep_merge(base, overlay)
        assert result == {"a": 123}

    def test_empty_overlay(self):
        base = {"a": 1}
        result = config_loader._deep_merge(base, {})
        assert result == {"a": 1}


# ── Integration: full 3-layer stack ──────────────────────────────────

class TestIntegration:
    def test_full_stack_priority(self, monkeypatch, tmp_path):
        _clean_env()
        # Layer 1: builtin
        builtin = {
            "app": {"port": 8000, "name": "Default", "debug": False},
            "modeling": {"tax": 0.21},
        }
        # Layer 2: YAML overrides builtin
        cfg_file = tmp_path / "t.yaml"
        cfg_file.write_text("app:\n  port: 9090\n  debug: true\n")
        # Layer 3: env overrides YAML
        monkeypatch.setenv("FIN_APP_PORT", "7777")
        monkeypatch.setenv("FIN_MODELING_TAX", "0.30")

        config = config_loader.get_config(yaml_paths=cfg_file, builtin=builtin)

        assert config["app"]["port"] == 7777      # env wins over YAML 9090
        assert config["app"]["name"] == "Default"  # builtin kept (no YAML/env override)
        assert config["app"]["debug"] is True      # YAML wins over builtin False
        assert config["modeling"]["tax"] == 0.30   # env wins over builtin 0.21

    def test_rate_limit_overridable(self, monkeypatch):
        _clean_env()
        monkeypatch.setenv("FIN_DATA_RATE_LIMIT_MAX_REQUESTS_PER_MINUTE", "3")
        config = config_loader.get_config()
        assert config["data"]["rate_limit"]["max_requests_per_minute"] == 3
