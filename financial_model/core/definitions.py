"""
Model definitions: every model's defaults (and, for declarative models, its structure) as data.

A definition is a YAML file named ``<model_type>.yaml``::

    model_type: dcf
    extends: three-statement        # inherit everything, change only what differs
    name: DCF Model
    drivers:                        # starting value, description and unit of each driver
      risk_free_rate: {value: 0.045, description: Risk-free rate, unit: "%"}
    scenarios: {}
    ...                             # any other section a model reads (labels, starters...)

Files are searched in the directories of the settings key ``models.definition_paths``
first, then the built-in ``financial_model/models/definitions``. A file found earlier
replaces a built-in one of the same name, so an operator can change any default
without touching code. ``extends`` chains are merged: mappings deep-merge (a ``null``
value deletes the inherited entry), everything else is replaced.

Nothing a model uses as a default is written in Python; models read it from here.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from financial_model.core.line_item import Driver

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "models" / "definitions"
#: Keys that belong to one file only and are not inherited through ``extends``.
NOT_INHERITED = frozenset({"aliases", "abstract", "starter"})


class DefinitionError(ValueError):
    """A definition is missing, malformed, or extends itself."""


def definition_dirs() -> tuple[Path, ...]:
    """Search path: ``models.definition_paths`` (settings / FIN_MODELS_DEFINITION_PATHS), then built-ins."""
    from financial_model.config_loader import get_config

    models_cfg = get_config().get("models") or {}
    extra: list[Any] = []
    for key in ("definition_paths", "template_paths"):  # template_paths: earlier name, still read
        value = models_cfg.get(key) or []
        extra.extend([value] if isinstance(value, (str, Path)) else value)
    return (*(Path(p).expanduser() for p in extra if str(p)), BUILTIN_DIR)


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in overlay.items():
        if v is None and k in out and isinstance(out[k], dict):
            out.pop(k)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif v is None:
            out.pop(k, None)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _find(model_type: str, dirs: tuple[Path, ...]) -> Path | None:
    for directory in dirs:
        path = directory / f"{model_type}.yaml"
        if path.is_file():
            return path
    return None


@lru_cache(maxsize=256)
def _load_cached(model_type: str, dirs: tuple[Path, ...]) -> dict[str, Any] | None:
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    key: str | None = model_type
    while key:
        if key in seen:
            raise DefinitionError(f"definition '{model_type}' extends itself through '{key}'")
        seen.add(key)
        path = _find(key, dirs)
        if path is None:
            if key == model_type:
                return None
            raise DefinitionError(f"definition '{chain[-1].get('model_type')}' extends unknown '{key}'")
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise DefinitionError(f"{path}: a model definition must be a mapping")
        data.setdefault("model_type", key)
        data["_source"] = str(path)
        chain.append(data)
        key = data.get("extends")
    merged: dict[str, Any] = {}
    for depth, layer in reversed(list(enumerate(chain))):
        own = depth == 0
        merged = _merge(merged, {k: v for k, v in layer.items() if k != "extends" and (own or k not in NOT_INHERITED)})
    merged["model_type"] = model_type
    merged["_chain"] = [layer["model_type"] for layer in chain]
    return merged


def load_definition(model_type: str, missing_ok: bool = False) -> dict[str, Any]:
    """The merged definition of ``model_type`` (a fresh copy)."""
    found = _load_cached(model_type, definition_dirs())
    if found is None:
        if missing_ok:
            return {}
        raise DefinitionError(f"no definition '{model_type}.yaml' in {', '.join(str(d) for d in definition_dirs())}")
    return copy.deepcopy(found)


def clear_cache() -> None:
    """Forget loaded definitions (after editing files or changing ``models.definition_paths``)."""
    _load_cached.cache_clear()


def available_definitions() -> dict[str, Path]:
    """Every ``<model_type>.yaml`` on the search path (earlier directories win)."""
    found: dict[str, Path] = {}
    for directory in definition_dirs():
        if directory.is_dir():
            for path in sorted(directory.glob("*.yaml")):
                found.setdefault(path.stem, path)
    return found


def engine_section(name: str, definition: dict[str, Any] | None = None) -> dict[str, Any]:
    """Section ``name`` (``lines``, ``line_groups``, ``balancing``...) of ``definition`` over the
    three-statement one, so a definition that does not extend it (e.g. budget) still has every role."""
    base = load_definition("three-statement", missing_ok=True).get(name)
    own = (definition or {}).get(name)
    if isinstance(base, dict) or isinstance(own, dict):
        return _merge(base if isinstance(base, dict) else {}, own if isinstance(own, dict) else {})
    raise DefinitionError(f"definition section '{name}' must be a mapping")


def engine_value(name: str, definition: dict[str, Any] | None = None) -> Any:
    """A scalar definition entry, falling back to the three-statement definition's."""
    own = (definition or {}).get(name)
    return own if own is not None else load_definition("three-statement", missing_ok=True).get(name)


def line_role(lines: dict[str, Any], role: str) -> str:
    """The line name of an engine ``role`` (definition ``lines``); a clear error when it is missing."""
    name = lines.get(role)
    if not name:
        raise DefinitionError(f"no line for role '{role}' in the definition's `lines` section")
    return str(name)


def driver_from_spec(name: str, spec: Any) -> Driver:
    """``{value, description, unit, source}`` (or a bare value) -> Driver."""
    if not isinstance(spec, dict):
        spec = {"value": spec}
    if "value" not in spec:
        raise DefinitionError(f"driver '{name}' has no value")
    driver = Driver(name, spec["value"], str(spec.get("description") or ""), str(spec.get("unit") or ""))
    if spec.get("source"):
        driver.source = str(spec["source"])
    return driver


def definition_drivers(definition: dict[str, Any]) -> dict[str, Driver]:
    return {name: driver_from_spec(name, spec) for name, spec in (definition.get("drivers") or {}).items()}
