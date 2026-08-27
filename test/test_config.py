from unittest.mock import patch

import pytest

from src.config import (
    Config,
    optional_config,
    optional_int,
    required_config,
    required_float,
    required_secret,
)

# ============================================================================
# required_config
# ============================================================================


def test_required_config_returns_environment_value():
    with patch.dict("os.environ", {"TEST_CONFIG": "hello"}):
        assert required_config("TEST_CONFIG") == "hello"


def test_required_config_raises_when_missing():
    with (
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(
            RuntimeError,
            match="Required configuration 'TEST_CONFIG' is missing",
        ),
    ):
        required_config("TEST_CONFIG")


def test_required_config_raises_when_empty():
    with (
        patch.dict("os.environ", {"TEST_CONFIG": ""}),
        pytest.raises(
            RuntimeError,
            match="Required configuration 'TEST_CONFIG' is missing",
        ),
    ):
        required_config("TEST_CONFIG")


# ============================================================================
# required_float
# ============================================================================


def test_required_float_returns_float():
    with patch.dict("os.environ", {"TEST_FLOAT": "12.5"}):
        assert required_float("TEST_FLOAT") == 12.5


def test_required_float_raises_for_invalid_value():
    with (
        patch.dict("os.environ", {"TEST_FLOAT": "abc"}),
        pytest.raises(
            RuntimeError,
            match=r"Configuration 'TEST_FLOAT' must be a float, got: 'abc'",
        ),
    ):
        required_float("TEST_FLOAT")


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


# ============================================================================
# optional_config
# ============================================================================


def test_optional_config_returns_environment_value():
    with patch.dict("os.environ", {"TEST_CONFIG": "configured"}):
        assert optional_config("TEST_CONFIG", "default") == "configured"


def test_optional_config_returns_default_when_missing():
    with patch.dict("os.environ", {}, clear=True):
        assert optional_config("TEST_CONFIG", "default") == "default"


def test_optional_config_returns_default_when_empty():
    with patch.dict("os.environ", {"TEST_CONFIG": ""}):
        assert optional_config("TEST_CONFIG", "default") == "default"


# ============================================================================
# optional_int
# ============================================================================


def test_optional_int_returns_configured_integer():
    with patch.dict("os.environ", {"TEST_INTERVAL": "15"}):
        assert optional_int("TEST_INTERVAL", 10) == 15


def test_optional_int_returns_default_when_missing():
    with patch.dict("os.environ", {}, clear=True):
        assert optional_int("TEST_INTERVAL", 10) == 10


def test_optional_int_raises_for_invalid_value():
    with (
        patch.dict("os.environ", {"TEST_INTERVAL": "abc"}),
        pytest.raises(
            RuntimeError,
            match="Configuration 'TEST_INTERVAL' must be an integer",
        ),
    ):
        optional_int("TEST_INTERVAL", 10)


def test_optional_int_accepts_negative_integer():
    with patch.dict("os.environ", {"TEST_INTERVAL": "-5"}):
        assert optional_int("TEST_INTERVAL", 10) == -5


# ============================================================================
# Config
# ============================================================================


def test_config_dataclass():
    config = Config(
        influxdb_token="token",
        latitude=52.4567,
        longitude=13.7213,
        influxdb_url="http://localhost:8086",
        influxdb_org="home-energy",
        influxdb_bucket="energy",
        fronius_ip="192.168.178.25",
        power_meter_grid_ir_device0="/dev/ttyUSB0",
        power_meter_household_ir_device1="/dev/ttyUSB1",
        smart_home_box_ip="192.168.178.19",
        umweltsensor_9475_device_id="50",
        collection_interval=10,
    )

    assert config.latitude == 52.4567
    assert config.longitude == 13.7213
    assert config.influxdb_token == "token"
    assert config.collection_interval == 10
