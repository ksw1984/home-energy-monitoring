import asyncio
from unittest.mock import AsyncMock, Mock, patch

import src.hems.app

import pytest


@pytest.fixture
def mocked_config():
    config = Mock()
    config.collection.interval = 5
    config.collection.timezone = "Europe/Berlin"

    config.collectors = []
    config.databases = []
    config.estimators = []
    config.calculators = []

    config.storage = Mock()
    config.bems = None

    return config


def test_run_creates_collectors_and_runs_manager(mocked_config):
    mock_collectors = [Mock(), Mock(), Mock()]
    mock_databases = [Mock()]
    mock_estimators = [Mock()]
    mock_calculators = [Mock()]
    mock_manager = Mock()
    mock_manager.run = AsyncMock()
    mock_bus = Mock()
    publisher = Mock()
    publisher.start = AsyncMock()
    publisher.close = AsyncMock()

    with (
        patch.object(src.hems.app, "config_obj", mocked_config),
        patch.object(src.hems.app, "MqttMeasurementPublisher", return_value=publisher),
        patch.object(
            src.hems.app,
            "MeasurementBus",
            return_value=mock_bus,
        ),
        patch.object(
            src.hems.app,
            "create_collectors",
            return_value=mock_collectors,
        ) as collectors_factory,
        patch.object(
            src.hems.app,
            "create_databases",
            return_value=mock_databases,
        ) as databases_factory,
        patch.object(
            src.hems.app,
            "create_estimators",
            return_value=mock_estimators,
        ) as estimators_factory,
        patch.object(
            src.hems.app,
            "create_calculators",
            return_value=mock_calculators,
        ) as calculators_factory,
        patch.object(
            src.hems.app,
            "MeasurementManager",
            return_value=mock_manager,
        ) as manager_cls,
    ):
        asyncio.run(src.hems.app.run())

    collectors_factory.assert_called_once_with(mocked_config)
    databases_factory.assert_called_once_with(mocked_config)
    calculators_factory.assert_called_once_with(mocked_config.calculators)
    estimators_factory.assert_called_once_with(
        mocked_config.estimators,
        [],
    )

    manager_cls.assert_called_once_with(
        collectors=mock_collectors,
        databases=mock_databases,
        estimators=mock_estimators,
        calculators=mock_calculators,
        timezone="Europe/Berlin",
        storage_config=mocked_config.storage,
        bus=mock_bus,
        live_publisher=publisher.publish,
        configure_live_publisher=publisher.configure,
    )

    mock_manager.run.assert_awaited_once()
    publisher.configure.assert_called_once_with(mocked_config)
    publisher.start.assert_awaited_once()
    publisher.close.assert_awaited_once()

    for database in mock_databases:
        database.close.assert_called_once()


def test_run_does_not_host_bems_control(mocked_config):
    mock_databases = [Mock()]
    mock_manager = Mock()
    mock_manager.run = AsyncMock()
    mock_bus = Mock()
    mocked_config.bems = Mock()
    mocked_config.bems.enabled = True
    publisher = Mock()
    publisher.start = AsyncMock()
    publisher.close = AsyncMock()

    with (
        patch.object(src.hems.app, "config_obj", mocked_config),
        patch.object(src.hems.app, "MeasurementBus", return_value=mock_bus),
        patch.object(src.hems.app, "create_collectors", return_value=[]),
        patch.object(src.hems.app, "create_databases", return_value=mock_databases),
        patch.object(src.hems.app, "create_estimators", return_value=[]),
        patch.object(src.hems.app, "create_calculators", return_value=[]),
        patch.object(src.hems.app, "MeasurementManager", return_value=mock_manager),
        patch.object(src.hems.app, "MqttMeasurementPublisher", return_value=publisher),
    ):
        asyncio.run(src.hems.app.run())

    publisher.start.assert_awaited_once()
    publisher.close.assert_awaited_once()

    for database in mock_databases:
        database.close.assert_called_once()


def test_run_closes_database_when_manager_fails(mocked_config):
    mock_databases = [Mock()]
    mock_manager = Mock()

    error = RuntimeError("manager failed")
    mock_manager.run = AsyncMock(side_effect=error)

    with (
        patch.object(src.hems.app, "config_obj", mocked_config),
        patch.object(src.hems.app, "MqttMeasurementPublisher", return_value=Mock(start=AsyncMock(), close=AsyncMock())),
        patch.object(
            src.hems.app,
            "create_collectors",
            return_value=[],
        ),
        patch.object(
            src.hems.app,
            "create_databases",
            return_value=mock_databases,
        ),
        patch.object(
            src.hems.app,
            "create_estimators",
            return_value=[],
        ),
        patch.object(
            src.hems.app,
            "create_calculators",
            return_value=[],
        ),
        patch.object(
            src.hems.app,
            "MeasurementManager",
            return_value=mock_manager,
        ),
        pytest.raises(RuntimeError, match="manager failed"),
    ):
        asyncio.run(src.hems.app.run())

    mock_databases[0].close.assert_called_once()


def test_main_sets_up_event_loop():
    loop = Mock()

    with (
        patch.object(src.hems.app, "asyncio") as asyncio_mock,
        patch.object(src.hems.app, "signal") as signal_mock,
    ):
        asyncio_mock.new_event_loop.return_value = loop

        src.hems.app.main()

    coroutine = loop.run_until_complete.call_args.args[0]
    coroutine.close()

    asyncio_mock.new_event_loop.assert_called_once()
    asyncio_mock.set_event_loop.assert_called_once_with(loop)

    loop.run_until_complete.assert_called_once()
    loop.close.assert_called_once()

    assert signal_mock.signal.call_count == 2


def test_module_entry_point():
    assert callable(src.hems.app.main)
