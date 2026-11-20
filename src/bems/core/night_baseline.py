"""Measured household night baseline and weekday selection.

The baseline is one average household power value per completed night. It is
derived from meter history, stored as one logical night record, and selected by
a configurable weekday mask. All functions in this module are pure so the
selection and aggregation rules can be tested without InfluxDB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from astral import Observer
from astral.sun import dawn, dusk

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MIN_AGGREGATION_POINTS = 2


@dataclass(frozen=True)
class NightWindow:
    """Local evening date and the dusk-to-dawn measurement window."""

    night_date: date
    start: datetime
    end: datetime

    @property
    def duration_h(self) -> float:
        """Return the window duration from real timestamps, including DST."""
        return max(0.0, (self.end - self.start).total_seconds() / 3600)


@dataclass(frozen=True)
class NightHouseholdEnergy:
    """Aggregated gross household energy for one completed night."""

    night_date: date
    window_start: datetime
    window_end: datetime
    energy_wh: float
    duration_h: float
    avg_power_w: float
    samples: int
    valid: bool
    battery_adjusted: bool

    @property
    def weekday(self) -> str:
        """Return the short weekday key used by the selection mask."""
        return WEEKDAYS[self.night_date.weekday()]


def weekday_key(value: date) -> str:
    """Return the short weekday key for a date."""
    return WEEKDAYS[value.weekday()]


def night_window_for_evening(  # noqa: PLR0913 - location and both margins are independent schedule inputs.
    evening: date,
    *,
    latitude: float,
    longitude: float,
    timezone: str,
    dusk_margin_minutes: int,
    dawn_margin_minutes: int,
) -> NightWindow:
    """Build the dusk-to-dawn window belonging to one local evening date."""
    observer = Observer(latitude=latitude, longitude=longitude)
    start = dusk(observer, evening, 0, tzinfo=timezone) - timedelta(minutes=dusk_margin_minutes)
    end = dawn(observer, evening + timedelta(days=1), 0, tzinfo=timezone) + timedelta(minutes=dawn_margin_minutes)
    return NightWindow(night_date=evening, start=start, end=end)


def night_date_for(  # noqa: PLR0913 - location and both margins are independent schedule inputs.
    timestamp: datetime,
    *,
    latitude: float,
    longitude: float,
    timezone: str,
    dusk_margin_minutes: int,
    dawn_margin_minutes: int,
) -> date | None:
    """Return the evening date of the active night, or ``None`` during daylight."""
    local_time = timestamp.astimezone(ZoneInfo(timezone))
    observer = Observer(latitude=latitude, longitude=longitude)
    today = local_time.date()
    today_dawn = dawn(observer, today, 0, tzinfo=timezone) + timedelta(minutes=dawn_margin_minutes)
    today_dusk = dusk(observer, today, 0, tzinfo=timezone) - timedelta(minutes=dusk_margin_minutes)

    if local_time < today_dawn:
        return today - timedelta(days=1)
    if local_time >= today_dusk:
        return today
    return None


def last_completed_night(  # noqa: PLR0913 - location and both margins are independent schedule inputs.
    now: datetime,
    *,
    latitude: float,
    longitude: float,
    timezone: str,
    dusk_margin_minutes: int,
    dawn_margin_minutes: int,
) -> date:
    """Return the evening date of the newest night whose window has ended."""
    local_date = now.astimezone(ZoneInfo(timezone)).date()
    for days_back in range(3):
        evening = local_date - timedelta(days=days_back)
        window = night_window_for_evening(
            evening,
            latitude=latitude,
            longitude=longitude,
            timezone=timezone,
            dusk_margin_minutes=dusk_margin_minutes,
            dawn_margin_minutes=dawn_margin_minutes,
        )
        if window.end <= now:
            return evening
    # Three days without a completed dawn is not realistic, but the oldest
    # candidate keeps the caller deterministic for polar or bad configurations.
    return local_date - timedelta(days=3)


def _latest_prior(points: list[tuple[datetime, float]], target: datetime, tolerance_s: float) -> float | None:
    """Return the newest value at/before ``target`` within the tolerance."""
    match: float | None = None
    for timestamp, value in points:
        if timestamp > target:
            break
        if (target - timestamp).total_seconds() <= tolerance_s:
            match = value
    return match


def aggregate_household_energy(  # noqa: PLR0913 - each meter and validity input is an explicit aggregation knob.
    *,
    night_date: date,
    window_start: datetime,
    window_end: datetime,
    household_points: list[tuple[datetime, float]],
    battery_points: list[list[tuple[datetime, float]]] | None = None,
    household_meter_includes_battery: bool,
    max_gap_s: float,
    min_valid_ratio: float,
    battery_tolerance_s: float = 120.0,
) -> NightHouseholdEnergy:
    """Integrate gross household power over one night window.

    Args:
        night_date: Local evening date of the night.
        window_start: Inclusive dusk boundary.
        window_end: Exclusive dawn boundary.
        household_points: Meter readings as ``(timestamp, watts)``.
        battery_points: One sorted list per battery, signed AC power (charge
            positive, discharge negative).
        household_meter_includes_battery: Subtract battery power from the meter
            to recover gross household load.
        max_gap_s: Gaps above this duration are not integrated.
        min_valid_ratio: Minimum integrated time divided by window duration.
        battery_tolerance_s: Maximum age of a battery reading aligned to a
            household sample.

    Returns:
        A night record. Invalid nights have zero energy and average power.
    """
    duration_h = max(0.0, (window_end - window_start).total_seconds() / 3600)
    points = sorted((timestamp, value) for timestamp, value in household_points if window_start <= timestamp < window_end)
    battery_series = [sorted(series) for series in (battery_points or [])]
    adjusted = bool(household_meter_includes_battery and battery_series)

    if household_meter_includes_battery and not battery_series:
        return _invalid_night(night_date, window_start, window_end, duration_h)

    gross_points: list[tuple[datetime, float]] = []
    for timestamp, meter_w in points:
        gross_w = meter_w
        if household_meter_includes_battery:
            battery_w = 0.0
            for series in battery_series:
                value = _latest_prior(series, timestamp, battery_tolerance_s)
                if value is None:
                    return _invalid_night(night_date, window_start, window_end, duration_h)
                battery_w += value
            gross_w = meter_w - battery_w
        gross_points.append((timestamp, max(0.0, gross_w)))

    energy_wh = 0.0
    covered_s = 0.0
    for (left_time, left_w), (right_time, right_w) in pairwise(gross_points):
        delta_s = (right_time - left_time).total_seconds()
        if delta_s <= 0 or delta_s > max_gap_s:
            continue
        energy_wh += (left_w + right_w) / 2 * delta_s / 3600
        covered_s += delta_s

    coverage = covered_s / (duration_h * 3600) if duration_h > 0 else 0.0
    valid = len(gross_points) >= MIN_AGGREGATION_POINTS and duration_h > 0 and coverage >= min_valid_ratio
    if not valid:
        return _invalid_night(night_date, window_start, window_end, duration_h)

    return NightHouseholdEnergy(
        night_date=night_date,
        window_start=window_start,
        window_end=window_end,
        energy_wh=energy_wh,
        duration_h=duration_h,
        avg_power_w=energy_wh / duration_h,
        samples=len(gross_points),
        valid=True,
        battery_adjusted=adjusted,
    )


def _invalid_night(
    night_date: date,
    window_start: datetime,
    window_end: datetime,
    duration_h: float,
) -> NightHouseholdEnergy:
    """Build a zero-energy invalid night without hiding the measurement gap."""
    return NightHouseholdEnergy(
        night_date=night_date,
        window_start=window_start,
        window_end=window_end,
        energy_wh=0.0,
        duration_h=duration_h,
        avg_power_w=0.0,
        samples=0,
        valid=False,
        battery_adjusted=False,
    )


def previous_night_dates(
    target_night_date: date,
    weekdays: list[str] | tuple[str, ...],
    max_age_days: int,
) -> list[date]:
    """Return all candidate dates in selection order, newest occurrence first."""
    oldest = target_night_date - timedelta(days=max_age_days)
    candidates: list[date] = []
    for key in weekdays:
        if key not in WEEKDAYS:
            continue
        offset = (target_night_date.weekday() - WEEKDAYS.index(key)) % 7
        candidate = target_night_date - timedelta(days=offset or 7)
        while candidate >= oldest:
            candidates.append(candidate)
            candidate -= timedelta(days=7)
    return candidates


def select_nights(
    *,
    target_night_date: date,
    nights: dict[date, NightHouseholdEnergy],
    weekdays: list[str] | tuple[str, ...],
    max_age_days: int,
) -> list[NightHouseholdEnergy]:
    """Return the newest valid occurrence of each configured weekday."""
    oldest = target_night_date - timedelta(days=max_age_days)
    selected: list[NightHouseholdEnergy] = []
    seen: set[date] = set()

    for key in weekdays:
        if key not in WEEKDAYS:
            continue
        offset = (target_night_date.weekday() - WEEKDAYS.index(key)) % 7
        candidate = target_night_date - timedelta(days=offset or 7)
        while candidate >= oldest:
            night = nights.get(candidate)
            if night is not None and night.valid and candidate not in seen:
                selected.append(night)
                seen.add(candidate)
                break
            candidate -= timedelta(days=7)
    return selected


def select_baseline_power_w(  # noqa: PLR0913 - selection, validity and safety inputs are independent policy settings.
    *,
    target_night_date: date,
    nights: dict[date, NightHouseholdEnergy],
    weekdays: list[str] | tuple[str, ...],
    max_age_days: int,
    min_nights: int,
    safety_factor: float,
    min_power_w: float,
    max_power_w: float,
) -> float | None:
    """Select the latest valid occurrence per weekday and return gross power.

    Every weekday contributes at most one night. If the latest occurrence is
    invalid or too old, the selection walks back one week at a time. The
    weighted average uses total energy divided by total duration, not the mean
    of nightly averages.
    """
    selected = select_nights(
        target_night_date=target_night_date,
        nights=nights,
        weekdays=weekdays,
        max_age_days=max_age_days,
    )
    if len(selected) < min_nights:
        return None

    total_energy_wh = sum(night.energy_wh for night in selected)
    total_duration_h = sum(night.duration_h for night in selected)
    if total_duration_h <= 0:
        return None
    power_w = total_energy_wh / total_duration_h * safety_factor
    return min(max_power_w, max(min_power_w, power_w))


def floor_to_minute(timestamp: datetime) -> datetime:
    """Return the start of the minute, useful for deterministic test fixtures."""
    return datetime.combine(timestamp.date(), time(timestamp.hour, timestamp.minute), tzinfo=timestamp.tzinfo)
