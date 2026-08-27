import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.collectors.definitions.measurement import Measurement
from src.manager.collector_manager import CollectorManager


def make_measurement(
    metric="temperature",
    value=20.5,
):
    return Measurement(
        timestamp=datetime(2026, 8, 27, 12, 0),
        source="test",
        metric=metric,
        value=value,
        unit="°C",
    )


def test_init():
    collectors = [Mock(), Mock()]
    database = Mock()

    manager = CollectorManager(
        collectors=collectors,
        database=database,
        interval=15,
    )

    assert manager.collectors is collectors
    assert manager.database is database
    assert manager.interval == 15


def test_connect_connects_all_collectors():
    collector1 = Mock()
    collector2 = Mock()

    manager = CollectorManager(
        collectors=[collector1, collector2],
    )

    asyncio.run(manager.connect())

    collector1.connect.assert_called_once()
    collector2.connect.assert_called_once()


def test_disconnect_disconnects_all_collectors():
    collector1 = Mock()
    collector2 = Mock()

    manager = CollectorManager(
        collectors=[collector1, collector2],
    )

    asyncio.run(manager.disconnect())

    collector1.disconnect.assert_called_once()
    collector2.disconnect.assert_called_once()


def test_collect_all_returns_measurements():
    measurement1 = make_measurement("temperature", 20.5)
    measurement2 = make_measurement("humidity", 60.0)

    collector1 = Mock()
    collector1.collect.return_value = [measurement1]

    collector2 = Mock()
    collector2.collect.return_value = [measurement2]

    manager = CollectorManager(
        collectors=[collector1, collector2],
    )

    result = asyncio.run(manager.collect_all())

    assert result == [measurement1, measurement2]

    collector1.collect.assert_called_once()
    collector2.collect.assert_called_once()


def test_collect_all_skips_failed_collector():
    measurement = make_measurement()

    failing_collector = Mock()
    failing_collector.collect.side_effect = RuntimeError("collector failed")

    working_collector = Mock()
    working_collector.collect.return_value = [measurement]

    manager = CollectorManager(
        collectors=[failing_collector, working_collector],
    )

    result = asyncio.run(manager.collect_all())

    assert result == [measurement]


def test_collect_all_skips_unavailable_collector():
    measurement = make_measurement()

    unavailable_collector = Mock()
    unavailable_collector.collect.return_value = []

    working_collector = Mock()
    working_collector.collect.return_value = [measurement]

    manager = CollectorManager(
        collectors=[unavailable_collector, working_collector],
    )

    result = asyncio.run(manager.collect_all())

    assert result == [measurement]


def test_collect_all_returns_empty_list_when_no_measurements():
    collector = Mock()
    collector.collect.return_value = []

    manager = CollectorManager(
        collectors=[collector],
    )

    result = asyncio.run(manager.collect_all())

    assert result == []


def test_output(capsys):
    measurement = make_measurement(
        metric="temperature",
        value=20.5,
    )

    CollectorManager.output([measurement])

    captured = capsys.readouterr()

    assert "2026-08-27T12:00:00" in captured.out
    assert "test" in captured.out
    assert "temperature" in captured.out
    assert "20.500" in captured.out
    assert "°C" in captured.out


def test_run_without_database_disconnects_on_shutdown():
    manager = CollectorManager(
        collectors=[],
        database=None,
        interval=10,
    )

    manager.connect = AsyncMock()
    manager.collect_all = AsyncMock(
        side_effect=asyncio.CancelledError,
    )
    manager.output = Mock()
    manager.disconnect = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager.run())

    manager.connect.assert_awaited_once()
    manager.collect_all.assert_awaited_once()
    manager.output.assert_not_called()
    manager.disconnect.assert_awaited_once()


def test_run_stores_measurements_when_database_is_configured():
    measurement = make_measurement()

    database = Mock()
    database.store = AsyncMock()

    manager = CollectorManager(
        collectors=[],
        database=database,
        interval=10,
    )

    manager.connect = AsyncMock()
    manager.collect_all = AsyncMock(
        side_effect=[
            [measurement],
            asyncio.CancelledError(),
        ],
    )
    manager.output = Mock()
    manager.disconnect = AsyncMock()

    with (
        patch(
            "src.manager.collector_manager.asyncio.sleep",
            new_callable=AsyncMock,
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        asyncio.run(manager.run())

    manager.connect.assert_awaited_once()
    manager.output.assert_called_once_with([measurement])
    database.store.assert_awaited_once_with([measurement])
    manager.disconnect.assert_awaited_once()


def test_run_sleeps_between_collection_cycles():
    measurement = make_measurement()

    manager = CollectorManager(
        collectors=[],
        database=None,
        interval=15,
    )

    manager.connect = AsyncMock()
    manager.collect_all = AsyncMock(
        side_effect=[
            [measurement],
            asyncio.CancelledError(),
        ],
    )
    manager.output = Mock()
    manager.disconnect = AsyncMock()

    with (
        patch(
            "src.manager.collector_manager.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep,
        pytest.raises(asyncio.CancelledError),
    ):
        asyncio.run(manager.run())

    sleep.assert_awaited_once_with(15)
    manager.disconnect.assert_awaited_once()
