from unittest.mock import Mock, patch

import pytest

from src.collectors.collector_factory import create_collectors


def make_collector_config(
    collector_type,
    attributes=None,
    enabled=True,
):
    config = Mock()
    config.type = collector_type
    config.attributes = attributes or {}
    config.enabled = enabled
    return config


def make_config(*collectors):
    config = Mock()
    config.collectors = list(collectors)
    return config


def test_create_collectors_returns_empty_list_when_no_collectors_are_configured():
    config = make_config()

    result = create_collectors(config)

    assert result == []


def test_create_collectors_skips_disabled_collector():
    config = make_config(
        make_collector_config(
            "fronius",
            {
                "inverter_ip": "192.168.178.25",
            },
            enabled=False,
        )
    )

    with patch("src.collectors.collector_factory.FroniusSymoInverterCollector") as fronius_cls:
        result = create_collectors(config)

    assert result == []
    fronius_cls.assert_not_called()


def test_create_fronius_collector():
    collector_config = make_collector_config(
        "fronius",
        {
            "inverter_ip": "192.168.178.25",
            "latitude": 52.4567,
            "longitude": 13.7213,
        },
    )
    config = make_config(collector_config)

    with patch("src.collectors.collector_factory.FroniusSymoInverterCollector") as fronius_cls:
        result = create_collectors(config)

    fronius_cls.assert_called_once_with(
        inverter_ip="192.168.178.25",
        latitude=52.4567,
        longitude=13.7213,
    )
    assert result == [fronius_cls.return_value]


def test_create_iec_collector():
    collector_config = make_collector_config(
        "iec",
        {
            "device": "/dev/ttyUSB0",
        },
    )
    config = make_config(collector_config)

    with patch("src.collectors.collector_factory.IecCollector") as iec_cls:
        result = create_collectors(config)

    iec_cls.assert_called_once_with(
        device="/dev/ttyUSB0",
    )
    assert result == [iec_cls.return_value]


def test_create_environment_collector():
    collector_config = make_collector_config(
        "environment",
        {
            "smart_home_box_ip": "192.168.178.19",
            "device_id": "50",
        },
    )
    config = make_config(collector_config)

    with patch("src.collectors.collector_factory.RademacherEnvironmentSensorCollector") as environment_cls:
        result = create_collectors(config)

    environment_cls.assert_called_once_with(
        smart_home_box_ip="192.168.178.19",
        device_id="50",
    )
    assert result == [environment_cls.return_value]


def test_create_weather_collector():
    collector_config = make_collector_config(
        "weather",
        {
            "latitude": 52.4567,
            "longitude": 13.7213,
        },
    )
    config = make_config(collector_config)

    with patch("src.collectors.collector_factory.OpenMeteoWeatherCollector") as weather_cls:
        result = create_collectors(config)

    weather_cls.assert_called_once_with(
        latitude=52.4567,
        longitude=13.7213,
    )
    assert result == [weather_cls.return_value]


def test_create_collectors_creates_all_enabled_types():
    config = make_config(
        make_collector_config(
            "fronius",
            {
                "inverter_ip": "192.168.178.25",
                "latitude": 52.4567,
                "longitude": 13.7213,
            },
        ),
        make_collector_config(
            "iec",
            {
                "device": "/dev/ttyUSB0",
            },
        ),
        make_collector_config(
            "environment",
            {
                "smart_home_box_ip": "192.168.178.19",
                "device_id": "50",
            },
        ),
        make_collector_config(
            "weather",
            {
                "latitude": 52.4567,
                "longitude": 13.7213,
            },
        ),
    )

    with (
        patch("src.collectors.collector_factory.FroniusSymoInverterCollector") as fronius_cls,
        patch("src.collectors.collector_factory.IecCollector") as iec_cls,
        patch("src.collectors.collector_factory.RademacherEnvironmentSensorCollector") as environment_cls,
        patch("src.collectors.collector_factory.OpenMeteoWeatherCollector") as weather_cls,
    ):
        result = create_collectors(config)

    assert result == [
        fronius_cls.return_value,
        iec_cls.return_value,
        environment_cls.return_value,
        weather_cls.return_value,
    ]

    fronius_cls.assert_called_once()
    iec_cls.assert_called_once()
    environment_cls.assert_called_once()
    weather_cls.assert_called_once()


def test_create_collectors_skips_disabled_collectors():
    config = make_config(
        make_collector_config(
            "fronius",
            {
                "inverter_ip": "192.168.178.25",
            },
        ),
        make_collector_config(
            "iec",
            {
                "device": "/dev/ttyUSB0",
            },
            enabled=False,
        ),
        make_collector_config(
            "weather",
            {
                "latitude": 52.4567,
                "longitude": 13.7213,
            },
        ),
    )

    with (
        patch("src.collectors.collector_factory.FroniusSymoInverterCollector") as fronius_cls,
        patch("src.collectors.collector_factory.IecCollector") as iec_cls,
        patch("src.collectors.collector_factory.OpenMeteoWeatherCollector") as weather_cls,
    ):
        result = create_collectors(config)

    assert result == [
        fronius_cls.return_value,
        weather_cls.return_value,
    ]

    fronius_cls.assert_called_once()
    iec_cls.assert_not_called()
    weather_cls.assert_called_once()


def test_create_collectors_raises_for_unknown_type():
    config = make_config(
        make_collector_config(
            "unknown",
            {},
        )
    )

    with pytest.raises(
        ValueError,
        match="Unknown collector type: unknown",
    ):
        create_collectors(config)
