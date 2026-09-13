import asyncio
from unittest.mock import Mock

from src.manager.connection_lifecycle import (
    connect_collectors,
    connect_databases,
    disconnect_collectors,
    disconnect_databases,
)

import serial


def make_collector(
    *,
    source="test",
    enabled=True,
):
    collector = Mock()
    collector.source = source
    collector.enabled = enabled
    return collector


def make_database(
    *,
    database_type="test",
    enabled=True,
):
    database = Mock()
    database.type = database_type
    database.enabled = enabled
    return database


def test_connect_collectors_connects_all_collectors():
    collector1 = make_collector(source="fronius")
    collector2 = make_collector(source="iec")

    asyncio.run(
        connect_collectors(
            [collector1, collector2],
        ),
    )

    collector1.connect.assert_called_once()
    collector2.connect.assert_called_once()


def test_connect_collectors_logs_serial_error(caplog):
    collector = make_collector()

    collector.connect.side_effect = serial.SerialException(
        "serial connection failed",
    )

    with caplog.at_level("WARNING"):
        asyncio.run(
            connect_collectors([collector]),
        )

    assert "Collector Mock unavailable" in caplog.text
    assert "serial connection failed" in caplog.text


def test_connect_collectors_logs_unexpected_error(caplog):
    collector = make_collector()

    collector.connect.side_effect = RuntimeError(
        "unexpected connection failure",
    )

    with caplog.at_level("ERROR"):
        asyncio.run(
            connect_collectors([collector]),
        )

    assert "Collector Mock failed" in caplog.text
    assert "unexpected connection failure" in caplog.text


def test_disconnect_collectors_disconnects_all_collectors():
    collector1 = make_collector(source="fronius")
    collector2 = make_collector(source="iec")

    asyncio.run(
        disconnect_collectors(
            [collector1, collector2],
        ),
    )

    collector1.disconnect.assert_called_once()
    collector2.disconnect.assert_called_once()


def test_disconnect_collectors_logs_error(caplog):
    collector = make_collector()

    collector.disconnect.side_effect = RuntimeError(
        "disconnect failed",
    )

    with caplog.at_level("ERROR"):
        asyncio.run(
            disconnect_collectors([collector]),
        )

    assert "Failed to disconnect collector Mock" in caplog.text
    assert "disconnect failed" in caplog.text


def test_connect_databases_connects_enabled_databases():
    database1 = make_database(
        database_type="influxdb",
        enabled=True,
    )
    database2 = make_database(
        database_type="text_file",
        enabled=True,
    )

    asyncio.run(
        connect_databases(
            [database1, database2],
        ),
    )

    database1.connect.assert_called_once()
    database2.connect.assert_called_once()


def test_connect_databases_skips_disabled_databases():
    enabled_database = make_database(
        database_type="influxdb",
        enabled=True,
    )
    disabled_database = make_database(
        database_type="text_file",
        enabled=False,
    )

    asyncio.run(
        connect_databases(
            [enabled_database, disabled_database],
        ),
    )

    enabled_database.connect.assert_called_once()
    disabled_database.connect.assert_not_called()


def test_connect_databases_logs_error(caplog):
    database = make_database(
        database_type="influxdb",
        enabled=True,
    )

    database.connect.side_effect = RuntimeError(
        "database connection failed",
    )

    with caplog.at_level("ERROR"):
        asyncio.run(
            connect_databases([database]),
        )

    assert "Failed to connect database Mock (type=influxdb)" in caplog.text
    assert "database connection failed" in caplog.text


def test_disconnect_databases_closes_enabled_databases():
    database1 = make_database(
        database_type="influxdb",
        enabled=True,
    )
    database2 = make_database(
        database_type="text_file",
        enabled=True,
    )

    asyncio.run(
        disconnect_databases(
            [database1, database2],
        ),
    )

    database1.close.assert_called_once()
    database2.close.assert_called_once()


def test_disconnect_databases_skips_disabled_databases():
    enabled_database = make_database(
        database_type="influxdb",
        enabled=True,
    )
    disabled_database = make_database(
        database_type="text_file",
        enabled=False,
    )

    asyncio.run(
        disconnect_databases(
            [enabled_database, disabled_database],
        ),
    )

    enabled_database.close.assert_called_once()
    disabled_database.close.assert_not_called()


def test_disconnect_databases_logs_error(caplog):
    database = make_database(
        database_type="influxdb",
        enabled=True,
    )

    database.close.side_effect = RuntimeError(
        "database close failed",
    )

    with caplog.at_level("ERROR"):
        asyncio.run(
            disconnect_databases([database]),
        )

    assert "Failed to disconnect database Mock (type=influxdb)" in caplog.text
    assert "database close failed" in caplog.text
