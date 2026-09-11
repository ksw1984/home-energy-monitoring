import asyncio
from unittest.mock import Mock

from src.config.config import (
    CollectionConfig,
    ComponentConfig,
    Config,
    StorageConfig,
)
from src.manager.runtime_config import (
    configure_collectors,
    configure_databases,
)


def make_config(
    *,
    collectors=None,
    databases=None,
):
    return Config(
        collection=CollectionConfig(
            timezone="UTC",
        ),
        collectors=[] if collectors is None else collectors,
        databases=[] if databases is None else databases,
        storage=StorageConfig(
            enabled=True,
            measurements=[],
        ),
    )


def make_collector_config(
    *,
    source="test",
    enabled=True,
    interval=60,
):
    return ComponentConfig(
        type="test",
        enabled=enabled,
        interval=interval,
        attributes={
            "source": source,
        },
    )


def make_database_config(
    *,
    database_type="influxdb",
    enabled=True,
):
    return ComponentConfig(
        type=database_type,
        enabled=enabled,
        interval=None,
        attributes={},
    )


def make_collector(
    *,
    source="test",
    enabled=True,
    interval=300,
):
    collector = Mock()
    collector.source = source
    collector.enabled = enabled
    collector.interval = interval
    return collector


def make_database(
    *,
    database_type="influxdb",
    enabled=True,
):
    database = Mock()
    database.type = database_type
    database.enabled = enabled
    return database


def test_configure_collectors_updates_runtime_settings():
    collector = make_collector(
        source="fronius",
        enabled=True,
        interval=300,
    )

    config = make_config(
        collectors=[
            make_collector_config(
                source="fronius",
                enabled=False,
                interval=30,
            ),
        ],
    )

    asyncio.run(
        configure_collectors(
            [collector],
            config,
        ),
    )

    collector.configure_runtime.assert_called_once_with(
        interval=30,
        enabled=False,
    )


def test_configure_collectors_warns_when_configuration_missing(caplog):
    collector = make_collector(
        source="fronius",
    )

    config = make_config(
        collectors=[],
    )

    with caplog.at_level("WARNING"):
        asyncio.run(
            configure_collectors(
                [collector],
                config,
            ),
        )

    assert ("No configuration found for collector Mock (source=fronius)") in caplog.text

    collector.configure_runtime.assert_not_called()


def test_configure_collectors_warns_when_interval_missing(caplog):
    collector = make_collector(
        source="fronius",
    )

    config = make_config(
        collectors=[
            make_collector_config(
                source="fronius",
                enabled=True,
                interval=None,
            ),
        ],
    )

    with caplog.at_level("WARNING"):
        asyncio.run(
            configure_collectors(
                [collector],
                config,
            ),
        )

    assert ("No interval configured for collector Mock (source=fronius)") in caplog.text

    collector.configure_runtime.assert_not_called()


def test_configure_databases_enables_database_and_connects():
    database = make_database(
        database_type="influxdb",
        enabled=False,
    )

    config = make_config(
        databases=[
            make_database_config(
                database_type="influxdb",
                enabled=True,
            ),
        ],
    )

    asyncio.run(
        configure_databases(
            [database],
            config,
        ),
    )

    database.configure_runtime.assert_called_once_with(
        enabled=True,
    )
    database.connect.assert_called_once()


def test_configure_databases_disables_database_and_closes():
    database = make_database(
        database_type="influxdb",
        enabled=True,
    )

    config = make_config(
        databases=[
            make_database_config(
                database_type="influxdb",
                enabled=False,
            ),
        ],
    )

    asyncio.run(
        configure_databases(
            [database],
            config,
        ),
    )

    database.configure_runtime.assert_called_once_with(
        enabled=False,
    )
    database.close.assert_called_once()


def test_configure_databases_keeps_database_enabled_when_connect_fails(
    caplog,
):
    database = make_database(
        database_type="influxdb",
        enabled=False,
    )

    database.connect.side_effect = RuntimeError(
        "connection failed",
    )

    config = make_config(
        databases=[
            make_database_config(
                database_type="influxdb",
                enabled=True,
            ),
        ],
    )

    with caplog.at_level("ERROR"):
        asyncio.run(
            configure_databases(
                [database],
                config,
            ),
        )

    database.configure_runtime.assert_called_once_with(
        enabled=True,
    )
    database.connect.assert_called_once()

    assert "Failed to connect database Mock (type=influxdb)" in caplog.text
    assert "connection failed" in caplog.text


def test_configure_databases_warns_when_configuration_missing(caplog):
    database = make_database(
        database_type="influxdb",
        enabled=True,
    )

    config = make_config(
        databases=[],
    )

    with caplog.at_level("WARNING"):
        asyncio.run(
            configure_databases(
                [database],
                config,
            ),
        )

    assert ("No configuration found for database Mock (type=influxdb)") in caplog.text

    database.configure_runtime.assert_not_called()
    database.connect.assert_not_called()
    database.close.assert_not_called()
