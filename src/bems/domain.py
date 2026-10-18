from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Protocol

from src.collectors.definitions.common.measurement import Measurement


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


class Battery(Protocol):
    """Async contract implemented by simulated and future live batteries."""

    async def get_state(self) -> BatteryState:
        """Read a normalized battery state without assuming a vendor API."""
        ...

    async def set_power(self, command: BatteryPowerCommand) -> None:
        """Send one signed AC power target to the battery implementation."""
        ...


def _power_in_w(measurement: Measurement) -> float:
    """Convert supported W/kW measurement units to watts."""
    unit = measurement.unit.strip().lower()
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


def normalize_grid_meter(measurement: Measurement) -> float:
    """Convert the HEMS meter sign (import negative) to BEMS sign (import positive)."""
    return -_power_in_w(measurement)


def normalize_pv(measurement: Measurement) -> float:
    """PV generation is positive in the HEMS measurement convention."""
    return _power_in_w(measurement)


def align_power_samples(
    grid_measurements: list[Measurement],
    household_measurements: list[Measurement],
    pv_measurements: list[Measurement],
    *,
    tolerance_seconds: float,
) -> list[BemsPowerSample]:
    """Align grid-led samples with earlier household/PV values within tolerance."""
    """Align on grid timestamps with the latest earlier meter/PV points.

    No interpolation or look-ahead is performed. Household data is required
    for the cascade split; PV data is retained when a matching point exists,
    but is not added to grid power because the grid meter is already net.
    """
    if tolerance_seconds < 0:
        raise ValueError("tolerance_seconds cannot be negative")

    household = sorted(household_measurements, key=lambda item: _utc(item.timestamp))
    pv = sorted(pv_measurements, key=lambda item: _utc(item.timestamp))

    def latest_prior(
        points: list[Measurement],
        target: datetime,
    ) -> Measurement | None:
        """Return the newest point at/before target, if it is still fresh."""
        match = None
        for point in points:
            point_time = _utc(point.timestamp)
            if point_time > target:
                break
            if (target - point_time).total_seconds() <= tolerance_seconds:
                match = point
        return match

    aligned: list[BemsPowerSample] = []
    for grid_point in sorted(grid_measurements, key=lambda item: _utc(item.timestamp)):
        target = _utc(grid_point.timestamp)
        household_point = latest_prior(household, target)
        if household_point is None:
            continue
        pv_point = latest_prior(pv, target)
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
