import asyncio
from unittest.mock import AsyncMock, Mock, patch

import src.app

import pytest


@pytest.fixture
def mocked_config():
    config = Mock()
    config.collection.timezone = "Europe/Berlin"
    config.storage = Mock()
    return config


def test_run_creates_collectors_and_runs_manager(mocked_config):
    mock_collectors = [Mock(), Mock(), Mock()]
    mock_databases = [Mock()]
    mock_estimators = [Mock()]
    mock_calculators = [Mock()]
    mock_manager = Mock()
    mock_manager.run = AsyncMock()

    with (
        patch.object(src.app, "config_obj", mocked_config),
        patch.object(
            src.app,
            "create_collectors",
            return_value=mock_collectors,
        ) as collectors_factory,
        patch.object(
            src.app,
            "create_databases",
            return_value=mock_databases,
        ) as databases_factory,
        patch.object(
            src.app,
            "create_estimators",
            return_value=mock_estimators,
        ) as estimators_factory,
        patch.object(
            src.app,
            "create_calculators",
            return_value=mock_calculators,
        ) as calculators_factory,
        patch.object(
            src.app,
            "MeasurementManager",
            return_value=mock_manager,
        ) as manager_cls,
    ):
        asyncio.run(src.app.run())

    collectors_factory.assert_called_once_with(mocked_config)
    databases_factory.assert_called_once_with(mocked_config)
    estimators_factory.assert_called_once_with(
        mocked_config.estimators,
    )
    calculators_factory.assert_called_once_with(
        mocked_config.calculators,
    )

    manager_cls.assert_called_once_with(
        collectors=mock_collectors,
        databases=mock_databases,
        estimators=mock_estimators,
        calculators=mock_calculators,
        timezone=mocked_config.collection.timezone,
        storage_config=mocked_config.storage,
    )

    mock_manager.run.assert_awaited_once()
    mock_databases[0].close.assert_called_once()


def test_run_closes_database_when_manager_fails(mocked_config):
    mock_databases = [Mock()]
    mock_manager = Mock()

    error = RuntimeError("manager failed")
    mock_manager.run = AsyncMock(side_effect=error)

    with (
        patch.object(src.app, "config_obj", mocked_config),
        patch.object(
            src.app,
            "create_collectors",
            return_value=[],
        ),
        patch.object(
            src.app,
            "create_databases",
            return_value=mock_databases,
        ),
        patch.object(
            src.app,
            "create_estimators",
            return_value=[],
        ),
        patch.object(
            src.app,
            "create_calculators",
            return_value=[],
        ),
        patch.object(
            src.app,
            "MeasurementManager",
            return_value=mock_manager,
        ),
        pytest.raises(RuntimeError, match="manager failed"),
    ):
        asyncio.run(src.app.run())

    mock_databases[0].close.assert_called_once()


def test_main_sets_up_event_loop():
    loop = Mock()

    with (
        patch.object(src.app, "asyncio") as asyncio_mock,
        patch.object(src.app, "signal") as signal_mock,
    ):
        asyncio_mock.new_event_loop.return_value = loop

        src.app.main()

    coroutine = loop.run_until_complete.call_args.args[0]
    coroutine.close()

    asyncio_mock.new_event_loop.assert_called_once()
    asyncio_mock.set_event_loop.assert_called_once_with(loop)

    loop.run_until_complete.assert_called_once()
    loop.close.assert_called_once()

    assert signal_mock.signal.call_count == 2


def test_module_entry_point():
    assert callable(src.app.main)
