"""Tests for the env_helper module."""

import os
import pytest

from financial_model import env_helper


class TestEnvGet:
    def test_returns_default_when_not_set(self):
        assert env_helper.env_get("NONEXISTENT", "fallback") == "fallback"

    def test_returns_env_value_when_set(self, monkeypatch):
        monkeypatch.setenv("FIN_TEST_KEY", "hello")
        assert env_helper.env_get("TEST_KEY") == "hello"

    def test_custom_prefix(self, monkeypatch):
        monkeypatch.setenv("MY_CUSTOM_KEY", "val")
        assert env_helper.env_get("CUSTOM_KEY", env_prefix="MY_") == "val"

    def test_none_default(self, monkeypatch):
        monkeypatch.delenv("FIN_MISSING", raising=False)
        assert env_helper.env_get("MISSING") is None


class TestEnvInt:
    def test_returns_default_when_not_set(self, monkeypatch):
        monkeypatch.delenv("FIN_INT_KEY", raising=False)
        assert env_helper.env_int("INT_KEY", 42) == 42

    def test_parses_string_from_env(self, monkeypatch):
        monkeypatch.setenv("FIN_INT_KEY", "99")
        assert env_helper.env_int("INT_KEY", 42) == 99

    def test_accepts_int_default_directly(self):
        # When default is int and no env, returns int
        assert env_helper.env_int("NO_SUCH_KEY", 7) == 7


class TestEnvFloat:
    def test_returns_default_when_not_set(self, monkeypatch):
        monkeypatch.delenv("FIN_FLOAT_KEY", raising=False)
        assert env_helper.env_float("FLOAT_KEY", 3.14) == 3.14

    def test_parses_string_from_env(self, monkeypatch):
        monkeypatch.setenv("FIN_FLOAT_KEY", "2.71")
        assert env_helper.env_float("FLOAT_KEY", 0.0) == 2.71


class TestEnvBool:
    def test_returns_default_when_not_set(self, monkeypatch):
        monkeypatch.delenv("FIN_BOOL_KEY", raising=False)
        assert env_helper.env_bool("BOOL_KEY", True) is True

    def test_true_values(self, monkeypatch):
        for val in ("1", "true", "TRUE", "yes", "YES", "on", "ON"):
            monkeypatch.setenv("FIN_BOOL_KEY", val)
            assert env_helper.env_bool("BOOL_KEY", False) is True, f"'{val}' should be True"

    def test_false_values(self, monkeypatch):
        for val in ("0", "false", "FALSE", "no", "NO", "off", "OFF"):
            monkeypatch.setenv("FIN_BOOL_KEY", val)
            assert env_helper.env_bool("BOOL_KEY", True) is False, f"'{val}' should be False"


class TestEnvList:
    def test_returns_default_when_not_set(self, monkeypatch):
        monkeypatch.delenv("FIN_LIST_KEY", raising=False)
        assert env_helper.env_list("LIST_KEY", [1, 2]) == [1, 2]

    def test_parses_comma_separated(self, monkeypatch):
        monkeypatch.setenv("FIN_LIST_KEY", "a, b, c")
        assert env_helper.env_list("LIST_KEY", []) == ["a", "b", "c"]

    def test_empty_string_returns_empty_list(self, monkeypatch):
        monkeypatch.setenv("FIN_LIST_KEY", "")
        assert env_helper.env_list("LIST_KEY", []) == []

    def test_custom_delimiter(self, monkeypatch):
        monkeypatch.setenv("FIN_LIST_KEY", "a:b:c")
        assert env_helper.env_list("LIST_KEY", [], delimiter=":") == ["a", "b", "c"]

    def test_whitespace_stripped(self, monkeypatch):
        monkeypatch.setenv("FIN_LIST_KEY", "  x  ,  y  ")
        assert env_helper.env_list("LIST_KEY", []) == ["x", "y"]
