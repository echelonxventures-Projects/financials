"""
Assumptions management with versioning and scenario support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from financial_model.core.line_item import Driver


class DriverConfig(BaseModel):
    name: str
    value: float | list[float]
    description: str = ""
    unit: str = ""
    source: str = "assumption"


class AssumptionsConfig(BaseModel):
    """Pydantic model for YAML validation."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    drivers: dict[str, DriverConfig] = Field(default_factory=dict)
    scenarios: dict[str, dict[str, float | list[float]]] = Field(default_factory=dict)


_RESERVED_KEYS = {"drivers", "scenarios", "metadata"}


@dataclass
class Assumptions:
    """
    Container for all model assumptions (drivers).
    Supports base case + named scenarios with override semantics.
    """

    drivers: dict[str, Driver] = field(default_factory=dict)
    scenarios: dict[str, dict[str, float | list[float]]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Ensure all drivers are Driver objects
        for name, driver in list(self.drivers.items()):
            if isinstance(driver, dict):
                data = dict(driver)
                data.setdefault("name", name)
                self.drivers[name] = Driver(**data)
        if self.scenarios is None:
            self.scenarios = {}
        if self.metadata is None:
            self.metadata = {}

    # ------------------------------------------------------------------ #
    # Access
    # ------------------------------------------------------------------ #
    def get(self, name: str, scenario: str | None = None) -> Driver | None:
        """Get driver, applying scenario override if provided."""
        driver = self.drivers.get(name)
        if not driver:
            return None

        if scenario and scenario in self.scenarios:
            override = self.scenarios[scenario].get(name)
            if override is not None:
                return Driver(
                    name=driver.name,
                    value=override,
                    description=driver.description,
                    unit=driver.unit,
                    source=driver.source,
                )
        return driver

    def get_value(self, name: str, period_idx: int, scenario: str | None = None) -> float:
        driver = self.get(name, scenario)
        return driver.get_value(period_idx) if driver else 0.0

    def effective_drivers(self, scenario: str | None = None) -> dict[str, Driver]:
        """All drivers with the scenario's overrides applied."""
        drivers: dict[str, Driver] = {}
        for name in self.drivers:
            driver = self.get(name, scenario)
            if driver is not None:
                drivers[name] = driver
        return drivers

    def set_driver(self, driver: Driver) -> Assumptions:
        self.drivers[driver.name] = driver
        return self

    def set_scenario(self, name: str, overrides: dict[str, float | list[float]]) -> Assumptions:
        self.scenarios[name] = overrides
        return self

    def scenario_names(self) -> list[str]:
        return list(self.scenarios.keys())

    def merge(self, other: Assumptions) -> Assumptions:
        """Overlay ``other`` on top of this instance (drivers, scenarios, metadata)."""
        self.drivers.update(other.drivers)
        self.scenarios.update(other.scenarios)
        self.metadata.update(other.metadata)
        return self

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Assumptions:
        """
        Build from a mapping in either the nested format
        ``{"drivers": {...}, "scenarios": {...}, "metadata": {...}}`` or the flat
        format ``{driver_name: {"value": ...}, ...}``. Driver names are injected
        from the mapping keys when absent.
        """
        if not data:
            return cls()
        nested = "drivers" in data or set(data.keys()) <= _RESERVED_KEYS
        if nested:
            raw_drivers = data.get("drivers", {}) or {}
        else:
            raw_drivers = {k: v for k, v in data.items() if k not in _RESERVED_KEYS}
        scenarios = data.get("scenarios", {}) or {}
        metadata = data.get("metadata", {}) or {}

        drivers: dict[str, Driver] = {}
        for name, spec in raw_drivers.items():
            if isinstance(spec, Driver):
                drivers[name] = spec
            elif isinstance(spec, dict):
                payload = dict(spec)
                payload.setdefault("name", name)
                drivers[name] = Driver(**payload)
            else:
                drivers[name] = Driver(name=name, value=spec)
        return cls(drivers=drivers, scenarios=dict(scenarios), metadata=dict(metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": dict(self.metadata),
            "drivers": {
                name: {
                    "name": d.name,
                    "value": d.value,
                    "description": d.description,
                    "unit": d.unit,
                    "source": d.source,
                }
                for name, d in self.drivers.items()
            },
            "scenarios": {k: dict(v) for k, v in self.scenarios.items()},
        }

    @classmethod
    def from_yaml(cls, path: str | Path) -> Assumptions:
        with Path(path).open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if "drivers" in data:
            for name, spec in (data.get("drivers") or {}).items():
                if isinstance(spec, dict):
                    spec.setdefault("name", name)
            config = AssumptionsConfig(**data)
            drivers = {name: Driver(**dc.model_dump()) for name, dc in config.drivers.items()}
            return cls(drivers=drivers, scenarios=config.scenarios, metadata=config.metadata)
        return cls.from_dict(data)

    def to_yaml(self, path: str | Path) -> None:
        config = AssumptionsConfig(
            metadata=self.metadata,
            drivers={name: DriverConfig(**d.__dict__) for name, d in self.drivers.items()},
            scenarios=self.scenarios,
        )
        with Path(path).open("w", encoding="utf-8") as f:
            yaml.dump(config.model_dump(), f, sort_keys=False)

    def copy(self) -> Assumptions:
        return Assumptions(
            drivers={k: Driver(**v.__dict__) for k, v in self.drivers.items()},
            scenarios={k: dict(v) for k, v in self.scenarios.items()},
            metadata=dict(self.metadata),
        )
