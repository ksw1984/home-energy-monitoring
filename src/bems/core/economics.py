"""Fixed-tariff cost calculations for the household meter cascade."""

from itertools import pairwise

from src.bems.core.domain import BemsPowerSample
from src.config.config import BemsTariffConfig


def _cost_rate_cents_per_hour(
    sample: BemsPowerSample,
    battery_power_w: float,
    tariffs: BemsTariffConfig,
) -> float:
    """Calculate the instantaneous tariff rate in cents per hour."""
    household_power_w = sample.household_w + battery_power_w
    grid_power_w = sample.grid_w + battery_power_w

    household_import_w = max(0.0, household_power_w)
    household_export_w = max(0.0, -household_power_w)
    # Battery discharge offsets the household branch first; only leftover
    # household export can flow upstream to serve the heat pump.
    heat_pump_import_w = max(0.0, sample.heat_pump_w - min(household_export_w, max(0.0, sample.heat_pump_w)))
    grid_export_w = max(0.0, -grid_power_w)

    return (
        household_import_w / 1000.0 * tariffs.household_import_ct_per_kwh
        + heat_pump_import_w / 1000.0 * tariffs.heat_pump_import_ct_per_kwh
        - grid_export_w / 1000.0 * tariffs.feed_in_ct_per_kwh
    )


def interval_cost_cents(
    sample: BemsPowerSample,
    *,
    battery_power_start_w: float,
    battery_power_end_w: float,
    duration_seconds: float,
    tariffs: BemsTariffConfig,
) -> tuple[float, float]:
    """Integrate no-storage and with-storage cost over one held-input interval.

    Grid and household measurements use zero-order hold until the next valid
    sample. Battery power follows a linear ramp between its endpoint values.
    The integration splits where household import/export or grid export changes
    sign, so the piecewise fixed-tariff calculation remains accurate.
    """
    """Return baseline and battery-run cost in cents for one interval."""
    if duration_seconds < 0:
        raise ValueError("duration_seconds cannot be negative")
    baseline_rate = _cost_rate_cents_per_hour(sample, 0.0, tariffs)
    baseline_cost = baseline_rate * duration_seconds / 3600.0
    if duration_seconds == 0:
        return baseline_cost, baseline_cost

    fractions = [0.0, 1.0]
    delta = battery_power_end_w - battery_power_start_w
    if delta != 0:
        thresholds = (-sample.household_w, -sample.grid_w)
        for threshold in thresholds:
            fraction = (threshold - battery_power_start_w) / delta
            if 0.0 < fraction < 1.0:
                fractions.append(fraction)
    fractions.sort()

    battery_cost_rate_integral = 0.0
    for start_fraction, end_fraction in pairwise(fractions):
        start_power = battery_power_start_w + delta * start_fraction
        end_power = battery_power_start_w + delta * end_fraction
        start_rate = _cost_rate_cents_per_hour(sample, start_power, tariffs)
        end_rate = _cost_rate_cents_per_hour(sample, end_power, tariffs)
        segment_fraction = end_fraction - start_fraction
        battery_cost_rate_integral += (start_rate + end_rate) / 2.0 * segment_fraction
    battery_cost = battery_cost_rate_integral * duration_seconds / 3600.0
    return baseline_cost, battery_cost
