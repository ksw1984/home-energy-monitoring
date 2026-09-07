import os
from dataclasses import dataclass
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

with CONFIG_FILE.open(encoding="utf-8") as file:
    config_data = yaml.safe_load(file)


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
    interval: int
    timezone: str


@dataclass(frozen=True)
class StorageMeasurementConfig:
    source: str
    metric: str
    measurement_type: str = "current"


@dataclass(frozen=True)
class StorageConfig:
    enabled: bool
    measurements: list[StorageMeasurementConfig]


@dataclass(frozen=True)
class Config:
    collection: CollectionConfig
    collectors: list[ComponentConfig]
    databases: list[ComponentConfig]
    storage: StorageConfig


# ---------------------------------------------------------
# Component configuration
# ---------------------------------------------------------


def load_storage_measurements() -> list[StorageMeasurementConfig]:
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


def load_component_configs(
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


# ---------------------------------------------------------
# Load configuration
# ---------------------------------------------------------

config_obj = Config(
    collection=CollectionConfig(
        interval=int(config_data["collection"]["interval"]),
        timezone=config_data["collection"]["timezone"],
    ),
    collectors=load_component_configs("collectors"),
    databases=load_component_configs("databases"),
    storage=StorageConfig(
        enabled=config_data.get("storage", {}).get("enabled", True),
        measurements=load_storage_measurements(),
    ),
)
