import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

import src.app


@pytest.fixture
def mocked_config():
    config = Mock()
    config.power_meter_grid_ir_device0 = "/dev/ttyUSB0"
    config.power_meter_household_ir_device1 = "/dev/ttyUSB1"
    config.fronius_ip = "192.168.178.25"
    config.latitude = 52.4567
    config.longitude = 13.7213
    config.smart_home_box_ip = "192.168.178.19"
    config.umweltsensor_9475_device_id = "50"
    config.influxdb_url = "http://localhost:8086"
    config.influxdb_token = "test-token"
    config.influxdb_org = "home-energy"
    config.influxdb_bucket = "energy"
    config.collection_interval = 5
    return config


def test_run_creates_collectors_and_runs_manager(mocked_config):
    mock_fronius = Mock()
    mock_env_sensor = Mock()
    mock_weather = Mock()
    mock_database = Mock()
    mock_manager = Mock()
    mock_manager.run = AsyncMock()

    with (
        patch.object(src.app, "config_obj", mocked_config),
        patch.object(src.app, "FroniusSymoInverterCollector", return_value=mock_fronius) as fronius_cls,
        patch.object(
            src.app,
            "RademacherEnvironmentSensorCollector",
            return_value=mock_env_sensor,
        ) as env_sensor_cls,
        patch.object(src.app, "OpenMeteoWeatherCollector", return_value=mock_weather) as weather_cls,
        patch.object(src.app, "InfluxDatabase", return_value=mock_database) as database_cls,
        patch.object(src.app, "CollectorManager", return_value=mock_manager) as manager_cls,
        patch.object(src.app, "IecCollector") as iec_cls,
    ):
        asyncio.run(src.app.run())

    fronius_cls.assert_called_once_with(
        inverter_ip="192.168.178.25",
        pv_latitude=52.4567,
        pv_longitude=13.7213,
    )

    env_sensor_cls.assert_called_once_with(
        smart_home_box_ip="192.168.178.19",
        device_id="50",
    )

    weather_cls.assert_called_once_with(
        latitude=52.4567,
        longitude=13.7213,
    )

    database_cls.assert_called_once_with(
        url="http://localhost:8086",
        token="test-token",
        org="home-energy",
        bucket="energy",
    )

    manager_cls.assert_called_once_with(
        collectors=[
            mock_fronius,
            mock_env_sensor,
            mock_weather,
        ],
        database=mock_database,
        interval=5,
    )

    mock_manager.run.assert_awaited_once()
    mock_database.close.assert_called_once()

    assert iec_cls.call_count == 2


def test_run_closes_database_when_manager_fails(mocked_config):
    mock_database = Mock()
    mock_manager = Mock()

    error = RuntimeError("manager failed")
    mock_manager.run = AsyncMock(side_effect=error)

    with (
        patch.object(src.app, "config_obj", mocked_config),
        patch.object(src.app, "IecCollector"),
        patch.object(src.app, "FroniusSymoInverterCollector"),
        patch.object(src.app, "RademacherEnvironmentSensorCollector"),
        patch.object(src.app, "OpenMeteoWeatherCollector"),
        patch.object(src.app, "InfluxDatabase", return_value=mock_database),
        patch.object(src.app, "CollectorManager", return_value=mock_manager),
        pytest.raises(RuntimeError, match="manager failed"),
    ):
        asyncio.run(src.app.run())

    mock_database.close.assert_called_once()


def test_main_calls_asyncio_run():
    with patch.object(src.app.asyncio, "run") as asyncio_run:
        src.app.main()

    asyncio_run.assert_called_once()

    coroutine = asyncio_run.call_args.args[0]

    assert asyncio.iscoroutine(coroutine)
    coroutine.close()


def test_module_entry_point():
    # The actual ``if __name__ == "__main__"`` block is normally not
    # interesting to test through imported Python code.
    # ``main()`` is already tested separately.
    assert callable(src.app.main)
