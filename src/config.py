import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------
# Configuration sources
# ---------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

# Load local secrets and environment variables.
load_dotenv(BASE_DIR / ".env")

CONFIG_FILE = BASE_DIR / "config.yaml"

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
        raise RuntimeError(
            f"Required secret '{name}' is missing. " "Please set it in .env or as an environment variable."
        )

    return value


# ---------------------------------------------------------
# Settings
# ---------------------------------------------------------


@dataclass(frozen=True)
class ComponentConfig:
    type: str
    enabled: bool
    attributes: dict[str, Any]


@dataclass(frozen=True)
class CollectionConfig:
    interval: int
    timezone: str


@dataclass(frozen=True)
class Config:
    collection: CollectionConfig
    collectors: list[ComponentConfig]
    databases: list[ComponentConfig]


# ---------------------------------------------------------
# Component configuration
# ---------------------------------------------------------


def load_component_configs(
    key: str,
) -> list[ComponentConfig]:
    return [
        ComponentConfig(
            type=item["type"],
            enabled=item.get("enabled", True),
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
)
