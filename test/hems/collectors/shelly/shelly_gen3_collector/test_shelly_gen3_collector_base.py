from __future__ import annotations

import asyncio
from datetime import datetime, UTC
from unittest.mock import AsyncMock, Mock, patch

from src.hems.collectors.shelly.shelly_gen3_collector.shelly_gen3_collector_base import (
    ShellyGen3Collector,
)
from src.shared.measurement import Measurement

import httpx
import pytest


class TestShellyCollector(ShellyGen3Collector):
    @property
    def rpc_method(self) -> str:
        return "Test.GetStatus"


def make_collector(**kwargs) -> TestShellyCollector:
    return TestShellyCollector(
        timezone="UTC",
        interval=10,
        source="test_shelly",
        ip="192.168.178.200",
        timeout=2.0,
        **kwargs,
    )


def test_collect_returns_measurements():
    collector = make_collector()

    timestamp = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    status = {
        "id": 0,
        "voltage": 237.9,
        "current": 2.5,
        "apower": 500.0,
        "aenergy": {
            "total": 12345.0,
        },
        "ret_aenergy": {
            "total": 678.0,
        },
    }

    with (
        patch.object(collector, "_get_status", new_callable=AsyncMock, return_value=status),
        patch.object(collector, "now", return_value=timestamp),
    ):
        result = collector.collect()

    assert result == [
        Measurement(
            timestamp=timestamp,
            source="test_shelly",
            metric="power",
            value=0.5,
            unit="kW",
        ),
        Measurement(
            timestamp=timestamp,
            source="test_shelly",
            metric="voltage",
            value=237.9,
            unit="V",
        ),
        Measurement(
            timestamp=timestamp,
            source="test_shelly",
            metric="current",
            value=2.5,
            unit="A",
        ),
        Measurement(
            timestamp=timestamp,
            source="test_shelly",
            metric="energy_total",
            value=12.345,
            unit="kWh",
        ),
        Measurement(
            timestamp=timestamp,
            source="test_shelly",
            metric="returned_energy_total",
            value=0.678,
            unit="kWh",
        ),
    ]


def test_collect_works_without_returned_energy():
    collector = make_collector()

    status = {
        "voltage": 230.0,
        "current": 1.0,
        "apower": 100.0,
        "aenergy": {
            "total": 1000.0,
        },
    }

    with (
        patch.object(collector, "_get_status", new_callable=AsyncMock, return_value=status),
        patch.object(
            collector,
            "now",
            return_value=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    ):
        result = collector.collect()

    assert len(result) == 4

    metrics = {measurement.metric: measurement.value for measurement in result}

    assert metrics == {
        "power": 0.1,
        "voltage": 230.0,
        "current": 1.0,
        "energy_total": 1.0,
    }


def test_collect_returns_empty_list_when_disabled():
    collector = make_collector(enabled=False)

    with patch.object(collector, "_get_status", new_callable=AsyncMock) as get_status:
        result = collector.collect()

    assert result == []
    get_status.assert_not_awaited()


def test_get_status_calls_rpc_endpoint():
    collector = make_collector()

    response = Mock()
    response.json.return_value = {
        "id": 0,
        "apower": 123,
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=response) as get:
        result = asyncio.run(collector._get_status())

    get.assert_awaited_once_with(
        "http://192.168.178.200/rpc/Test.GetStatus",
        params={"id": 0},
    )
    response.raise_for_status.assert_called_once_with()
    assert result == {
        "id": 0,
        "apower": 123,
    }


def test_get_status_raises_request_exception():
    collector = make_collector()

    error = httpx.ConnectError("connection refused")

    with (
        patch(
            "httpx.AsyncClient.get",
            new_callable=AsyncMock,
            side_effect=error,
        ),
        pytest.raises(httpx.ConnectError, match="connection refused"),
    ):
        asyncio.run(collector._get_status())


def test_get_status_raises_for_invalid_json():
    collector = make_collector()

    response = Mock()
    response.json.side_effect = ValueError("invalid json")
    response.text = "not-json"

    with (
        patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=response),
        pytest.raises(
            RuntimeError,
            match="Shelly returned invalid JSON",
        ),
    ):
        asyncio.run(collector._get_status())


def test_get_status_raises_for_non_dict_response():
    collector = make_collector()

    response = Mock()
    response.json.return_value = ["unexpected"]

    with (
        patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=response),
        pytest.raises(
            TypeError,
            match="Unexpected Shelly response type: list",
        ),
    ):
        asyncio.run(collector._get_status())


def test_get_status_raises_for_rpc_error():
    collector = make_collector()

    response = Mock()
    response.json.return_value = {
        "error": {
            "code": -103,
            "message": "Unknown method",
        },
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=response), pytest.raises(RuntimeError, match="Shelly RPC error"):
        asyncio.run(collector._get_status())


def test_get_power_kw_converts_watts_to_kw():
    collector = make_collector()

    assert collector._get_power_kw({"apower": 1234.0}) == 1.234


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (123, 123.0),
        (123.45, 123.45),
        (0, 0.0),
        (-123.45, -123.45),
    ],
)
def test_get_float_accepts_numeric_values(value, expected):
    assert ShellyGen3Collector._get_float(value, "test") == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "123",
        [],
        {},
        True,
        False,
    ],
)
def test_get_float_rejects_non_numeric_values(value):
    with pytest.raises(TypeError, match="must be numeric"):
        ShellyGen3Collector._get_float(value, "test")


def test_require_dict_accepts_dict():
    value = {"total": 123}

    assert ShellyGen3Collector._require_dict(value, "test") == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        "invalid",
        123,
        True,
    ],
)
def test_require_dict_rejects_non_dict_values(value):
    with pytest.raises(TypeError, match="must be an object"):
        ShellyGen3Collector._require_dict(value, "test")


def test_connect_does_not_make_network_request():
    collector = make_collector()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get:
        collector.connect()

    get.assert_not_awaited()


def test_connect_does_nothing_when_disabled():
    collector = make_collector(enabled=False)

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get:
        collector.connect()

    get.assert_not_awaited()


def test_disconnect_closes_http_client():
    collector = make_collector()
    client = collector._client()

    with patch.object(client, "aclose", new_callable=AsyncMock) as close:
        asyncio.run(collector.disconnect_async())

    close.assert_awaited_once_with()
    assert collector._http_client is None
