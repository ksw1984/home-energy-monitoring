import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch

from src.collectors.definitions.measurement import Measurement
from src.database.influxdb import InfluxDatabase


def make_measurement(
    metric="temperature",
    source="test",
    value=20.5,
):
    return Measurement(
        timestamp=datetime(2026, 8, 27, 12, 0),
        source=source,
        metric=metric,
        value=value,
        unit="°C",
    )


def test_init_creates_influxdb_client():
    client = MagicMock()
    write_api = MagicMock()
    client.write_api.return_value = write_api

    with patch(
        "src.database.influxdb.InfluxDBClient",
        return_value=client,
    ) as influx_client:

        database = InfluxDatabase(
            url="http://localhost:8086",
            token="test-token",
            org="home-energy",
            bucket="energy",
        )

    influx_client.assert_called_once_with(
        url="http://localhost:8086",
        token="test-token",
        org="home-energy",
    )

    client.write_api.assert_called_once()

    assert database.bucket == "energy"
    assert database.org == "home-energy"
    assert database.client is client
    assert database.write_api is write_api


def test_store_writes_measurements():
    client = MagicMock()
    write_api = MagicMock()
    client.write_api.return_value = write_api

    with patch(
        "src.database.influxdb.InfluxDBClient",
        return_value=client,
    ):
        database = InfluxDatabase(
            url="http://localhost:8086",
            token="test-token",
            org="home-energy",
            bucket="energy",
        )

    measurements = [
        make_measurement("temperature", "rademacher", 20.5),
        make_measurement("humidity", "weather", 60.0),
    ]

    asyncio.run(database.store(measurements))

    write_api.write.assert_called_once()

    points = write_api.write.call_args.kwargs["record"]

    assert len(points) == 2

    assert points[0].to_line_protocol().startswith("temperature,source=rademacher value=20.5")

    assert points[1].to_line_protocol().startswith("humidity,source=weather value=60")


def test_store_with_empty_measurements_writes_empty_list():
    client = MagicMock()
    write_api = MagicMock()
    client.write_api.return_value = write_api

    with patch(
        "src.database.influxdb.InfluxDBClient",
        return_value=client,
    ):
        database = InfluxDatabase(
            url="http://localhost:8086",
            token="test-token",
            org="home-energy",
            bucket="energy",
        )

    asyncio.run(database.store([]))

    write_api.write.assert_called_once_with(
        bucket="energy",
        org="home-energy",
        record=[],
    )


def test_close_closes_client():
    client = MagicMock()
    write_api = MagicMock()
    client.write_api.return_value = write_api

    with patch(
        "src.database.influxdb.InfluxDBClient",
        return_value=client,
    ):
        database = InfluxDatabase(
            url="http://localhost:8086",
            token="test-token",
            org="home-energy",
            bucket="energy",
        )

    database.close()

    client.close.assert_called_once()
