from unittest.mock import Mock, patch

from src.collectors.collector_factory import create_collectors

import pytest


def make_collector_config(
    collector_type,
    attributes=None,
    enabled=True,
    interval=300,
):
    config = Mock()
    config.type = collector_type
    config.attributes = attributes or {}
    config.enabled = enabled
    config.interval = interval
    return config


def make_config(*collectors):
    config = Mock()
    config.collectors = list(collectors)
    return config


def test_create_collectors_returns_empty_list_when_no_collectors_are_configured():
    config = make_config()

    result = create_collectors(config)

    assert result == []


def test_create_collectors_creates_disabled_collector():
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

    assert result == [fronius_cls.return_value]

    fronius_cls.assert_called_once_with(
        timezone=config.collection.timezone,
        interval=300,
        enabled=False,
        inverter_ip="192.168.178.25",
    )


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
        timezone=config.collection.timezone,
        interval=collector_config.interval,
        enabled=True,
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
        timezone=config.collection.timezone,
        interval=collector_config.interval,
        enabled=True,
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
        timezone=config.collection.timezone,
        interval=collector_config.interval,
        enabled=True,
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
        timezone=config.collection.timezone,
        interval=collector_config.interval,
        enabled=True,
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

    fronius_cls.assert_called_once_with(
        timezone=config.collection.timezone,
        interval=300,
        enabled=True,
        inverter_ip="192.168.178.25",
        latitude=52.4567,
        longitude=13.7213,
    )

    iec_cls.assert_called_once_with(
        timezone=config.collection.timezone,
        interval=300,
        enabled=True,
        device="/dev/ttyUSB0",
    )

    environment_cls.assert_called_once_with(
        timezone=config.collection.timezone,
        interval=300,
        enabled=True,
        smart_home_box_ip="192.168.178.19",
        device_id="50",
    )

    weather_cls.assert_called_once_with(
        timezone=config.collection.timezone,
        interval=300,
        enabled=True,
        latitude=52.4567,
        longitude=13.7213,
    )


def test_create_collectors_creates_disabled_collectors():
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
        iec_cls.return_value,
        weather_cls.return_value,
    ]

    fronius_cls.assert_called_once_with(
        timezone=config.collection.timezone,
        interval=300,
        enabled=True,
        inverter_ip="192.168.178.25",
    )

    iec_cls.assert_called_once_with(
        timezone=config.collection.timezone,
        interval=300,
        enabled=False,
        device="/dev/ttyUSB0",
    )

    weather_cls.assert_called_once_with(
        timezone=config.collection.timezone,
        interval=300,
        enabled=True,
        latitude=52.4567,
        longitude=13.7213,
    )


def test_create_collectors_uses_collection_interval_when_collector_interval_is_none():
    collector_config = make_collector_config(
        "fronius",
        {
            "inverter_ip": "192.168.178.25",
        },
        interval=None,
    )

    config = make_config(collector_config)
    config.collection.interval = 60

    with patch("src.collectors.collector_factory.FroniusSymoInverterCollector") as fronius_cls:
        result = create_collectors(config)

    fronius_cls.assert_called_once_with(
        timezone=config.collection.timezone,
        interval=60,
        enabled=True,
        inverter_ip="192.168.178.25",
    )

    assert result == [fronius_cls.return_value]


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
