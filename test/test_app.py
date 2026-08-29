import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

import src.app


@pytest.fixture
def mocked_config():
    config = Mock()
    config.collection.interval = 5
    return config


def test_run_creates_collectors_and_runs_manager(mocked_config):
    mock_collectors = [Mock(), Mock(), Mock()]
    mock_databases = [Mock()]
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
            "CollectorManager",
            return_value=mock_manager,
        ) as manager_cls,
    ):
        asyncio.run(src.app.run())

    collectors_factory.assert_called_once_with(mocked_config)
    databases_factory.assert_called_once_with(mocked_config)

    manager_cls.assert_called_once_with(
        collectors=mock_collectors,
        databases=mock_databases,
        interval=5,
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
            "CollectorManager",
            return_value=mock_manager,
        ),
        pytest.raises(RuntimeError, match="manager failed"),
    ):
        asyncio.run(src.app.run())

    mock_databases[0].close.assert_called_once()


def test_main_calls_asyncio_run():
    with patch.object(src.app.asyncio, "run") as asyncio_run:
        src.app.main()

    asyncio_run.assert_called_once()

    coroutine = asyncio_run.call_args.args[0]

    assert asyncio.iscoroutine(coroutine)
    coroutine.close()


def test_module_entry_point():
    assert callable(src.app.main)
