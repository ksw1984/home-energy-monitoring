import os
from dataclasses import dataclass

from dotenv import load_dotenv

# ---------------------------------------------------------
# Configuration sources
# ---------------------------------------------------------

# Load general configuration
load_dotenv("config.env")

# Load secrets and override
load_dotenv(".env", override=True)

# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------


def required_config(name: str) -> str:
    """Read a required project configuration value.

    Resolution order:
    1. Real environment variable
    2. config.env

    Raises:
        RuntimeError: if the value is missing or empty.
    """
    value = os.getenv(name) or config_env.get(name)

    if not value:
        raise RuntimeError(
            f"Required configuration '{name}' is missing. "
            f"Please set it in config.env or as an environment variable."
        )

    return value


def required_secret(name: str) -> str:
    """Read a required secret.

    Resolution order:
    1. Real environment variable
    2. .env

    Raises:
        RuntimeError: if the secret is missing or empty.
    """
    value = os.getenv(name) or secret_env.get(name)

    if not value:
        raise RuntimeError(
            f"Required secret '{name}' is missing. "
            f"Please set it in .env or as an environment variable."
        )

    return value


def optional_config(name: str, default: str) -> str:
    """Read optional project configuration."""
    return os.getenv(name) or config_env.get(name) or default


def optional_int(name: str, default: int) -> int:
    """Read an optional integer configuration value."""
    value = optional_config(name, str(default))

    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(
            f"Configuration '{name}' must be an integer, got: {value!r}"
        ) from exc


# ---------------------------------------------------------
# Settings
# ---------------------------------------------------------


@dataclass(frozen=True)
class Config:
    # Secrets
    influxdb_token: str

    # Required project configuration
    influxdb_url: str
    influxdb_org: str
    influxdb_bucket: str
    fronius_ip: str
    ir_device0: str

    # Optional project configuration
    collection_interval: int


# ---------------------------------------------------------
# Load settings
# ---------------------------------------------------------

config_obj = Config(
    # Secret
    influxdb_token=required_secret("INFLUXDB_TOKEN"),
    # Required project configuration
    influxdb_url=required_config("INFLUXDB_URL"),
    influxdb_org=required_config("INFLUXDB_ORG"),
    influxdb_bucket=required_config("INFLUXDB_BUCKET"),
    fronius_ip=required_config("FRONIUS_IP"),
    ir_device0=required_config("IR_DEVICE0"),
    # Optional project configuration
    collection_interval=optional_int(
        "COLLECTION_INTERVAL",
        default=10,
    ),
)
