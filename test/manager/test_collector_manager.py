import asyncio
from datetime import datetime, UTC
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from src.collectors.definitions.measurement import Measurement
from src.config.config import StorageConfig, StorageMeasurementConfig
from src.manager.collector_manager import CollectorManager

import pytest


def make_measurement(
    metric="temperature",
    value=20.5,
    source="test",
):
    return Measurement(
        timestamp=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
        source=source,
        metric=metric,
        value=value,
        unit="°C",
    )


def make_storage_config(*measurements):
    return StorageConfig(
        enabled=True,
        measurements=[
            StorageMeasurementConfig(
                source=source,
                metric=metric,
                measurement_type=measurement_type,
            )
            for source, metric, measurement_type in measurements
        ],
    )


def make_manager(
    *,
    collectors=None,
    databases=None,
    interval=300,
    timezone="UTC",
    storage_measurements=(),
):
    return CollectorManager(
        collectors=[] if collectors is None else collectors,
        databases=[] if databases is None else databases,
        interval=interval,
        timezone=timezone,
        storage_config=make_storage_config(*storage_measurements),
    )


def test_init():
    collectors = [Mock(), Mock()]
    databases = [Mock()]

    storage_config = make_storage_config(
        ("test", "temperature", "current"),
    )

    manager = CollectorManager(
        collectors=collectors,
        databases=databases,
        interval=15,
        timezone="Europe/Berlin",
        storage_config=storage_config,
    )

    assert manager.collectors is collectors
    assert manager.databases is databases
    assert manager.interval == 15
    assert manager.timezone == ZoneInfo("Europe/Berlin")


def test_connect_connects_all_collectors():
    collector1 = Mock()
    collector2 = Mock()

    manager = make_manager(
        collectors=[collector1, collector2],
    )

    asyncio.run(manager.connect())

    collector1.connect.assert_called_once()
    collector2.connect.assert_called_once()


def test_disconnect_disconnects_all_collectors():
    collector1 = Mock()
    collector2 = Mock()

    manager = make_manager(
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

    manager = make_manager(
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

    manager = make_manager(
        collectors=[failing_collector, working_collector],
        storage_measurements=((measurement.source, measurement.metric, measurement.measurement_type),),
    )

    result = asyncio.run(manager.collect_all())

    assert result == [measurement]


def test_collect_all_skips_unavailable_collector():
    measurement = make_measurement()

    unavailable_collector = Mock()
    unavailable_collector.collect.return_value = []

    working_collector = Mock()
    working_collector.collect.return_value = [measurement]

    manager = make_manager(
        collectors=[unavailable_collector, working_collector],
        storage_measurements=((measurement.source, measurement.metric, measurement.measurement_type),),
    )

    result = asyncio.run(manager.collect_all())

    assert result == [measurement]


def test_collect_all_returns_empty_list_when_no_measurements():
    collector = Mock()
    collector.collect.return_value = []

    manager = make_manager(
        collectors=[collector],
    )

    result = asyncio.run(manager.collect_all())

    assert result == []


def test_output(caplog):
    measurement = make_measurement(
        metric="temperature",
        value=20.5,
    )

    with caplog.at_level("INFO"):
        CollectorManager.output([measurement])

    assert "2026-08-27T12:00:00" in caplog.text
    assert "test" in caplog.text
    assert "temperature" in caplog.text
    assert "20.500" in caplog.text
    assert "°C" in caplog.text
    assert "src.manager.collector_manager" in caplog.text


def test_filter_measurements_stores_current_meter_values():
    manager = make_manager(
        interval=10,
        storage_measurements=(
            ("test", "grid_import_power", "current"),
            ("test", "grid_export_power", "current"),
        ),
    )

    measurements = [
        make_measurement("grid_import_power", 1.5),
        make_measurement("grid_export_power", 2.5),
    ]

    result = manager._filter_measurements(measurements)

    assert result == measurements


def test_filter_measurements_stores_current_meter_values_every_cycle():
    manager = make_manager(
        interval=10,
        storage_measurements=(
            ("test", "grid_import_power", "current"),
            ("test", "grid_export_power", "current"),
        ),
    )

    first = [
        make_measurement("grid_import_power", 1.5),
        make_measurement("grid_export_power", 2.5),
    ]

    second = [
        make_measurement("grid_import_power", 1.7),
        make_measurement("grid_export_power", 2.7),
    ]

    assert manager._filter_measurements(first) == first
    assert manager._filter_measurements(second) == second


def test_filter_measurements_does_not_store_daily_values_until_both_are_available():
    manager = make_manager(
        interval=10,
        storage_measurements=(
            ("meter_grid", "grid_import_energy_total", "current"),
            ("meter_grid", "grid_export_energy_total", "current"),
        ),
    )

    measurements = [
        make_measurement(
            "grid_import_energy_total",
            100.0,
            source="meter_grid",
        ),
    ]

    with patch("src.manager.collector_manager.datetime") as datetime_mock:
        datetime_mock.now.return_value.hour = 0

        result = manager._filter_measurements(measurements)

    assert result == []
    assert manager._daily_values_stored == set()


def test_filter_measurements_stores_daily_values_when_both_are_available():
    manager = make_manager(
        collectors=[],
        interval=10,
        storage_measurements=(
            ("meter_grid", "grid_import_energy_total", "current"),
            ("meter_grid", "grid_export_energy_total", "current"),
        ),
    )

    measurements = [
        make_measurement(
            "grid_import_energy_total",
            100.0,
            source="meter_grid",
        ),
        make_measurement(
            "grid_export_energy_total",
            50.0,
            source="meter_grid",
        ),
    ]

    with patch("src.manager.collector_manager.datetime") as datetime_mock:
        datetime_mock.now.return_value.hour = 0

        result = manager._filter_measurements(measurements)

    assert result == measurements
    assert manager._daily_values_stored == {"meter_grid"}


def test_filter_measurements_stores_daily_values_only_once():
    manager = make_manager(
        interval=10,
        storage_measurements=(
            ("meter_grid", "grid_import_energy_total", "current"),
            ("meter_grid", "grid_export_energy_total", "current"),
        ),
    )

    measurements = [
        make_measurement(
            "grid_import_energy_total",
            100.0,
            source="meter_grid",
        ),
        make_measurement(
            "grid_export_energy_total",
            50.0,
            source="meter_grid",
        ),
    ]

    with patch("src.manager.collector_manager.datetime") as datetime_mock:
        datetime_mock.now.return_value.hour = 0

        first_result = manager._filter_measurements(measurements)
        second_result = manager._filter_measurements(measurements)

    assert first_result == measurements
    assert second_result == []
    assert manager._daily_values_stored == {"meter_grid"}


def test_filter_measurements_does_not_store_daily_values_outside_midnight():
    manager = make_manager(
        interval=10,
        storage_measurements=(
            ("meter_grid", "grid_import_energy_total", "current"),
            ("meter_grid", "grid_export_energy_total", "current"),
        ),
    )

    measurements = [
        make_measurement(
            "grid_import_energy_total",
            100.0,
            source="meter_grid",
        ),
        make_measurement(
            "grid_export_energy_total",
            50.0,
            source="meter_grid",
        ),
    ]

    with patch("src.manager.collector_manager.datetime") as datetime_mock:
        datetime_mock.now.return_value.hour = 12

        result = manager._filter_measurements(measurements)

    assert result == []


def test_filter_measurements_stores_normal_measurements_every_cycle():
    measurement = make_measurement(
        metric="temperature",
        value=20.5,
    )

    manager = make_manager(
        storage_measurements=(("test", "temperature", "current"),),
    )

    result = manager._filter_measurements([measurement])

    assert result == [measurement]


def test_filter_measurements_excludes_unconfigured_measurements():
    measurement = make_measurement(
        metric="temperature",
        value=20.5,
    )

    manager = make_manager(
        storage_measurements=(),
    )

    result = manager._filter_measurements([measurement])

    assert result == []


def test_filter_measurements_stores_daily_values_from_one_meter():
    manager = make_manager(
        interval=10,
        storage_measurements=(
            ("meter_grid", "grid_import_energy_total", "current"),
            ("meter_grid", "grid_export_energy_total", "current"),
        ),
    )

    measurements = [
        make_measurement(
            "grid_import_energy_total",
            100.0,
            source="meter_grid",
        ),
        make_measurement(
            "grid_export_energy_total",
            50.0,
            source="meter_grid",
        ),
    ]

    with patch("src.manager.collector_manager.datetime") as datetime_mock:
        datetime_mock.now.return_value.hour = 0

        result = manager._filter_measurements(measurements)

    assert result == measurements
    assert manager._daily_values_stored == {"meter_grid"}


def test_filter_measurements_stores_daily_values_from_both_meters():
    manager = make_manager(
        interval=10,
        storage_measurements=(
            ("meter_grid", "grid_import_energy_total", "current"),
            ("meter_grid", "grid_export_energy_total", "current"),
            ("meter_household", "grid_import_energy_total", "current"),
            ("meter_household", "grid_export_energy_total", "current"),
        ),
    )

    measurements = [
        make_measurement(
            "grid_import_energy_total",
            100.0,
            source="meter_grid",
        ),
        make_measurement(
            "grid_export_energy_total",
            50.0,
            source="meter_grid",
        ),
        make_measurement(
            "grid_import_energy_total",
            200.0,
            source="meter_household",
        ),
        make_measurement(
            "grid_export_energy_total",
            25.0,
            source="meter_household",
        ),
    ]

    with patch("src.manager.collector_manager.datetime") as datetime_mock:
        datetime_mock.now.return_value.hour = 0

        result = manager._filter_measurements(measurements)

    assert result == measurements
    assert manager._daily_values_stored == {
        "meter_grid",
        "meter_household",
    }


def test_filter_measurements_stores_each_meter_independently():
    manager = make_manager(
        interval=10,
        storage_measurements=(
            ("meter_grid", "grid_import_energy_total", "current"),
            ("meter_grid", "grid_export_energy_total", "current"),
            ("meter_household", "grid_import_energy_total", "current"),
            ("meter_household", "grid_export_energy_total", "current"),
        ),
    )

    grid_measurements = [
        make_measurement(
            "grid_import_energy_total",
            100.0,
            source="meter_grid",
        ),
        make_measurement(
            "grid_export_energy_total",
            50.0,
            source="meter_grid",
        ),
    ]

    household_measurements = [
        make_measurement(
            "grid_import_energy_total",
            200.0,
            source="meter_household",
        ),
        make_measurement(
            "grid_export_energy_total",
            25.0,
            source="meter_household",
        ),
    ]

    with patch("src.manager.collector_manager.datetime") as datetime_mock:
        datetime_mock.now.return_value.hour = 0

        first_result = manager._filter_measurements(grid_measurements)
        second_result = manager._filter_measurements(household_measurements)

    assert first_result == grid_measurements
    assert second_result == household_measurements
    assert manager._daily_values_stored == {
        "meter_grid",
        "meter_household",
    }


# ---------------------------------------------------------------------------
# Independent collector timing / database tests
# ---------------------------------------------------------------------------


def test_run_collector_uses_collector_interval():
    collector = Mock()
    collector.interval = 10
    collector.collect.return_value = []

    manager = make_manager(
        collectors=[collector],
    )

    sleep_calls = []

    async def fake_sleep(delay):
        sleep_calls.append(delay)
        raise asyncio.CancelledError

    with (
        patch(
            "src.manager.collector_manager.asyncio.sleep",
            side_effect=fake_sleep,
        ),
        patch(
            "src.manager.collector_manager.asyncio.get_running_loop",
        ) as get_loop,
    ):
        get_loop.return_value.time.side_effect = [0, 0]

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(manager._run_collector(collector))

    collector.collect.assert_called_once()
    assert sleep_calls == [10]


def test_store_measurements_writes_to_database_immediately():
    measurement = make_measurement()

    database = Mock()
    database.store = AsyncMock()

    manager = make_manager(
        databases=[database],
        storage_measurements=(("test", "temperature", "current"),),
    )

    with patch.object(manager, "output") as output:
        asyncio.run(
            manager._store_measurements([measurement]),
        )

    output.assert_called_once_with([measurement])
    database.store.assert_awaited_once_with([measurement])


def test_store_measurements_continues_with_next_database_when_database_fails(
    caplog,
):
    measurement = make_measurement()

    failing_database = Mock()
    failing_database.store = AsyncMock(
        side_effect=RuntimeError("database failed"),
    )

    working_database = Mock()
    working_database.store = AsyncMock()

    manager = make_manager(
        databases=[
            failing_database,
            working_database,
        ],
        storage_measurements=(("test", "temperature", "current"),),
    )

    with (
        patch.object(manager, "output"),
        caplog.at_level("ERROR"),
    ):
        asyncio.run(
            manager._store_measurements([measurement]),
        )

    failing_database.store.assert_awaited_once_with([measurement])
    working_database.store.assert_awaited_once_with([measurement])

    assert "Failed to store measurements in Mock" in caplog.text


def test_run_collector_continues_after_collection_failure():
    collector = Mock()
    collector.interval = 10

    collector.collect.side_effect = [
        RuntimeError("collector failed"),
        asyncio.CancelledError(),
    ]

    sleep = AsyncMock()

    manager = make_manager(
        collectors=[collector],
    )

    with (
        patch(
            "src.manager.collector_manager.asyncio.sleep",
            sleep,
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        asyncio.run(manager._run_collector(collector))

    assert collector.collect.call_count == 2
    sleep.assert_awaited_once()
    assert sleep.await_args is not None
    sleep_time = sleep.await_args.args[0]
    assert sleep_time == pytest.approx(10, abs=0.01)


def test_run_creates_independent_task_for_each_collector():
    collector1 = Mock()
    collector1.interval = 10

    collector2 = Mock()
    collector2.interval = 60

    manager = make_manager(
        collectors=[collector1, collector2],
    )

    created_tasks = []

    def fake_create_task(coro, name=None):
        created_tasks.append((coro, name))
        coro.close()
        return Mock()

    with (
        patch.object(
            manager,
            "connect",
            new_callable=AsyncMock,
        ),
        patch.object(
            manager,
            "disconnect",
            new_callable=AsyncMock,
        ),
        patch(
            "src.manager.collector_manager.asyncio.create_task",
            side_effect=fake_create_task,
        ),
        patch(
            "src.manager.collector_manager.asyncio.gather",
            new_callable=AsyncMock,
            side_effect=asyncio.CancelledError,
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        asyncio.run(manager.run())

    assert len(created_tasks) == 2
    assert created_tasks[0][1] == "collector-Mock"
    assert created_tasks[1][1] == "collector-Mock"


def test_reload_config_if_changed_does_nothing_when_config_is_unchanged():
    manager = make_manager()

    with (
        patch.object(Path, "stat") as stat,
        patch(
            "src.manager.collector_manager.load_config",
        ) as load_config,
    ):
        stat.return_value.st_mtime = manager._config_mod_time

        asyncio.run(manager._reload_config_if_changed())

    load_config.assert_not_called()


def test_reload_config_if_changed_updates_storage_filter():
    manager = make_manager(
        storage_measurements=(("meter_grid", "grid_import_power", "current"),),
    )

    new_storage_config = StorageConfig(
        enabled=True,
        measurements=[
            StorageMeasurementConfig(
                source="meter_grid",
                metric="grid_export_power",
                measurement_type="current",
            ),
        ],
    )

    new_config = Mock()
    new_config.storage = new_storage_config

    old_mtime = manager._config_mod_time
    new_mtime = old_mtime + 1

    with (
        patch(
            "src.manager.collector_manager.CONFIG_FILE",
        ) as config_file,
        patch(
            "src.manager.collector_manager.load_config",
            return_value=new_config,
        ) as load_config,
    ):
        config_file.stat.return_value.st_mtime = new_mtime

        asyncio.run(manager._reload_config_if_changed())

    load_config.assert_called_once_with()
    assert manager._config_mod_time == new_mtime

    measurement_import = make_measurement(
        metric="grid_import_power",
        source="meter_grid",
    )
    measurement_export = make_measurement(
        metric="grid_export_power",
        source="meter_grid",
    )

    assert manager.storage_filter.filter([measurement_import]) == []
    assert manager.storage_filter.filter([measurement_export]) == [
        measurement_export,
    ]


def test_reload_config_if_changed_keeps_old_config_when_reload_fails(
    caplog,
):
    manager = make_manager(
        storage_measurements=(("meter_grid", "grid_import_power", "current"),),
    )

    old_mtime = manager._config_mod_time
    new_mtime = old_mtime + 1

    measurement = make_measurement(
        metric="grid_import_power",
        source="meter_grid",
    )

    with (
        patch(
            "src.manager.collector_manager.CONFIG_FILE",
        ) as config_file,
        patch(
            "src.manager.collector_manager.load_config",
            side_effect=ValueError("invalid YAML"),
        ) as load_config,
        caplog.at_level("ERROR"),
    ):
        config_file.stat.return_value.st_mtime = new_mtime

        asyncio.run(manager._reload_config_if_changed())

    load_config.assert_called_once_with()
    assert manager.storage_filter.filter([measurement]) == [measurement]
    assert manager._config_mod_time == old_mtime
    assert "Failed to reload configuration" in caplog.text


def test_reload_config_if_changed_only_reloads_once_for_same_mtime():
    manager = make_manager()

    new_storage_config = StorageConfig(
        enabled=True,
        measurements=[
            StorageMeasurementConfig(
                source="test",
                metric="temperature",
            ),
        ],
    )

    new_config = Mock()
    new_config.storage = new_storage_config

    new_mtime = manager._config_mod_time + 1

    with (
        patch(
            "src.manager.collector_manager.CONFIG_FILE",
        ) as config_file,
        patch(
            "src.manager.collector_manager.load_config",
            return_value=new_config,
        ) as load_config,
    ):
        config_file.stat.return_value.st_mtime = new_mtime

        asyncio.run(manager._reload_config_if_changed())
        asyncio.run(manager._reload_config_if_changed())

    load_config.assert_called_once_with()
    assert manager._config_mod_time == new_mtime


def test_reload_config_if_changed_handles_missing_config_file(caplog):
    manager = make_manager()

    with (
        patch(
            "src.manager.collector_manager.CONFIG_FILE",
        ) as config_file,
        caplog.at_level("ERROR"),
    ):
        config_file.stat.side_effect = OSError("file unavailable")

        asyncio.run(manager._reload_config_if_changed())

    assert "Failed to stat configuration file" in caplog.text


def test_run_collector_checks_for_config_reload():
    collector = Mock()
    collector.interval = 10
    collector.collect.return_value = []

    manager = make_manager(
        collectors=[collector],
    )

    reload_config = AsyncMock()

    async def fake_sleep(_delay):
        raise asyncio.CancelledError

    with (
        patch.object(
            manager,
            "_reload_config_if_changed",
            reload_config,
        ),
        patch(
            "src.manager.collector_manager.asyncio.sleep",
            side_effect=fake_sleep,
        ),
        patch(
            "src.manager.collector_manager.asyncio.get_running_loop",
        ) as get_loop,
    ):
        get_loop.return_value.time.side_effect = [0, 0]

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(manager._run_collector(collector))

    reload_config.assert_awaited_once()
    collector.collect.assert_called_once()


def test_reload_config_if_changed_updates_measurement_type():
    manager = make_manager(
        storage_measurements=(("open_meteo", "temperature", "current"),),
    )

    new_config = Mock()
    new_config.storage = StorageConfig(
        enabled=True,
        measurements=[
            StorageMeasurementConfig(
                source="open_meteo",
                metric="temperature",
                measurement_type="forecast",
            ),
        ],
    )

    new_mtime = manager._config_mod_time + 1

    current = make_measurement(
        metric="temperature",
        source="open_meteo",
    )

    forecast = Measurement(
        timestamp=datetime(2026, 8, 27, 13, 0, tzinfo=UTC),
        source="open_meteo",
        metric="temperature",
        value=21.0,
        unit="°C",
        measurement_type="forecast",
    )

    with (
        patch(
            "src.manager.collector_manager.CONFIG_FILE",
        ) as config_file,
        patch(
            "src.manager.collector_manager.load_config",
            return_value=new_config,
        ),
    ):
        config_file.stat.return_value.st_mtime = new_mtime

        asyncio.run(manager._reload_config_if_changed())

    assert manager.storage_filter.filter([current]) == []
    assert manager.storage_filter.filter([forecast]) == [forecast]
