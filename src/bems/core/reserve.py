"""Night reserve estimation and the intentionally empty forecast seam."""

from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from src.bems.core.domain import BatteryProfile
from src.bems.core.night_baseline import night_window_for_evening

from astral import Observer
from astral.sun import dawn


class SolarForecastProvider(Protocol):
    """Interface for a future provider of expected PV energy."""

    def expected_pv_energy_kwh(self, timestamp: datetime) -> float:
        """Return expected PV energy from the timestamp until the next sunrise."""


class ZeroSolarForecastProvider:
    """Forecast placeholder: keep forecasts disabled while preserving the seam."""

    def expected_pv_energy_kwh(self, timestamp: datetime) -> float:
        """Return zero until a forecast integration is configured."""
        del timestamp
        return 0.0


def night_reserve_wh(  # noqa: PLR0913 - geographic, schedule, battery and baseline inputs are independent policy settings.
    *,
    timestamp: datetime,
    profile: BatteryProfile,
    baseline_household_w: float,
    latitude: float,
    longitude: float,
    timezone: str,
    dusk_margin_minutes: int = 30,
    dawn_margin_minutes: int = 30,
) -> float:
    """Estimate pack energy to retain for household baseline over the night window."""
    local_time = timestamp.astimezone(ZoneInfo(timezone))
    observer = Observer(latitude=latitude, longitude=longitude)
    today = local_time.date()
    today_dawn = dawn(observer, today, 0, tzinfo=timezone)
    minimum_energy_wh = profile.capacity_wh * profile.min_soc_percent / 100
    dawn_with_margin = today_dawn + timedelta(minutes=dawn_margin_minutes)

    # Use the same dusk/dawn window definition as baseline aggregation so the
    # reserve horizon cannot drift away from the measured night boundaries.
    # Daytime keeps the coming night's full reserve as a build target. During
    # the night window, reserve shrinks with remaining hours so household energy
    # saved below baseline becomes available to the heat pump.
    window_evening = today - timedelta(days=1) if local_time < dawn_with_margin else today
    window = night_window_for_evening(
        window_evening,
        latitude=latitude,
        longitude=longitude,
        timezone=timezone,
        dusk_margin_minutes=dusk_margin_minutes,
        dawn_margin_minutes=dawn_margin_minutes,
    )
    reserve_hours = max(0.0, (window.end - local_time).total_seconds() / 3600) if window.start <= local_time < window.end else window.duration_h
    baseline_energy_wh = max(0.0, baseline_household_w) * reserve_hours / profile.discharge_efficiency
    return min(profile.capacity_wh * profile.max_soc_percent / 100, minimum_energy_wh + baseline_energy_wh)
