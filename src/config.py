import os
from dataclasses import dataclass

from dotenv import load_dotenv

# ---------------------------------------------------------
# Configuration sources
# ---------------------------------------------------------

# Load general configuration first.
load_dotenv("config.env")

# Load local secrets/configuration without overriding
# values already provided by the environment or config.env.
load_dotenv(".env", override=True)

# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------


def required_config(name: str) -> str:
    """Read a required project configuration value.

    The value is read from the environment. Values from ``config.env``
    are loaded first and values from ``.env`` override them.

    Raises:
        RuntimeError: If the value is missing or empty.
    """
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Required configuration '{name}' is missing. "
            "Please set it in config.env, .env, "
            "or as an environment variable."
        )

    return value


def required_secret(name: str) -> str:
    """Read a required secret.

    The value is read from the environment after ``config.env`` and
    ``.env`` have been loaded.

    Raises:
        RuntimeError: If the secret is missing or empty.
    """
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Required secret '{name}' is missing. " f"Please set it in .env or as an environment variable."
        )

    return value


def required_float(name: str) -> float:
    """Read a required floating-point configuration value.

    Raises:
        RuntimeError: If the value is missing, empty, or not a valid float.
    """
    value = required_config(name)

    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"Configuration '{name}' must be a float, got: {value!r}") from exc


def optional_config(name: str, default: str) -> str:
    """Read optional project configuration."""
    return os.getenv(name) or default


def optional_int(name: str, default: int) -> int:
    """Read an optional integer configuration value."""
    value = optional_config(name, str(default))

    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Configuration '{name}' must be an integer, got: {value!r}") from exc


# ---------------------------------------------------------
# Settings
# ---------------------------------------------------------


@dataclass(frozen=True)
class Config:
    # Secrets
    influxdb_token: str

    # Location
    latitude: float
    longitude: float

    # Required project configuration
    influxdb_url: str
    influxdb_org: str
    influxdb_bucket: str
    fronius_ip: str
    power_meter_grid_ir_device0: str
    power_meter_household_ir_device1: str
    smart_home_box_ip: str
    umweltsensor_9475_device_id: str

    # Optional project configuration
    collection_interval: int


# ---------------------------------------------------------
# Load settings
# ---------------------------------------------------------

config_obj = Config(
    # Secret
    influxdb_token=required_secret("INFLUXDB_TOKEN"),
    # Location
    latitude=required_float("LATITUDE"),
    longitude=required_float("LONGITUDE"),
    # Required project configuration
    influxdb_url=required_config("INFLUXDB_URL"),
    influxdb_org=required_config("INFLUXDB_ORG"),
    influxdb_bucket=required_config("INFLUXDB_BUCKET"),
    fronius_ip=required_config("FRONIUS_INVERTER_IP"),
    power_meter_grid_ir_device0=required_config("POWER_METER_GRID_IR_DEVICE0"),
    power_meter_household_ir_device1=required_config("POWER_METER_HOUSEHOLD_IR_DEVICE1"),
    smart_home_box_ip=required_config("RADEMACHER_SMART_HOME_BOX_IP"),
    umweltsensor_9475_device_id=required_config("RADEMACHER_UMWELTSENSOR_9475_DEVICE_ID"),
    # Optional project configuration
    collection_interval=optional_int(
        "COLLECTION_INTERVAL",
        default=10,
    ),
)
