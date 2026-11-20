import asyncio
from datetime import datetime, timedelta, UTC

from src.collectors.definitions.common.measurement import Measurement
from src.manager.measurement_bus import MeasurementBus

import pytest

GRID_SOURCE = "meter_grid_dlms"
GRID_METRIC = "grid_active_power"
BASE_TIME = datetime(2026, 10, 18, 12, tzinfo=UTC)


def make_measurement(
    timestamp: datetime = BASE_TIME,
    *,
    source: str = GRID_SOURCE,
    metric: str = GRID_METRIC,
    value: float = 500.0,
    unit: str = "W",
) -> Measurement:
    return Measurement(timestamp, source, metric, value, unit)


def test_publish_stores_latest_per_series():
    bus = MeasurementBus()
    point = make_measurement()

    bus.publish([point])

    assert bus.latest(GRID_SOURCE, GRID_METRIC) == point


def test_latest_returns_none_for_unknown_series():
    assert MeasurementBus().latest(GRID_SOURCE, GRID_METRIC) is None


def test_publish_ignores_older_timestamp():
    bus = MeasurementBus()
    newer = make_measurement(BASE_TIME + timedelta(seconds=1), value=600.0)
    older = make_measurement(BASE_TIME, value=400.0)

    bus.publish([newer])
    bus.publish([older])

    assert bus.latest(GRID_SOURCE, GRID_METRIC) == newer


def test_wait_returns_immediately_for_unseen_value():
    bus = MeasurementBus()
    bus.publish([make_measurement()])

    assert asyncio.run(
        bus.wait_for_next(
            source=GRID_SOURCE,
            metric=GRID_METRIC,
            after_timestamp=None,
            timeout=1.0,
        )
    )


def test_wait_wakes_when_value_is_published():
    async def scenario() -> bool:
        bus = MeasurementBus()
        waiter = asyncio.create_task(
            bus.wait_for_next(
                source=GRID_SOURCE,
                metric=GRID_METRIC,
                after_timestamp=None,
                timeout=1.0,
            )
        )

        # Let the waiter reach the event wait before publishing.
        await asyncio.sleep(0)
        bus.publish([make_measurement()])

        return await asyncio.wait_for(waiter, timeout=1.0)

    assert asyncio.run(scenario())


def test_wait_returns_false_on_timeout():
    bus = MeasurementBus()

    assert not asyncio.run(
        bus.wait_for_next(
            source=GRID_SOURCE,
            metric=GRID_METRIC,
            after_timestamp=None,
            timeout=0.01,
        )
    )


def test_wait_does_not_accept_the_same_timestamp_twice():
    bus = MeasurementBus()
    point = make_measurement()
    bus.publish([point])

    assert not asyncio.run(
        bus.wait_for_next(
            source=GRID_SOURCE,
            metric=GRID_METRIC,
            after_timestamp=point.timestamp,
            timeout=0.01,
        )
    )


def test_wait_keeps_its_deadline_across_unrelated_publishes():
    async def scenario() -> bool:
        bus = MeasurementBus()
        waiter = asyncio.create_task(
            bus.wait_for_next(
                source=GRID_SOURCE,
                metric=GRID_METRIC,
                after_timestamp=None,
                timeout=0.05,
            )
        )

        await asyncio.sleep(0)
        bus.publish([make_measurement(source="inverter_fronius", metric="pv_power")])

        return await asyncio.wait_for(waiter, timeout=1.0)

    assert not asyncio.run(scenario())


def test_two_waiters_both_wake_on_one_publish():
    async def scenario() -> list[bool]:
        bus = MeasurementBus()
        waiters = [
            asyncio.create_task(
                bus.wait_for_next(
                    source=GRID_SOURCE,
                    metric=GRID_METRIC,
                    after_timestamp=None,
                    timeout=1.0,
                )
            )
            for _ in range(2)
        ]

        await asyncio.sleep(0)
        bus.publish([make_measurement()])

        return await asyncio.gather(*waiters)

    assert asyncio.run(scenario()) == [True, True]


@pytest.mark.parametrize("timeout", [0.0, -1.0])
def test_wait_rejects_non_positive_timeout(timeout):
    bus = MeasurementBus()

    with pytest.raises(ValueError, match="timeout must be positive"):
        asyncio.run(
            bus.wait_for_next(
                source=GRID_SOURCE,
                metric=GRID_METRIC,
                after_timestamp=None,
                timeout=timeout,
            )
        )
