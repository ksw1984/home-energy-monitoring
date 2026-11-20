import asyncio
from dataclasses import replace
from datetime import date, datetime, time, timedelta, UTC
from zoneinfo import ZoneInfo

from src.bems.core.night_baseline import (
    aggregate_household_energy,
    last_completed_night,
    night_date_for,
    night_window_for_evening,
    NightHouseholdEnergy,
    previous_night_dates,
    select_baseline_power_w,
    weekday_key,
)
from src.bems.services.night_baseline_service import MeasurementNightBaselineRepository, NightBaselineProvider
from src.collectors.definitions.common.measurement import Measurement
from src.config.config import _load_night_baseline_estimation, config_obj
from src.databases.base_database import BaseDatabase

import pytest


class MemoryDatabase(BaseDatabase):
    """Query-capable in-memory backend with a call counter for cache tests."""

    def __init__(self, measurements: list[Measurement] | None = None) -> None:
        super().__init__(type="memory", enabled=True)
        self.measurements = measurements or []
        self.writes: list[Measurement] = []
        self.queries = 0

    async def store(self, measurements: list[Measurement]) -> None:
        self.writes.extend(measurements)
        self.measurements.extend(measurements)

    async def query_measurements(  # noqa: PLR0913 - implements the shared database query contract.
        self,
        *,
        source: str,
        metrics: list[str],
        start: datetime | None = None,
        stop: datetime | None = None,
        after: datetime | None = None,
        limit: int = 1000,
    ) -> list[Measurement]:
        self.queries += 1
        return sorted(
            [
                point
                for point in self.measurements
                if point.source == source
                and point.metric in metrics
                and (start is None or point.timestamp >= start)
                and (stop is None or point.timestamp < stop)
                and (after is None or point.timestamp > after)
            ],
            key=lambda point: point.timestamp,
        )[:limit]


def night_record(day: date, power_w: float, hours: float = 10.0, *, valid: bool = True) -> NightHouseholdEnergy:
    """Build a deterministic night record with known energy and duration."""
    start = datetime.combine(day, time(18), tzinfo=UTC)
    return NightHouseholdEnergy(
        night_date=day,
        window_start=start,
        window_end=start + timedelta(hours=hours),
        energy_wh=power_w * hours,
        duration_h=hours,
        avg_power_w=power_w,
        samples=60,
        valid=valid,
        battery_adjusted=False,
    )


def test_aggregate_household_energy_adds_discharge_back_to_net_meter():
    start = datetime(2026, 11, 17, 18, tzinfo=UTC)
    end = start + timedelta(hours=2)
    timestamps = [start + timedelta(minutes=2 * index) for index in range(60)]
    household_points = [(timestamp, 10.0) for timestamp in timestamps]
    battery_points = [[(timestamp, -190.0) for timestamp in timestamps]]
    covered_h = 118 / 60

    night = aggregate_household_energy(
        night_date=start.date(),
        window_start=start,
        window_end=end,
        household_points=household_points,
        battery_points=battery_points,
        household_meter_includes_battery=True,
        max_gap_s=120,
        min_valid_ratio=0.9,
    )

    assert night.valid is True
    assert night.battery_adjusted is True
    assert night.samples == 60
    assert night.energy_wh == pytest.approx(200 * covered_h)
    assert night.avg_power_w == pytest.approx(200 * covered_h / 2)


def test_aggregate_household_energy_rejects_missing_battery_series():
    start = datetime(2026, 11, 17, 18, tzinfo=UTC)
    household_points = [(start + timedelta(minutes=2 * index), 200.0) for index in range(60)]

    night = aggregate_household_energy(
        night_date=start.date(),
        window_start=start,
        window_end=start + timedelta(hours=2),
        household_points=household_points,
        household_meter_includes_battery=True,
        max_gap_s=120,
        min_valid_ratio=0.9,
    )

    assert night.valid is False
    assert night.energy_wh == 0.0
    assert night.avg_power_w == 0.0


def test_aggregate_household_energy_keeps_gross_history_unchanged():
    start = datetime(2026, 11, 17, 18, tzinfo=UTC)
    household_points = [(start + timedelta(minutes=2 * index), 200.0) for index in range(60)]

    night = aggregate_household_energy(
        night_date=start.date(),
        window_start=start,
        window_end=start + timedelta(hours=2),
        household_points=household_points,
        household_meter_includes_battery=False,
        max_gap_s=120,
        min_valid_ratio=0.9,
    )

    assert night.valid is True
    assert night.battery_adjusted is False
    assert night.energy_wh == pytest.approx(200 * 118 / 60)


def test_select_baseline_uses_latest_valid_weekday_and_weights_by_duration():
    target = date(2026, 11, 18)  # Wednesday
    nights = {
        date(2026, 11, 11): night_record(date(2026, 11, 11), 200, 10),  # Wed
        date(2026, 11, 12): night_record(date(2026, 11, 12), 300, 8),  # Thu
        date(2026, 11, 13): night_record(date(2026, 11, 13), 400, 10),  # Fri
        date(2026, 11, 16): night_record(date(2026, 11, 16), 500, 12),  # Mon
        date(2026, 11, 17): night_record(date(2026, 11, 17), 9999, 10, valid=False),  # invalid Tue
        date(2026, 11, 10): night_record(date(2026, 11, 10), 100, 10),  # older Tue fallback
        date(2026, 11, 24): night_record(date(2026, 11, 24), 9999, 10),  # future Tue must be ignored
    }

    result = select_baseline_power_w(
        target_night_date=target,
        nights=nights,
        weekdays=("Wed", "Thu", "Fri", "Mon", "Tue"),
        max_age_days=28,
        min_nights=5,
        safety_factor=1.15,
        min_power_w=100,
        max_power_w=2000,
    )

    # (2000 + 2400 + 4000 + 6000 + 1000) Wh / (10 + 8 + 10 + 12 + 10) h * 1.15
    assert result == pytest.approx(15400 / 50 * 1.15)


def test_friday_mask_selects_only_previous_friday_saturday_and_sunday():
    target = date(2026, 11, 20)  # Friday

    candidates = previous_night_dates(target, ("Fri", "Sat", "Sun"), max_age_days=7)

    assert candidates == [date(2026, 11, 13), date(2026, 11, 14), date(2026, 11, 15)]


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (date(2026, 11, 16), [date(2026, 11, d) for d in (9, 10, 11, 12, 13)]),
        (date(2026, 11, 17), [date(2026, 11, d) for d in (10, 11, 12, 13, 16)]),
        (date(2026, 11, 18), [date(2026, 11, d) for d in (11, 12, 13, 16, 17)]),
        (date(2026, 11, 19), [date(2026, 11, d) for d in (12, 13, 16, 17, 18)]),
        (date(2026, 11, 20), [date(2026, 11, d) for d in (13, 14, 15)]),
        (date(2026, 11, 21), [date(2026, 11, d) for d in (14, 15, 20)]),
        (date(2026, 11, 22), [date(2026, 11, d) for d in (15, 20, 21)]),
    ],
)
def test_all_weekday_masks_select_only_previous_nights(target: date, expected: list[date]):
    assert config_obj.bems is not None
    estimation = config_obj.bems.night.baseline_estimation
    mask = estimation.selection[weekday_key(target)]

    assert previous_night_dates(target, mask, max_age_days=7) == expected
    assert all(candidate < target for candidate in expected)


def test_select_baseline_returns_none_when_too_few_nights_are_valid():
    target = date(2026, 11, 18)
    nights = {
        date(2026, 11, 17): night_record(date(2026, 11, 17), 250, 10),
        date(2026, 11, 13): night_record(date(2026, 11, 13), 9999, 10, valid=False),
    }

    result = select_baseline_power_w(
        target_night_date=target,
        nights=nights,
        weekdays=("Wed", "Thu", "Fri", "Mon", "Tue"),
        max_age_days=28,
        min_nights=2,
        safety_factor=1.15,
        min_power_w=100,
        max_power_w=2000,
    )

    assert result is None


def test_select_baseline_clamps_safety_factor_to_configured_limits():
    nights = {date(2026, 11, 17): night_record(date(2026, 11, 17), 500, 10)}
    kwargs = {
        "target_night_date": date(2026, 11, 18),
        "nights": nights,
        "weekdays": ("Tue",),
        "max_age_days": 28,
        "min_nights": 1,
        "min_power_w": 100,
        "max_power_w": 800,
    }

    assert select_baseline_power_w(**kwargs, safety_factor=2.0) == 800
    assert select_baseline_power_w(**kwargs, safety_factor=0.1) == 100


def test_night_date_and_last_completed_night_follow_the_shared_window():
    schedule = {
        "latitude": 52.4,
        "longitude": 13.7,
        "timezone": "Europe/Berlin",
        "dusk_margin_minutes": 30,
        "dawn_margin_minutes": 50,
    }
    evening = date(2026, 11, 19)
    window = night_window_for_evening(evening, **schedule)
    during_night = window.start + timedelta(hours=1)
    after_night = window.end + timedelta(minutes=1)

    assert night_date_for(during_night, **schedule) == evening
    assert last_completed_night(during_night, **schedule) == evening - timedelta(days=1)
    assert night_date_for(after_night, **schedule) is None
    assert last_completed_night(after_night, **schedule) == evening


def test_provider_uses_stored_nights_ignores_future_and_caches_result():
    assert config_obj.bems is not None
    config = replace(
        config_obj.bems,
        night=replace(
            config_obj.bems.night,
            baseline_estimation=replace(
                config_obj.bems.night.baseline_estimation,
                enabled=True,
                measurement="test_night_household",
                safety_factor=1.0,
                min_nights=2,
            ),
        ),
    )
    database = MemoryDatabase()
    repository = MeasurementNightBaselineRepository(database, source="test_night_household")
    for day, power_w in ((date(2026, 11, 11), 200.0), (date(2026, 11, 17), 600.0)):
        asyncio.run(repository.store(night_record(day, power_w)))
    asyncio.run(repository.store(night_record(date(2026, 11, 25), 9999.0)))

    provider = NightBaselineProvider(config, database)
    timestamp = datetime(2026, 11, 18, 23, tzinfo=ZoneInfo("Europe/Berlin"))

    assert asyncio.run(provider.power_for(timestamp)) == pytest.approx(400.0)
    queries_after_first_call = database.queries
    assert asyncio.run(provider.power_for(timestamp)) == pytest.approx(400.0)
    assert database.queries == queries_after_first_call


def test_recalculate_night_can_override_live_meter_semantics_for_history():
    assert config_obj.bems is not None
    config = replace(config_obj.bems)
    config = replace(
        config,
        night=replace(
            config.night,
            baseline_estimation=replace(config.night.baseline_estimation, enabled=True, household_meter_includes_battery=True),
        ),
    )
    evening = date(2026, 11, 18)
    window = night_window_for_evening(
        evening,
        latitude=config.night.latitude,
        longitude=config.night.longitude,
        timezone=config.night.timezone,
        dusk_margin_minutes=config.night.dusk_margin_minutes,
        dawn_margin_minutes=config.night.dawn_margin_minutes,
    )
    household = config.inputs.household
    database = MemoryDatabase(
        [
            Measurement(
                window.start + timedelta(minutes=2 * index),
                household.source,
                household.metric,
                300.0,
                "W",
            )
            for index in range(500)
        ]
    )
    provider = NightBaselineProvider(config, database)

    night = asyncio.run(provider.recalculate_night(evening, meter_includes_battery=False))

    assert night.valid is True
    assert night.battery_adjusted is False
    assert night.avg_power_w == pytest.approx(300.0, rel=0.02)


def test_disabled_baseline_never_queries_the_database():
    assert config_obj.bems is not None
    config = replace(
        config_obj.bems,
        night=replace(
            config_obj.bems.night,
            baseline_estimation=replace(config_obj.bems.night.baseline_estimation, enabled=False),
        ),
    )
    database = MemoryDatabase()
    provider = NightBaselineProvider(config, database)
    timestamp = datetime(2026, 11, 18, 23, tzinfo=ZoneInfo("Europe/Berlin"))

    assert asyncio.run(provider.power_for(timestamp)) is None
    assert database.queries == 0


def test_baseline_estimation_config_defaults_are_safe():
    estimation = _load_night_baseline_estimation({})

    assert estimation.enabled is False
    assert estimation.measurement == "bems_night_household"
    assert estimation.safety_factor == 1.15
    assert estimation.selection["Wed"] == ("Wed", "Thu", "Fri", "Mon", "Tue")
