import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------
# Configuration sources
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Load local secrets and environment variables.
load_dotenv(PROJECT_ROOT / ".env")

CONFIG_FILE = PROJECT_ROOT / "config.yaml"


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------


def required_secret(name: str) -> str:
    """Read a required secret from the environment.

    Raises:
        RuntimeError: If the secret is missing or empty.
    """
    value = os.getenv(name)

    if not value:
        raise RuntimeError(f"Required secret '{name}' is missing. Please set it in .env or as an environment variable.")

    return value


def parse_duration(value: str) -> timedelta:
    """Parse a duration such as '500ms', '2s', or '1m'."""
    value = value.strip().lower()

    if value.endswith("ms"):
        return timedelta(milliseconds=float(value[:-2]))

    if value.endswith("s"):
        return timedelta(seconds=float(value[:-1]))

    if value.endswith("m"):
        return timedelta(minutes=float(value[:-1]))

    if value.endswith("h"):
        return timedelta(hours=float(value[:-1]))

    raise ValueError(f"Invalid duration: {value!r}")


# ---------------------------------------------------------
# Settings
# ---------------------------------------------------------


@dataclass(frozen=True)
class ComponentConfig:
    type: str
    enabled: bool
    interval: int | None
    attributes: dict[str, Any]


@dataclass(frozen=True)
class CollectionConfig:
    timezone: str


@dataclass(frozen=True)
class StorageMeasurementConfig:
    source: str
    metric: str
    measurement_type: str = "current"

    @property
    def storage_key(self) -> tuple[str, str, str]:
        return (
            self.source,
            self.metric,
            self.measurement_type,
        )


@dataclass(frozen=True)
class StorageConfig:
    enabled: bool
    measurements: list[StorageMeasurementConfig]


@dataclass(frozen=True)
class EstimatorMeasurementConfig:
    source: str
    metric: str


@dataclass(frozen=True)
class EstimatorConfig:
    type: str
    enabled: bool
    latency: timedelta
    history_size: int
    measurements: list[EstimatorMeasurementConfig]


@dataclass(frozen=True)
class CalculatorInputConfig:
    source: str
    metric: str


@dataclass(frozen=True)
class CalculationConfig:
    source: str
    metric: str
    unit: str
    formula: str
    inputs: dict[str, CalculatorInputConfig]


@dataclass(frozen=True)
class CalculatorConfig:
    type: str
    enabled: bool
    calculations: list[CalculationConfig]


@dataclass(frozen=True)
class Config:
    collection: CollectionConfig
    collectors: list[ComponentConfig]
    databases: list[ComponentConfig]
    estimators: list[EstimatorConfig]
    calculators: list[CalculatorConfig]
    storage: StorageConfig


# ---------------------------------------------------------
# Component configuration
# ---------------------------------------------------------


def load_component_configs(
    config_data: dict[str, Any],
    key: str,
) -> list[ComponentConfig]:
    return [
        ComponentConfig(
            type=item["type"],
            enabled=item.get("enabled", True),
            interval=(int(item["interval"]) if item.get("interval") is not None else None),
            attributes=item.get("attributes", {}),
        )
        for item in config_data[key]
    ]


def load_storage_measurements(
    config_data: dict[str, Any],
) -> list[StorageMeasurementConfig]:
    measurements = []

    for source, measurement_types in config_data["storage"]["measurements"].items():
        # Allow the simple form:
        #
        # meter_grid:
        #   - grid_import_power
        #
        # which implicitly means measurement_type="current".
        if isinstance(measurement_types, list):
            measurements.extend(
                StorageMeasurementConfig(
                    source=source,
                    metric=metric,
                    measurement_type="current",
                )
                for metric in measurement_types
            )
            continue

        # Allow the explicit form:
        #
        # open_meteo:
        #   current:
        #     - temperature
        #   forecast:
        #     - temperature
        for measurement_type, metrics in measurement_types.items():
            measurements.extend(
                StorageMeasurementConfig(
                    source=source,
                    metric=metric,
                    measurement_type=measurement_type,
                )
                for metric in metrics
            )

    return measurements


def load_estimator_configs(
    config_data: dict[str, Any],
) -> list[EstimatorConfig]:
    configs = []

    for item in config_data.get("estimators", []):
        measurements = [
            EstimatorMeasurementConfig(
                source=measurement["source"],
                metric=measurement["metric"],
            )
            for measurement in item.get("measurements", [])
        ]

        history_size = int(item.get("history_size", 10))

        if history_size <= 0:
            raise ValueError(f"Estimator history_size must be positive, got {history_size}")

        configs.append(
            EstimatorConfig(
                type=item["type"],
                enabled=item.get("enabled", True),
                latency=parse_duration(item.get("latency", "0s")),
                history_size=history_size,
                measurements=measurements,
            )
        )

    return configs


# ---------------------------------------------------------
# Load configuration
# ---------------------------------------------------------
def load_calculator_configs(
    config_data: dict[str, Any],
) -> list[CalculatorConfig]:
    configs = []

    for item in config_data.get("calculators", []):
        calculations = []

        for calculation in item.get("calculations", []):
            inputs = {
                name: CalculatorInputConfig(
                    source=value["source"],
                    metric=value["metric"],
                )
                for name, value in calculation["inputs"].items()
            }

            calculations.append(
                CalculationConfig(
                    source=calculation["source"],
                    metric=calculation["metric"],
                    unit=calculation["unit"],
                    formula=calculation["formula"],
                    inputs=inputs,
                )
            )

        configs.append(
            CalculatorConfig(
                type=item["type"],
                enabled=item.get("enabled", True),
                calculations=calculations,
            )
        )

    return configs


def load_config() -> Config:
    with CONFIG_FILE.open(encoding="utf-8") as file:
        config_data = yaml.safe_load(file)

    return Config(
        collection=CollectionConfig(
            timezone=config_data["collection"]["timezone"],
        ),
        # collectors and databases
        collectors=load_component_configs(config_data, "collectors"),
        databases=load_component_configs(config_data, "databases"),
        # estimator and calculator
        estimators=load_estimator_configs(config_data),
        calculators=load_calculator_configs(config_data),
        # storage
        storage=StorageConfig(
            enabled=config_data.get("storage", {}).get("enabled", True),
            measurements=load_storage_measurements(config_data),
        ),
    )


config_obj = load_config()
