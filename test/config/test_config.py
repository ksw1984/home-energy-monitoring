from unittest.mock import patch

from src.config.config import (
    CollectionConfig,
    ComponentConfig,
    Config,
    required_secret,
    StorageConfig,
    StorageMeasurementConfig,
)

import pytest

# ============================================================================
# required_secret
# ============================================================================


def test_required_secret_returns_environment_value():
    with patch.dict("os.environ", {"TEST_SECRET": "secret-value"}):
        assert required_secret("TEST_SECRET") == "secret-value"


def test_required_secret_raises_when_missing():
    with (
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(
            RuntimeError,
            match="Required secret 'TEST_SECRET' is missing",
        ),
    ):
        required_secret("TEST_SECRET")


def test_required_secret_raises_when_empty():
    with (
        patch.dict("os.environ", {"TEST_SECRET": ""}),
        pytest.raises(
            RuntimeError,
            match="Required secret 'TEST_SECRET' is missing",
        ),
    ):
        required_secret("TEST_SECRET")


def test_storage_measurement_config_storage_key():
    measurement = StorageMeasurementConfig(
        source="open_meteo",
        metric="temperature",
        measurement_type="forecast",
    )

    assert measurement.storage_key == (
        "open_meteo",
        "temperature",
        "forecast",
    )


# ============================================================================
# Config
# ============================================================================


def test_config_dataclass():
    collectors = [
        ComponentConfig(
            type="fronius",
            enabled=True,
            interval=30,
            attributes={
                "ip": "192.168.178.25",
                "latitude": 52.4567,
                "longitude": 13.7213,
            },
        ),
    ]

    databases = [
        ComponentConfig(
            type="influxdb",
            enabled=True,
            interval=None,
            attributes={
                "url": "http://localhost:8086",
                "token": "token",
                "org": "home-energy",
                "bucket": "energy",
            },
        ),
    ]

    storage = StorageConfig(
        enabled=True,
        measurements=[
            StorageMeasurementConfig(
                source="fronius",
                metric="pv_power",
            ),
        ],
    )

    config = Config(
        collection=CollectionConfig(
            interval=10,
            timezone="Europe/Berlin",
        ),
        collectors=collectors,
        databases=databases,
        storage=storage,
    )

    assert config.collection.interval == 10
    assert config.collection.timezone == "Europe/Berlin"

    assert config.collectors == collectors
    assert config.databases == databases
    assert config.storage == storage

    assert config.collectors[0].type == "fronius"
    assert config.collectors[0].interval == 30
    assert config.collectors[0].attributes["latitude"] == 52.4567

    assert config.databases[0].type == "influxdb"
    assert config.databases[0].interval is None
    assert config.databases[0].attributes["token"] == "token"

    assert config.storage.enabled is True
    assert config.storage.measurements[0].source == "fronius"
    assert config.storage.measurements[0].metric == "pv_power"
    assert config.storage.measurements[0].measurement_type == "current"
