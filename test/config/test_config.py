from unittest.mock import patch

from src.config.config import (
    _load_bems_config,
    CollectionConfig,
    ComponentConfig,
    Config,
    load_component_configs,
    load_estimator_configs,
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
# Loaders
# ============================================================================
def test_load_estimator_config():
    config_data = {
        "estimators": [
            {
                "type": "kalman",
                "enabled": True,
                "lookback": 7,
            }
        ]
    }

    result = load_estimator_configs(config_data)

    assert len(result) == 1
    assert result[0].type == "kalman"
    assert result[0].enabled is True
    assert result[0].lookback == 7


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
            timezone="Europe/Berlin",
        ),
        collectors=collectors,
        databases=databases,
        estimators=[],
        calculators=[],
        storage=storage,
    )

    assert config.collection.timezone == "Europe/Berlin"

    assert config.collectors == collectors
    assert config.databases == databases
    assert config.storage == storage

    assert config.collectors[0].type == "fronius"
    assert config.collectors[0].enabled is True
    assert config.collectors[0].interval == 30
    assert config.collectors[0].attributes["latitude"] == 52.4567

    assert config.databases[0].type == "influxdb"
    assert config.databases[0].enabled is True
    assert config.databases[0].interval is None
    assert config.databases[0].attributes["token"] == "token"

    assert config.storage.enabled is True
    assert config.storage.measurements[0].source == "fronius"
    assert config.storage.measurements[0].metric == "pv_power"
    assert config.storage.measurements[0].measurement_type == "current"


# ============================================================================
# BEMS control feed
# ============================================================================


def test_bems_control_feed_defaults_to_bus():
    config = _load_bems_config({})

    assert config.control.feed == "bus"


def test_bems_control_accepts_database_feed():
    config = _load_bems_config(
        {
            "databases": [{"type": "influxdb"}],
            "bems": {"control": {"feed": "database"}},
        }
    )

    assert config.control.feed == "database"


def test_bems_control_rejects_unknown_feed():
    with pytest.raises(ValueError, match="feed"):
        _load_bems_config(
            {
                "databases": [{"type": "influxdb"}],
                "bems": {"control": {"feed": "mqtt"}},
            }
        )


def test_bems_control_command_log_defaults_to_none():
    config = _load_bems_config({})

    assert config.control.command_log_csv is None


def test_bems_control_accepts_command_log_path():
    config = _load_bems_config(
        {
            "databases": [{"type": "influxdb"}],
            "bems": {"control": {"command_log_csv": "data/commands.csv"}},
        }
    )

    assert config.control.command_log_csv == "data/commands.csv"


@pytest.mark.parametrize("value", ["", "   ", 5])
def test_bems_control_rejects_invalid_command_log_path(value):
    with pytest.raises(ValueError, match="command_log_csv"):
        _load_bems_config(
            {
                "databases": [{"type": "influxdb"}],
                "bems": {"control": {"command_log_csv": value}},
            }
        )


def test_bems_control_live_age_defaults_to_three_seconds():
    config = _load_bems_config({})

    assert config.control.live_max_measurement_age_s == 3.0


def test_bems_control_accepts_explicit_live_age():
    config = _load_bems_config(
        {
            "databases": [{"type": "influxdb"}],
            "bems": {"control": {"live_max_measurement_age_s": 1.5}},
        }
    )

    assert config.control.live_max_measurement_age_s == 1.5


def test_bems_control_rejects_non_positive_live_age():
    with pytest.raises(ValueError, match="live_max_measurement_age_s"):
        _load_bems_config(
            {
                "databases": [{"type": "influxdb"}],
                "bems": {"control": {"live_max_measurement_age_s": 0}},
            }
        )


def test_component_config_accepts_fractional_interval():
    configs = load_component_configs(
        {
            "collectors": [
                {
                    "type": "landys_gyr_zmd310_meter_dlms",
                    "interval": 0.5,
                }
            ]
        },
        "collectors",
    )

    assert configs[0].interval == 0.5
    assert isinstance(configs[0].interval, float)


def test_component_config_defaults_storage_interval_to_none():
    configs = load_component_configs(
        {
            "collectors": [
                {
                    "type": "landys_gyr_zmd310_meter_dlms",
                    "interval": 1,
                }
            ]
        },
        "collectors",
    )

    assert configs[0].storage_interval is None


def test_component_config_parses_integer_storage_interval():
    configs = load_component_configs(
        {
            "collectors": [
                {
                    "type": "landys_gyr_zmd310_meter_dlms",
                    "interval": 1,
                    "storage_interval": 5,
                }
            ]
        },
        "collectors",
    )

    assert configs[0].storage_interval == 5
    assert isinstance(configs[0].storage_interval, int)


def test_component_config_accepts_integral_float_storage_interval():
    configs = load_component_configs(
        {
            "collectors": [
                {
                    "type": "landys_gyr_zmd310_meter_dlms",
                    "interval": 1,
                    "storage_interval": 5.0,
                }
            ]
        },
        "collectors",
    )

    assert configs[0].storage_interval == 5
    assert isinstance(configs[0].storage_interval, int)


@pytest.mark.parametrize("storage_interval", [0, -1, 5.5, True])
def test_component_config_rejects_invalid_storage_interval(storage_interval):
    with pytest.raises((TypeError, ValueError), match="storage_interval"):
        load_component_configs(
            {
                "collectors": [
                    {
                        "type": "landys_gyr_zmd310_meter_dlms",
                        "interval": 1,
                        "storage_interval": storage_interval,
                    }
                ]
            },
            "collectors",
        )
