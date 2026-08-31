from unittest.mock import patch

import pytest

from src.config import (
    CollectionConfig,
    ComponentConfig,
    Config,
    required_secret,
)

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
# Config
# ============================================================================


def test_config_dataclass():
    collectors = [
        ComponentConfig(
            type="fronius",
            enabled=True,
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
            attributes={
                "url": "http://localhost:8086",
                "token": "token",
                "org": "home-energy",
                "bucket": "energy",
            },
        ),
    ]

    config = Config(
        collection=CollectionConfig(
            interval=10,
            timezone="Europe/Berlin",
        ),
        collectors=collectors,
        databases=databases,
    )

    assert config.collection.interval == 10
    assert config.collection.timezone == "Europe/Berlin"

    assert config.collectors == collectors
    assert config.databases == databases

    assert config.collectors[0].type == "fronius"
    assert config.collectors[0].attributes["latitude"] == 52.4567

    assert config.databases[0].type == "influxdb"
    assert config.databases[0].attributes["token"] == "token"
