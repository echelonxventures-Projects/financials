"""
Layered runtime configuration for the CLI and the self-hosted API.

Priority (highest wins):
  1. Environment variables (``FIN_<SECTION>_<KEY>``, e.g. ``FIN_API_PORT=9000``)
  2. YAML settings file(s): explicit paths, else ``$FIN_CONFIG_PATH``, else
     ``./financial.yaml`` / ``./config/financial.yaml`` when present
  3. Built-in defaults (below)

This is *runtime* configuration (where to listen, CORS, data-source limits). Model
assumptions live in the model YAML files passed to ``financial-model run`` / the API.

Usage::

    from financial_model.config_loader import get_config

    cfg = get_config()                 # defaults < settings file < env
    cfg = get_config("settings.yaml")  # explicit settings file
"""

from __future__ import annotations

import copy
import os
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from financial_model import __version__


def _platform_defaults() -> dict[str, Any]:
    path = Path(__file__).with_name("platform") / "settings.yaml"
    with path.open(encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    return data


def _builtin_defaults() -> dict[str, Any]:
    """Built-in settings (financial_model/settings.yaml) plus the platform section and the version."""
    with Path(__file__).with_name("settings.yaml").open(encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    data.setdefault("app", {})["version"] = __version__
    # Commercial multi-tenant mode (PaaS / B2B / B2C), off by default: see docs/commercial.md.
    data["platform"] = _platform_defaults()
    return data


# Every key is read somewhere: api.* by the API app and `financial-model serve`, data.* by the
# data providers, logging.level by both entry points, defaults.* by CLI options and API request
# fields, models.definition_paths by every model, platform.* by the multi-tenant mode.
_BUILTIN_DEFAULTS: dict[str, Any] = _builtin_defaults()

_DEFAULT_SETTINGS_FILES = (Path("financial.yaml"), Path("config/financial.yaml"))

_CACHED_CONFIG: dict[str, Any] | None = None


def _reset_cache() -> None:
    """Clear the cached default config (tests, or after changing env/settings files)."""
    global _CACHED_CONFIG
    _CACHED_CONFIG = None
    from financial_model.core.expressions import clear_limits  # values read once from the settings

    clear_limits()


def _load_yaml(path: str | Path) -> dict[str, Any]:
    """Load one YAML settings file; a missing or empty file contributes nothing."""
    p = Path(path)
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Settings file {p} must contain a mapping, got {type(data).__name__}")
    return data


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dicts without mutating either. Overlay wins on conflict."""
    merged = copy.deepcopy(base)
    for k, v in overlay.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = copy.deepcopy(v)
    return merged


def _resolve_path(parts: list[str], config: dict[str, Any]) -> list[str] | None:
    """Match underscore-split env parts against the config tree, longest key first.

    ``["api", "cors", "origins"]`` resolves to ``["api", "cors_origins"]`` and
    ``["data", "rate", "limit", "max", ...]`` to ``["data", "rate_limit", "max_..."]``.
    Returns None when the parts do not map onto existing keys.
    """
    d: Any = config
    used = 0
    resolved: list[str] = []
    while used < len(parts):
        if not isinstance(d, dict):
            return None
        for take in range(len(parts) - used, 0, -1):
            candidate = "_".join(parts[used : used + take])
            if candidate in d:
                resolved.append(candidate)
                used += take
                d = d[candidate]
                break
        else:
            return None
    return resolved


def _coerce_value(env_value: str, default: Any) -> Any:
    """Coerce a string env value to the type of the value it replaces."""
    if isinstance(default, bool):
        return env_value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(env_value)
    if isinstance(default, float):
        return float(env_value)
    if isinstance(default, list):
        return [item.strip() for item in env_value.split(",") if item.strip()]
    return env_value


def _apply_env_overrides(config: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return a copy of ``config`` with ``FIN_*`` environment variables applied.

    ``FIN_CONFIG_PATH`` selects the settings file and is not itself a setting.
    Unknown keys create new sections (``FIN_CUSTOM_SETTING`` -> ``custom.setting``).
    """
    source: Mapping[str, str] = os.environ if env is None else env
    result = copy.deepcopy(config)
    prefix = "FIN_"
    for key, value in source.items():
        if not key.startswith(prefix) or key == "FIN_CONFIG_PATH":
            continue
        parts = [p.lower() for p in key[len(prefix) :].split("_") if p]
        if not parts:
            continue
        path = _resolve_path(parts, result) or parts

        d = result
        for part in path[:-1]:
            if not isinstance(d.get(part), dict):
                d[part] = {}
            d = d[part]
        leaf = path[-1]
        if isinstance(d.get(leaf), dict):
            continue  # never replace a whole section with a scalar
        try:
            d[leaf] = _coerce_value(value, d.get(leaf))
        except ValueError as exc:
            raise ValueError(f"Environment variable {key}={value!r}: {exc}") from exc
    return result


def default(key: str) -> Any:
    """A ``defaults.<key>`` setting; ``start_year`` null means the current calendar year."""
    value = (get_config().get("defaults") or {}).get(key)
    if key == "start_year" and value is None:
        import datetime

        return datetime.date.today().year
    return value


def _maybe_generate_secret_key(config: dict[str, Any], config_path: Path | None) -> dict[str, Any]:
    """Auto-generate platform.secret_key if missing and enabled."""
    platform_cfg = config.get("platform", {})
    if not platform_cfg.get("enabled"):
        return config

    if platform_cfg.get("secret_key"):
        return config

    if not platform_cfg.get("require_secret_key", True):
        # Generate ephemeral key for this process
        ephemeral_key = secrets.token_urlsafe(32)
        config.setdefault("platform", {})["secret_key"] = ephemeral_key
        return config

    if not platform_cfg.get("auto_generate_secret_key", True):
        # Will fail later in serve with a clear error
        return config

    # Generate and persist if we have a writable config path
    generated_key = secrets.token_urlsafe(32)
    config.setdefault("platform", {})["secret_key"] = generated_key

    if config_path and config_path.exists():
        try:
            # Read current file, update secret_key, write back
            with config_path.open(encoding="utf-8") as f:
                file_data = yaml.safe_load(f) or {}
            file_data.setdefault("platform", {})["secret_key"] = generated_key
            with config_path.open("w", encoding="utf-8") as f:
                yaml.dump(file_data, f, sort_keys=False, default_flow_style=False)
        except Exception:
            # If we can't write, at least use it for this run
            pass

    return config


def get_config(
    yaml_paths: str | Path | list[str] | list[Path] | list[str | Path] | None = None,
    builtin: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the effective settings: built-in defaults < YAML file(s) < env vars.

    Only the default call (no arguments) is cached; call ``_reset_cache()`` after
    changing the environment. The returned dict is a fresh copy each time.
    """
    global _CACHED_CONFIG
    default_call = yaml_paths is None and builtin is None
    if default_call and _CACHED_CONFIG is not None:
        return copy.deepcopy(_CACHED_CONFIG)

    config = copy.deepcopy(_BUILTIN_DEFAULTS if builtin is None else builtin)

    paths: list[str | Path]
    resolved_config_path: Path | None = None
    if yaml_paths is None:
        env_path = os.environ.get("FIN_CONFIG_PATH")
        if env_path:
            paths = [env_path]
            resolved_config_path = Path(env_path)
        else:
            paths = [p for p in _DEFAULT_SETTINGS_FILES if p.exists()]
            resolved_config_path = paths[0] if paths else None
    elif isinstance(yaml_paths, (str, Path)):
        paths = [yaml_paths]
        resolved_config_path = Path(yaml_paths)
    else:
        paths = list(yaml_paths)
        resolved_config_path = Path(paths[0]) if paths else None

    for p in paths:
        config = _deep_merge(config, _load_yaml(p))

    config = _apply_env_overrides(config)

    # Auto-generate secret key if needed
    config = _maybe_generate_secret_key(config, resolved_config_path)

    if default_call:
        _CACHED_CONFIG = copy.deepcopy(config)
    return config
