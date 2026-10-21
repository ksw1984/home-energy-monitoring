"""Night reserve estimation and the intentionally empty forecast seam."""

from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from src.bems.domain import BatteryProfile

from astral import Observer
from astral.sun import dawn, dusk


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
    """Estimate pack energy to retain for household baseline through the night."""
    local_time = timestamp.astimezone(ZoneInfo(timezone))
    observer = Observer(latitude=latitude, longitude=longitude)
    today = local_time.date()
    today_dusk = dusk(observer, today, 0, tzinfo=timezone)
    tomorrow_dawn = dawn(observer, today + timedelta(days=1), 0, tzinfo=timezone)
    today_dawn = dawn(observer, today, 0, tzinfo=timezone)
    minimum_energy_wh = profile.capacity_wh * profile.min_soc_percent / 100
    dawn_with_margin = today_dawn + timedelta(minutes=dawn_margin_minutes)
    dusk_with_margin = today_dusk - timedelta(minutes=dusk_margin_minutes)

    # During daylight, retain only the configured minimum SOC. Holding the
    # coming night's reserve all day can suppress heat-pump discharge even
    # when morning PV has already returned.
    if dawn_with_margin <= local_time < dusk_with_margin:
        return minimum_energy_wh

    reserve_until = dawn_with_margin if local_time < dawn_with_margin else tomorrow_dawn + timedelta(minutes=dawn_margin_minutes)
    remaining_hours = max(0.0, (reserve_until - local_time).total_seconds() / 3600)
    baseline_energy_wh = max(0.0, baseline_household_w) * remaining_hours / profile.discharge_efficiency
    return min(profile.capacity_wh * profile.max_soc_percent / 100, minimum_energy_wh + baseline_energy_wh)
