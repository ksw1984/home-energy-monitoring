"""Night reserve estimation and the intentionally empty forecast seam."""

from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from src.bems.domain import BatteryProfile

from astral import Depression, Observer
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
    today_dusk = dusk(observer, today, Depression.ASTRONOMICAL, tzinfo=timezone)
    tomorrow_dawn = dawn(observer, today + timedelta(days=1), Depression.ASTRONOMICAL, tzinfo=timezone)
    yesterday_dusk = dusk(observer, today - timedelta(days=1), Depression.ASTRONOMICAL, tzinfo=timezone)
    today_dawn = dawn(observer, today, Depression.ASTRONOMICAL, tzinfo=timezone)
    dusk_at = today_dusk - timedelta(minutes=dusk_margin_minutes)
    dawn_at = tomorrow_dawn + timedelta(minutes=dawn_margin_minutes)
    if local_time < today_dawn or local_time >= today_dusk:
        dusk_at = yesterday_dusk - timedelta(minutes=dusk_margin_minutes)
        dawn_at = today_dawn + timedelta(minutes=dawn_margin_minutes)
    remaining_hours = max(0.0, (dawn_at - max(local_time, dusk_at)).total_seconds() / 3600)
    minimum_energy_wh = profile.capacity_wh * profile.min_soc_percent / 100
    baseline_energy_wh = max(0.0, baseline_household_w) * remaining_hours / profile.discharge_efficiency
    return min(profile.capacity_wh * profile.max_soc_percent / 100, minimum_energy_wh + baseline_energy_wh)
