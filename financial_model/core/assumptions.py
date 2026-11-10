"""
Assumptions management with versioning and scenario support.
"""

from dataclasses import dataclass, field
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
        for name, driver in self.drivers.items():
            if not isinstance(driver, Driver):
                self.drivers[name] = Driver(**driver) if isinstance(driver, dict) else driver

    def get(self, name: str, scenario: str | None = None) -> Driver | None:
        """Get driver value, applying scenario override if provided."""
        driver = self.drivers.get(name)
        if not driver:
            return None

        if scenario and scenario in self.scenarios:
            override = self.scenarios[scenario].get(name)
            if override is not None:
                # Return new driver with overridden value
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

    def set_driver(self, driver: Driver) -> "Assumptions":
        self.drivers[driver.name] = driver
        return self

    def set_scenario(self, name: str, overrides: dict[str, float | list[float]]) -> "Assumptions":
        self.scenarios[name] = overrides
        return self

    def scenario_names(self) -> list[str]:
        return list(self.scenarios.keys())

    @classmethod
    def from_yaml(cls, path: str) -> "Assumptions":
        with open(path) as f:
            data = yaml.safe_load(f)
        config = AssumptionsConfig(**data)
        drivers = {name: Driver(**dc.model_dump()) for name, dc in config.drivers.items()}
        return cls(drivers=drivers, scenarios=config.scenarios, metadata=config.metadata)

    def to_yaml(self, path: str) -> None:
        config = AssumptionsConfig(
            metadata=self.metadata,
            drivers={name: DriverConfig(**d.__dict__) for name, d in self.drivers.items()},
            scenarios=self.scenarios,
        )
        with open(path, "w") as f:
            yaml.dump(config.model_dump(), f, sort_keys=False)

    def copy(self) -> "Assumptions":
        return Assumptions(
            drivers={k: Driver(**v.__dict__) for k, v in self.drivers.items()},
            scenarios={k: dict(v) for k, v in self.scenarios.items()},
            metadata=dict(self.metadata),
        )