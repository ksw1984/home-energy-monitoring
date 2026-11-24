"""Persistence and runtime provider for the measured night baseline."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from src.bems.core.domain import normalize_grid_meter, normalize_power
from src.bems.core.night_baseline import (
    aggregate_household_energy,
    last_completed_night,
    night_date_for,
    night_window_for_evening,
    NightHouseholdEnergy,
    previous_night_dates,
    select_baseline_power_w,
    select_nights,
    weekday_key,
)
from src.shared.measurement import Measurement

if TYPE_CHECKING:
    from src.config.config import BemsConfig
    from src.databases.base_database import BaseDatabase

logger = logging.getLogger(__name__)
QUERY_PAGE_SIZE = 1000
BOOL_TRUE_THRESHOLD = 0.5

NIGHT_METRICS = (
    "night_date_ordinal",
    "night_energy_kwh",
    "night_duration_h",
    "night_avg_power_kw",
    "night_samples",
    "night_valid",
    "night_battery_adjusted",
)


class MeasurementNightBaselineRepository:
    """Store one logical night record as a small set of measurements."""

    def __init__(self, database: BaseDatabase, *, source: str) -> None:
        self.database = database
        self.source = source

    async def store(self, night: NightHouseholdEnergy) -> None:
        """Write a night record idempotently at its window end timestamp."""
        values = {
            "night_date_ordinal": float(night.night_date.toordinal()),
            "night_energy_kwh": night.energy_wh / 1000.0,
            "night_duration_h": night.duration_h,
            "night_avg_power_kw": night.avg_power_w / 1000.0,
            "night_samples": float(night.samples),
            "night_valid": float(night.valid),
            "night_battery_adjusted": float(night.battery_adjusted),
        }
        await self.database.store(
            [Measurement(night.window_end, self.source, metric, value, _night_metric_unit(metric)) for metric, value in values.items()]
        )

    async def load(self, *, start: datetime, stop: datetime) -> dict[date, NightHouseholdEnergy]:
        """Read and reconstruct night records in ``[start, stop)``."""
        rows = await _query_all(
            self.database,
            source=self.source,
            metric_list=list(NIGHT_METRICS),
            start=start,
            stop=stop,
        )
        grouped: dict[datetime, dict[str, float]] = {}
        for row in rows:
            values = grouped.setdefault(row.timestamp, {})
            values[row.metric] = row.value

        nights: dict[date, NightHouseholdEnergy] = {}
        for timestamp, values in grouped.items():
            if not set(NIGHT_METRICS) <= values.keys():
                continue
            night_date = date.fromordinal(int(values["night_date_ordinal"]))
            duration_h = values["night_duration_h"]
            nights[night_date] = NightHouseholdEnergy(
                night_date=night_date,
                window_start=timestamp - timedelta(hours=duration_h),
                window_end=timestamp,
                energy_wh=values["night_energy_kwh"] * 1000.0,
                duration_h=duration_h,
                avg_power_w=values["night_avg_power_kw"] * 1000.0,
                samples=int(values["night_samples"]),
                valid=values["night_valid"] >= BOOL_TRUE_THRESHOLD,
                battery_adjusted=values["night_battery_adjusted"] >= BOOL_TRUE_THRESHOLD,
            )
        return nights


def _night_metric_unit(metric: str) -> str:
    """Return a readable unit for one persisted night metric."""
    return {
        "night_date_ordinal": "ordinal",
        "night_energy_kwh": "kWh",
        "night_duration_h": "h",
        "night_avg_power_kw": "kW",
        "night_samples": "count",
        "night_valid": "bool",
        "night_battery_adjusted": "bool",
    }[metric]


async def _query_all(
    database: BaseDatabase,
    *,
    source: str,
    metric_list: list[str],
    start: datetime,
    stop: datetime,
) -> list[Measurement]:
    """Read every page in a bounded time range."""
    rows: list[Measurement] = []
    cursor: datetime | None = None
    while True:
        page = await database.query_measurements(
            source=source,
            metrics=metric_list,
            start=start if cursor is None else None,
            stop=stop,
            after=cursor,
            limit=QUERY_PAGE_SIZE,
        )
        if not page:
            return rows
        rows.extend(page)
        next_cursor = max(point.timestamp for point in page)
        if cursor is not None and next_cursor <= cursor:
            raise RuntimeError("Night baseline query did not advance its timestamp cursor")
        cursor = next_cursor
        if len(page) < QUERY_PAGE_SIZE:
            return rows


class NightBaselineProvider:
    """Cache stored nights and calculate the reserve power for a target night."""

    def __init__(self, config: BemsConfig, database: BaseDatabase | None) -> None:
        self.config = config
        self.night = config.night
        self.estimation = config.night.baseline_estimation
        self.database = database
        self.repository = MeasurementNightBaselineRepository(database, source=self.estimation.measurement) if database else None
        self._nights: dict[date, NightHouseholdEnergy] = {}
        self._baseline_cache: dict[date, float | None] = {}
        self._loaded_through: date | None = None
        self._last_refreshed_night: date | None = None

    @property
    def enabled(self) -> bool:
        """Return whether measured baseline selection is configured."""
        return self.estimation.enabled and self.repository is not None

    async def refresh_completed_night(self, now: datetime) -> None:
        """Aggregate and persist the newest completed night once."""
        if not self.enabled or self.database is None:
            return
        evening = last_completed_night(
            now,
            latitude=self.night.latitude,
            longitude=self.night.longitude,
            timezone=self.night.timezone,
            dusk_margin_minutes=self.night.dusk_margin_minutes,
            dawn_margin_minutes=self.night.dawn_margin_minutes,
        )
        if evening == self._last_refreshed_night:
            return
        try:
            night = await self.recalculate_night(evening)
            await self.store_night(night)
        except Exception:
            logger.exception("BEMS night baseline update failed for %s", evening)
            return
        self._last_refreshed_night = evening
        logger.info(
            "BEMS night baseline stored for %s: %.1f Wh, %.2f h, %.1f W (valid=%s, battery_adjusted=%s)",
            evening,
            night.energy_wh,
            night.duration_h,
            night.avg_power_w,
            night.valid,
            night.battery_adjusted,
        )

    async def recalculate_night(
        self,
        evening: date,
        *,
        meter_includes_battery: bool | None = None,
    ) -> NightHouseholdEnergy:
        """Aggregate one evening from raw history and refresh cached choices."""
        include_battery = self.estimation.household_meter_includes_battery if meter_includes_battery is None else meter_includes_battery
        night = await self._aggregate_night(evening, meter_includes_battery=include_battery)
        self._nights[evening] = night
        self._invalidate_from(evening)
        return night

    async def store_night(self, night: NightHouseholdEnergy) -> None:
        """Persist one night record through the configured repository."""
        if self.repository is None:
            raise RuntimeError("night baseline repository is not configured")
        await self.repository.store(night)

    async def selection_for(self, timestamp: datetime) -> tuple[date | None, list[NightHouseholdEnergy], float | None]:
        """Return the target night, selected nights, and resulting power."""
        if not self.enabled:
            return None, [], None
        target = night_date_for(
            timestamp,
            latitude=self.night.latitude,
            longitude=self.night.longitude,
            timezone=self.night.timezone,
            dusk_margin_minutes=self.night.dusk_margin_minutes,
            dawn_margin_minutes=self.night.dawn_margin_minutes,
        )
        if target is None:
            return None, [], None
        await self._ensure_loaded(target)
        weekdays = self.estimation.selection.get(weekday_key(target), ())
        selected = select_nights(
            target_night_date=target,
            nights=self._nights,
            weekdays=weekdays,
            max_age_days=self.estimation.max_age_days,
        )
        power_w = select_baseline_power_w(
            target_night_date=target,
            nights=self._nights,
            weekdays=weekdays,
            max_age_days=self.estimation.max_age_days,
            min_nights=self.estimation.min_nights,
            safety_factor=self.estimation.safety_factor,
            min_power_w=self.estimation.min_power_w,
            max_power_w=self.estimation.max_power_w,
        )
        return target, selected, power_w

    async def power_for(
        self,
        timestamp: datetime,
        *,
        recompute: bool = False,
        meter_includes_battery: bool | None = None,
    ) -> float | None:
        """Return the measured baseline power for the night containing ``timestamp``.

        Args:
            timestamp: Time whose active night is evaluated.
            recompute: Aggregate missing previous nights from raw history.
            meter_includes_battery: Override the configured live-meter semantics;
                historical household references are already gross.
        """
        if not self.enabled:
            return None
        target = night_date_for(
            timestamp,
            latitude=self.night.latitude,
            longitude=self.night.longitude,
            timezone=self.night.timezone,
            dusk_margin_minutes=self.night.dusk_margin_minutes,
            dawn_margin_minutes=self.night.dawn_margin_minutes,
        )
        if target is None:
            return None
        if target in self._baseline_cache:
            return self._baseline_cache[target]

        await self._ensure_loaded(target)
        if recompute:
            include_battery = self.estimation.household_meter_includes_battery if meter_includes_battery is None else meter_includes_battery
            for candidate in previous_night_dates(
                target,
                self.estimation.selection.get(weekday_key(target), ()),
                self.estimation.max_age_days,
            ):
                night = self._nights.get(candidate)
                if night is None or not night.valid:
                    try:
                        self._nights[candidate] = await self._aggregate_night(candidate, meter_includes_battery=include_battery)
                    except Exception:
                        logger.exception("BEMS night baseline recompute failed for %s", candidate)

        power_w = select_baseline_power_w(
            target_night_date=target,
            nights=self._nights,
            weekdays=self.estimation.selection.get(weekday_key(target), ()),
            max_age_days=self.estimation.max_age_days,
            min_nights=self.estimation.min_nights,
            safety_factor=self.estimation.safety_factor,
            min_power_w=self.estimation.min_power_w,
            max_power_w=self.estimation.max_power_w,
        )
        self._baseline_cache[target] = power_w
        return power_w

    async def _ensure_loaded(self, target: date) -> None:
        """Load stored nights covering the selection window for ``target``."""
        if self.repository is None:
            return
        if self._loaded_through is not None and target <= self._loaded_through:
            return
        local_start = datetime.combine(
            target - timedelta(days=self.estimation.max_age_days + 1),
            time.min,
            tzinfo=ZoneInfo(self.night.timezone),
        )
        local_stop = datetime.combine(target + timedelta(days=1), time.min, tzinfo=ZoneInfo(self.night.timezone))
        loaded = await self.repository.load(start=local_start, stop=local_stop)
        self._nights.update(loaded)
        self._loaded_through = target

    async def _aggregate_night(self, evening: date, *, meter_includes_battery: bool) -> NightHouseholdEnergy:
        """Read raw household and battery history for one night."""
        if self.database is None:
            raise RuntimeError("night baseline database is not configured")
        window = night_window_for_evening(
            evening,
            latitude=self.night.latitude,
            longitude=self.night.longitude,
            timezone=self.night.timezone,
            dusk_margin_minutes=self.night.dusk_margin_minutes,
            dawn_margin_minutes=self.night.dawn_margin_minutes,
        )
        household_ref = self.config.inputs.household
        household_rows = await _query_all(
            self.database,
            source=household_ref.source,
            metric_list=[household_ref.metric],
            start=window.start,
            stop=window.end,
        )
        household_points = [(row.timestamp, normalize_grid_meter(row)) for row in household_rows]

        battery_points: list[list[tuple[datetime, float]]] = []
        if meter_includes_battery:
            for device in self.config.devices:
                if not device.enabled or device.mode != "live":
                    continue
                rows = await _query_all(
                    self.database,
                    source=f"bems_live_{device.id}",
                    metric_list=["battery_actual_power"],
                    start=window.start - timedelta(seconds=self.estimation.max_gap_s),
                    stop=window.end,
                )
                battery_points.append([(row.timestamp, normalize_power(row)) for row in rows])

        return aggregate_household_energy(
            night_date=evening,
            window_start=window.start,
            window_end=window.end,
            household_points=household_points,
            battery_points=battery_points,
            household_meter_includes_battery=meter_includes_battery,
            max_gap_s=self.estimation.max_gap_s,
            min_valid_ratio=self.estimation.min_valid_ratio,
        )

    def _invalidate_from(self, evening: date) -> None:
        """Drop cached selections for nights affected by a new night record."""
        for target in [key for key in self._baseline_cache if key >= evening]:
            del self._baseline_cache[target]
        if self._loaded_through is not None and evening > self._loaded_through:
            self._loaded_through = None
