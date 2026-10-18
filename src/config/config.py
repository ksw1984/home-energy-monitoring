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
SOC_PERCENT_MAX = 100.0

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
class EstimatorConfig:
    type: str
    enabled: bool
    lookback: int


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
class BemsMeasurementRef:
    source: str
    metric: str


@dataclass(frozen=True)
class BemsInputsConfig:
    grid: BemsMeasurementRef
    household: BemsMeasurementRef
    pv: BemsMeasurementRef


@dataclass(frozen=True)
class BemsTariffConfig:
    feed_in_ct_per_kwh: float
    heat_pump_import_ct_per_kwh: float
    household_import_ct_per_kwh: float


@dataclass(frozen=True)
class BemsControlConfig:
    deadband_w: float
    max_output_power_w: float
    max_measurement_age_s: float
    history_alignment_tolerance_s: float
    simulation_ramp_seconds: float


@dataclass(frozen=True)
class BatteryProfileConfig:
    name: str
    capacity_wh: float
    min_soc_percent: float
    max_soc_percent: float
    max_charge_power_w: float
    max_discharge_power_w: float
    charge_efficiency: float
    discharge_efficiency: float


@dataclass(frozen=True)
class BemsDeviceConfig:
    id: str
    profile: str
    mode: str
    enabled: bool
    initial_soc_percent: float
    max_output_power_w: float
    attributes: dict[str, Any]


@dataclass(frozen=True)
class BemsConfig:
    enabled: bool
    history_database: str
    inputs: BemsInputsConfig
    tariffs: BemsTariffConfig
    control: BemsControlConfig
    profiles: dict[str, BatteryProfileConfig]
    devices: list[BemsDeviceConfig]


@dataclass(frozen=True)
class Config:
    collection: CollectionConfig
    collectors: list[ComponentConfig]
    databases: list[ComponentConfig]
    estimators: list[EstimatorConfig]
    calculators: list[CalculatorConfig]
    storage: StorageConfig
    bems: BemsConfig | None = None


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
        lookback = int(item.get("lookback", 1))

        if lookback <= 0:
            raise ValueError(f"Estimator lookback must be positive, got {lookback}")

        configs.append(
            EstimatorConfig(
                type=item["type"],
                enabled=item.get("enabled", True),
                lookback=lookback,
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


def _load_bems_config(config_data: dict[str, Any]) -> BemsConfig:  # noqa: C901, PLR0912 - field-specific validation errors stay beside their checks.
    raw = config_data.get("bems", {})
    raw_inputs = raw.get("inputs", {})
    raw_control = raw.get("control", {})
    raw_tariffs = raw.get("tariffs", {})

    def measurement_ref(name: str, default_source: str, default_metric: str) -> BemsMeasurementRef:
        item = raw_inputs.get(name, {})
        return BemsMeasurementRef(
            source=str(item.get("source", default_source)),
            metric=str(item.get("metric", default_metric)),
        )

    profiles: dict[str, BatteryProfileConfig] = {}
    for profile_id, item in raw.get("profiles", {}).items():
        profile = BatteryProfileConfig(
            name=str(item.get("name", profile_id)),
            capacity_wh=float(item["capacity_wh"]),
            min_soc_percent=float(item.get("min_soc_percent", 10.0)),
            max_soc_percent=float(item.get("max_soc_percent", 100.0)),
            max_charge_power_w=float(item["max_charge_power_w"]),
            max_discharge_power_w=float(item["max_discharge_power_w"]),
            charge_efficiency=float(item["charge_efficiency"]),
            discharge_efficiency=float(item["discharge_efficiency"]),
        )
        if profile.capacity_wh <= 0:
            raise ValueError(f"BEMS profile '{profile_id}' capacity_wh must be positive")
        if not 0 <= profile.min_soc_percent < profile.max_soc_percent <= SOC_PERCENT_MAX:
            raise ValueError(f"BEMS profile '{profile_id}' SOC limits must satisfy 0 <= min < max <= 100")
        if profile.max_charge_power_w <= 0 or profile.max_discharge_power_w <= 0:
            raise ValueError(f"BEMS profile '{profile_id}' charge and discharge limits must be positive")
        if not 0 < profile.charge_efficiency <= 1 or not 0 < profile.discharge_efficiency <= 1:
            raise ValueError(f"BEMS profile '{profile_id}' efficiencies must be in (0, 1]")
        profiles[profile_id] = profile

    devices = [
        BemsDeviceConfig(
            id=str(item["id"]),
            profile=str(item["profile"]),
            mode=str(item.get("mode", "simulation")),
            enabled=bool(item.get("enabled", True)),
            initial_soc_percent=float(item.get("initial_soc_percent", 50.0)),
            max_output_power_w=float(item.get("max_output_power_w", 800.0)),
            attributes=item.get("attributes", {}),
        )
        for item in raw.get("devices", [])
    ]
    device_ids = [device.id for device in devices]
    if len(device_ids) != len(set(device_ids)):
        raise ValueError("BEMS device IDs must be unique")
    for device in devices:
        if device.profile not in profiles:
            raise ValueError(f"BEMS device '{device.id}' references unknown profile '{device.profile}'")
        if device.mode not in {"simulation", "live"}:
            raise ValueError(f"BEMS device '{device.id}' mode must be 'simulation' or 'live'")
        if not 0 <= device.initial_soc_percent <= SOC_PERCENT_MAX:
            raise ValueError(f"BEMS device '{device.id}' initial_soc_percent must be between 0 and 100")
        profile = profiles[device.profile]
        if not profile.min_soc_percent <= device.initial_soc_percent <= profile.max_soc_percent:
            raise ValueError(f"BEMS device '{device.id}' initial SOC must be within its profile's SOC limits")
        if device.max_output_power_w < 0:
            raise ValueError(f"BEMS device '{device.id}' max_output_power_w cannot be negative")

    control = BemsControlConfig(
        deadband_w=float(raw_control.get("deadband_w", 20.0)),
        max_output_power_w=float(raw_control.get("max_output_power_w", 800.0)),
        max_measurement_age_s=float(raw_control.get("max_measurement_age_s", 20.0)),
        history_alignment_tolerance_s=float(raw_control.get("history_alignment_tolerance_s", 10.0)),
        simulation_ramp_seconds=float(raw_control.get("simulation_ramp_seconds", 10.0)),
    )
    if control.deadband_w < 0 or control.max_output_power_w < 0:
        raise ValueError("BEMS deadband and output power limit cannot be negative")
    if min(control.max_measurement_age_s, control.history_alignment_tolerance_s, control.simulation_ramp_seconds) < 0:
        raise ValueError("BEMS time settings cannot be negative")

    database_types = [item["type"] for item in config_data.get("databases", [])]
    default_history_database = "influxdb" if "influxdb" in database_types else (database_types[0] if database_types else "")
    history_database = str(raw.get("history_database", default_history_database))
    if raw and history_database not in database_types:
        raise ValueError(f"BEMS history_database '{history_database}' is not configured under databases")

    return BemsConfig(
        enabled=bool(raw.get("enabled", bool(raw))),
        history_database=history_database,
        inputs=BemsInputsConfig(
            grid=measurement_ref("grid", "meter_grid_dlms", "grid_active_power"),
            household=measurement_ref("household", "meter_household_dlms", "grid_active_power"),
            pv=measurement_ref("pv", "inverter_fronius", "pv_power"),
        ),
        tariffs=BemsTariffConfig(
            feed_in_ct_per_kwh=float(raw_tariffs.get("feed_in_ct_per_kwh", 7.8)),
            heat_pump_import_ct_per_kwh=float(raw_tariffs.get("heat_pump_import_ct_per_kwh", 22.0)),
            household_import_ct_per_kwh=float(raw_tariffs.get("household_import_ct_per_kwh", 30.0)),
        ),
        control=control,
        profiles=profiles,
        devices=devices,
    )


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
        bems=_load_bems_config(config_data),
    )


config_obj = load_config()
