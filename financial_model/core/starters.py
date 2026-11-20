"""
Starter configs for ``financial-model init``: data in each definition's ``starter`` section.

::

    starter:
      fallback_variant: other          # used for an --industry the definition has no variant for
      period_paths: [periods]          # mappings that receive start_year / years
      truncate_to_years: [deal.synergy_phase_in, "assumptions.drivers.*.value"]   # lists cut to years
      cap_at_years: [assumptions.drivers.hold_years.value]                          # numbers capped at years
      variants:
        saas: {name: ..., model_type: ..., periods: {type: annual}, assumptions: {...}}
        other: {...}

Paths are dotted; ``*`` matches every key at that level.
"""

from __future__ import annotations

import copy
from typing import Any

from financial_model.core.definitions import DefinitionError, load_definition


def _walk(node: Any, parts: list[str]) -> list[tuple[Any, str]]:
    """(container, key) pairs a dotted path points at."""
    if not parts:
        return []
    head, rest = parts[0], parts[1:]
    if not isinstance(node, dict):
        return []
    keys = list(node) if head == "*" else ([head] if head in node else [])
    if not rest:
        return [(node, k) for k in keys]
    found: list[tuple[Any, str]] = []
    for k in keys:
        found.extend(_walk(node[k], rest))
    return found


def _implied_starter(definition: dict[str, Any]) -> dict[str, Any]:
    """A definition without a ``starter``: its own drivers (those differing from its parent) as the config."""
    chain = definition.get("_chain") or []
    parent = load_definition(chain[1]).get("drivers", {}) if len(chain) > 1 else {}
    own = {k: v for k, v in (definition.get("drivers") or {}).items() if parent.get(k) != v}
    config = {
        "name": definition.get("name") or definition["model_type"],
        "model_type": definition["model_type"],
        "periods": {"type": (definition.get("periods") or {}).get("type", "annual")},
        "assumptions": {"drivers": own, "scenarios": definition.get("starter_scenarios") or {}},
    }
    return {"fallback_variant": "default", "period_paths": ["periods"], "variants": {"default": config}}


def variants(model_type: str) -> list[str]:
    return list((load_definition(model_type).get("starter") or {}).get("variants") or {})


def starter_config(
    model_type: str, years: int = 5, start_year: int = 2025, variant: str | None = None
) -> dict[str, Any]:
    """A ready-to-run model config for ``model_type`` built from its definition's ``starter``."""
    definition = load_definition(model_type)
    spec = definition.get("starter")
    if not spec or not spec.get("variants"):
        spec = _implied_starter(definition)
    options = spec["variants"]
    chosen = variant if variant in options else spec.get("fallback_variant")
    if chosen not in options:
        raise DefinitionError(f"'{model_type}' starter has no variant '{variant}' and no usable fallback_variant")
    config = copy.deepcopy(options[chosen])
    years = max(1, int(years))
    for path in spec.get("period_paths") or ["periods"]:
        for container, key in _walk(config, path.split(".")):
            if isinstance(container[key], dict):
                container[key].update({"start_year": int(start_year), "years": years})
    for path in spec.get("truncate_to_years") or []:
        for container, key in _walk(config, path.split(".")):
            if isinstance(container[key], list):
                container[key] = container[key][:years]
    for path in spec.get("cap_at_years") or []:
        for container, key in _walk(config, path.split(".")):
            if isinstance(container[key], (int, float)):
                container[key] = min(container[key], years)
    # Keep the periods mapping in its usual position for readable YAML.
    if isinstance(config.get("periods"), dict):
        p = config["periods"]
        config["periods"] = (
            {"start_year": p.pop("start_year"), "years": p.pop("years"), **p} if "start_year" in p else p
        )
    return config
