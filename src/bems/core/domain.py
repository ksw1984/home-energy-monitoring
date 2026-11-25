from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Protocol

from src.shared.measurement import Measurement


@dataclass(frozen=True)
class BatteryProfile:
    """Battery hardware limits and conversion efficiencies in W/Wh/%.

    Efficiencies are fractions between zero and one and describe AC-to-cell
    charging and cell-to-AC discharging separately.
    """

    id: str
    capacity_wh: float
    min_soc_percent: float
    max_soc_percent: float
    max_charge_power_w: float
    max_discharge_power_w: float
    charge_efficiency: float
    discharge_efficiency: float


@dataclass(frozen=True)
class BatteryState:
    """Normalized point-in-time state; charge and discharge power are nonnegative."""

    timestamp: datetime
    soc_percent: float
    energy_wh: float
    charge_power_w: float
    discharge_power_w: float
    available: bool = True


@dataclass(frozen=True)
class BatteryIdentity:
    """Identity reported by a live device during its compatibility preflight."""

    model: str
    firmware_version: str


@dataclass(frozen=True)
class BatteryPowerCommand:
    """Timestamped signed AC setpoint: positive charges, negative discharges."""

    timestamp: datetime
    power_w: float


@dataclass(frozen=True)
class BemsPowerSample:
    """Aligned cascade readings using import-positive power values in watts."""

    timestamp: datetime
    grid_w: float
    household_w: float
    heat_pump_w: float
    pv_w: float | None = None


def estimate_power_without_battery(measured_power_w: float, state: BatteryState) -> float:
    """Recover underlying import-positive load using the battery's actual AC power.

    A grid meter includes battery charging as extra import and battery
    discharging as reduced import. Requested power is deliberately not used:
    ramping, limits, and device regulation can make it differ from delivery.
    """
    actual_battery_power_w = state.charge_power_w - state.discharge_power_w
    return measured_power_w - actual_battery_power_w


class Battery(Protocol):
    """Async contract implemented by simulated and future live batteries."""

    async def get_state(self) -> BatteryState:
        """Read a normalized battery state without assuming a vendor API."""
        ...

    async def get_identity(self) -> BatteryIdentity:
        """Read model and firmware identity; live control requires this check."""
        ...

    async def set_power(self, command: BatteryPowerCommand) -> None:
        """Send one signed AC power target to the battery implementation."""
        ...


def _power_in_w(measurement: Measurement) -> float:
    """Convert supported W/kW units to watts, inferring legacy DLMS units.

    Older InfluxDB points may not contain the ``unit`` field. The configured
    DLMS active-power metric is stored in kW, while the Fronius ``pv_power``
    metric is stored in W.
    """
    unit = measurement.unit.strip().lower()
    if (
        not unit
        and measurement.metric == "grid_active_power"
        and measurement.source
        in {
            "meter_grid_dlms",
            "meter_household_dlms",
        }
    ):
        unit = "kw"
    elif not unit and measurement.source == "inverter_fronius" and measurement.metric == "pv_power":
        unit = "w"
    if unit == "w":
        return measurement.value
    if unit == "kw":
        return measurement.value * 1000.0
    raise ValueError(f"Unsupported power unit '{measurement.unit}' for {measurement.source}/{measurement.metric}")


def _utc(timestamp: datetime) -> datetime:
    """Validate an aware timestamp and normalize it to UTC for comparison."""
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("BEMS power measurements must have timezone-aware timestamps")
    return timestamp.astimezone(UTC)


def normalize_power(measurement: Measurement) -> float:
    """Convert a supported W/kW power measurement to watts."""
    return _power_in_w(measurement)


def normalize_grid_meter(measurement: Measurement) -> float:
    """Convert the HEMS meter to watts; its sign already matches BEMS convention."""
    # HEMS DLMS measurements use import-positive and export-negative values,
    # which is also the convention used internally by BEMS.
    return _power_in_w(measurement)


def normalize_pv(measurement: Measurement) -> float:
    """PV generation is positive in the HEMS measurement convention."""
    return _power_in_w(measurement)


def align_power_samples(
    grid_measurements: list[Measurement],
    household_measurements: list[Measurement],
    pv_measurements: list[Measurement],
    *,
    tolerance_seconds: float,
    lookahead_seconds: float = 0.0,
) -> list[BemsPowerSample]:
    """Align on grid timestamps with the closest meter/PV points.

    No interpolation is performed. Household data is required for the cascade
    split; PV data is retained when a matching point exists, but is not added
    to grid power because the grid meter is already net.

    Points older than ``tolerance_seconds`` are ignored. ``lookahead_seconds``
    extends the accepted window into the future by that amount. Live bus
    samples are collected by independent tasks, so the household reading can be
    a few milliseconds newer than the grid reading for the same sample instant.
    Historical replay keeps the default of zero and therefore never looks ahead.
    """
    if tolerance_seconds < 0:
        raise ValueError("tolerance_seconds cannot be negative")
    if lookahead_seconds < 0:
        raise ValueError("lookahead_seconds cannot be negative")

    household = sorted(household_measurements, key=lambda item: _utc(item.timestamp))
    pv = sorted(pv_measurements, key=lambda item: _utc(item.timestamp))

    def closest(
        points: list[Measurement],
        target: datetime,
    ) -> Measurement | None:
        """Return the point nearest target inside the accepted window."""
        match = None
        match_distance = None
        for point in points:
            point_time = _utc(point.timestamp)
            delta = (point_time - target).total_seconds()
            if delta > lookahead_seconds:
                break
            if delta < -tolerance_seconds:
                continue
            distance = abs(delta)
            if match_distance is None or distance < match_distance:
                match = point
                match_distance = distance
        return match

    aligned: list[BemsPowerSample] = []
    for grid_point in sorted(grid_measurements, key=lambda item: _utc(item.timestamp)):
        target = _utc(grid_point.timestamp)
        household_point = closest(household, target)
        if household_point is None:
            continue
        pv_point = closest(pv, target)
        grid_w = normalize_grid_meter(grid_point)
        household_w = normalize_grid_meter(household_point)
        aligned.append(
            BemsPowerSample(
                timestamp=grid_point.timestamp,
                grid_w=grid_w,
                household_w=household_w,
                heat_pump_w=grid_w - household_w,
                pv_w=normalize_pv(pv_point) if pv_point is not None else None,
            )
        )
    return aligned
