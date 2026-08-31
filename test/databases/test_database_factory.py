from unittest.mock import Mock, patch

import pytest

from src.databases.database_factory import create_databases


def make_database_config(
    database_type,
    attributes=None,
    enabled=True,
):
    config = Mock()
    config.type = database_type
    config.attributes = attributes or {}
    config.enabled = enabled
    return config


def make_config(*databases):
    config = Mock()
    config.databases = list(databases)
    return config


def test_create_databases_returns_empty_list_when_no_databases_are_configured():
    config = make_config()

    result = create_databases(config)

    assert result == []


def test_create_databases_skips_disabled_database():
    config = make_config(
        make_database_config(
            "influxdb",
            {
                "url": "http://localhost:8086",
                "org": "home-energy",
                "bucket": "energy",
            },
            enabled=False,
        )
    )

    with patch("src.databases.database_factory.InfluxDatabase") as influx_cls:
        result = create_databases(config)

    assert result == []
    influx_cls.assert_not_called()


def test_create_influxdb_database():
    config = make_config(
        make_database_config(
            "influxdb",
            {
                "url": "http://localhost:8086",
                "org": "home-energy",
                "bucket": "energy",
            },
        )
    )

    with (
        patch("src.databases.database_factory.InfluxDatabase") as influx_cls,
        patch(
            "src.databases.database_factory.required_secret",
            return_value="test-token",
        ) as required_secret_mock,
    ):
        result = create_databases(config)

    required_secret_mock.assert_called_once_with("INFLUXDB_TOKEN")

    influx_cls.assert_called_once_with(
        url="http://localhost:8086",
        token="test-token",
        org="home-energy",
        bucket="energy",
    )

    assert result == [influx_cls.return_value]


def test_create_databases_raises_for_unknown_type():
    config = make_config(
        make_database_config(
            "unknown",
            {},
        )
    )

    with pytest.raises(
        ValueError,
        match="Unknown database type: unknown",
    ):
        create_databases(config)


def test_create_text_file_database():
    config = make_config(
        make_database_config(
            "text_file",
            {
                "directory": "/data/backup",
            },
        )
    )

    with patch("src.databases.database_factory.TextFileDatabase") as text_file_cls:
        result = create_databases(config)

    text_file_cls.assert_called_once_with(
        directory="/data/backup",
    )

    assert result == [text_file_cls.return_value]


def test_create_databases_skips_disabled_text_file_database():
    config = make_config(
        make_database_config(
            "text_file",
            {
                "directory": "/data/backup",
            },
            enabled=False,
        )
    )

    with patch("src.databases.database_factory.TextFileDatabase") as text_file_cls:
        result = create_databases(config)

    assert result == []
    text_file_cls.assert_not_called()


def test_create_databases_creates_multiple_database_types():
    config = make_config(
        make_database_config(
            "influxdb",
            {
                "url": "http://localhost:8086",
                "org": "home-energy",
                "bucket": "energy",
            },
        ),
        make_database_config(
            "text_file",
            {
                "directory": "/data/backup",
            },
        ),
    )

    with (
        patch("src.databases.database_factory.InfluxDatabase") as influx_cls,
        patch("src.databases.database_factory.TextFileDatabase") as text_file_cls,
        patch(
            "src.databases.database_factory.required_secret",
            return_value="test-token",
        ),
    ):
        result = create_databases(config)

    influx_cls.assert_called_once_with(
        url="http://localhost:8086",
        token="test-token",
        org="home-energy",
        bucket="energy",
    )

    text_file_cls.assert_called_once_with(
        directory="/data/backup",
    )

    assert result == [
        influx_cls.return_value,
        text_file_cls.return_value,
    ]


def test_create_databases_only_creates_enabled_databases():
    config = make_config(
        make_database_config(
            "influxdb",
            {
                "url": "http://localhost:8086",
                "org": "home-energy",
                "bucket": "energy",
            },
        ),
        make_database_config(
            "text_file",
            {
                "directory": "/data/backup",
            },
            enabled=False,
        ),
    )

    with (
        patch("src.databases.database_factory.InfluxDatabase") as influx_cls,
        patch("src.databases.database_factory.TextFileDatabase") as text_file_cls,
        patch(
            "src.databases.database_factory.required_secret",
            return_value="test-token",
        ),
    ):
        result = create_databases(config)

    assert result == [influx_cls.return_value]

    influx_cls.assert_called_once()
    text_file_cls.assert_not_called()
