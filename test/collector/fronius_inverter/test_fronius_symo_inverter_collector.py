from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from src.collector.fronius_inverter.fronius_symo_inverter_collector import (
    FoniusSymoInverterCollector,
)

FRONIUS_RESPONSE = {
    "Body": {
        "Data": {
            "Inverters": {
                "1": {
                    "DT": 114,
                    "E_Day": 32880,
                    "E_Total": 90416904,
                    "E_Year": 13947576,
                    "P": 10924,
                }
            },
            "Site": {
                "E_Day": 32880,
                "E_Total": 90416904,
                "E_Year": 13947576,
                "Meter_Location": "unknown",
                "Mode": "produce-only",
                "P_Akku": None,
                "P_Grid": None,
                "P_Load": None,
                "P_PV": 10924,
                "rel_Autonomy": None,
                "rel_SelfConsumption": None,
            },
            "Version": "12",
        }
    },
    "Head": {
        "RequestArguments": {},
        "Status": {
            "Code": 0,
            "Reason": "",
            "UserMessage": "",
        },
        "Timestamp": "2026-08-15T11:43:28+02:00",
    },
}


@pytest.fixture
def collector():
    return FoniusSymoInverterCollector(
        inverter_ip="192.168.178.25",
    )


@pytest.fixture
def mock_response():
    response = Mock()
    response.json.return_value = FRONIUS_RESPONSE
    response.raise_for_status.return_value = None
    return response


def test_collect_current_power_watt(collector, mock_response):
    with patch(
        "src.collector.fronius_inverter.fronius_symo_inverter_collector.requests.get",
        return_value=mock_response,
    ) as mock_get:
        result = collector.collect_current_power_watt()

    assert result == 10924

    mock_get.assert_called_once_with(
        "http://192.168.178.25/solar_api/v1/GetPowerFlowRealtimeData.fcgi",
        timeout=5,
    )


def test_collect_returns_energy_values(collector, mock_response):
    with patch(
        "src.collector.fronius_inverter.fronius_symo_inverter_collector.requests.get",
        return_value=mock_response,
    ):
        result = collector.collect()

    assert result["power_watt"] == 10924
    assert result["energy_day_wh"] == 32880
    assert result["energy_year_wh"] == 13947576
    assert result["energy_total_wh"] == 90416904


def test_collect_returns_timestamp(collector, mock_response):
    with patch(
        "src.collector.fronius_inverter.fronius_symo_inverter_collector.requests.get",
        return_value=mock_response,
    ):
        result = collector.collect()

    assert result["timestamp"] == datetime.fromisoformat("2026-08-15T11:43:28+02:00")


def test_collect_timestamp_has_timezone(collector, mock_response):
    with patch(
        "src.collector.fronius_inverter.fronius_symo_inverter_collector.requests.get",
        return_value=mock_response,
    ):
        result = collector.collect()

    assert result["timestamp"].tzinfo is not None
    assert result["timestamp"].utcoffset().total_seconds() == 2 * 60 * 60


def test_collect_timestamp_age(collector, mock_response):
    fixed_now = datetime.fromisoformat("2026-08-15T11:43:38+02:00")

    with (
        patch(
            "src.collector.fronius_inverter.fronius_symo_inverter_collector.requests.get",
            return_value=mock_response,
        ),
        patch.object(collector, "_now", return_value=fixed_now),
    ):
        result = collector.collect()

    assert result["timestamp_age_seconds"] == 10


def test_collect_current_power_uses_collect(collector, mock_response):
    with patch(
        "src.collector.fronius_inverter.fronius_symo_inverter_collector.requests.get",
        return_value=mock_response,
    ) as mock_get:
        result = collector.collect_current_power_watt()

    assert result == 10924

    # Only one HTTP request should be made.
    mock_get.assert_called_once_with(
        "http://192.168.178.25/solar_api/v1/GetPowerFlowRealtimeData.fcgi",
        timeout=5,
    )


def test_http_error_is_propagated(collector, mock_response):
    import requests

    mock_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

    with patch(
        "src.collector.fronius_inverter.fronius_symo_inverter_collector.requests.get",
        return_value=mock_response,
    ):
        with pytest.raises(requests.HTTPError):
            collector.collect()


def test_fronius_api_error_is_detected(collector, mock_response):
    error_response = {
        **FRONIUS_RESPONSE,
        "Head": {
            **FRONIUS_RESPONSE["Head"],
            "Status": {
                "Code": 1,
                "Reason": "Something went wrong",
                "UserMessage": "Fronius API error",
            },
        },
    }

    mock_response.json.return_value = error_response

    with patch(
        "src.collector.fronius_inverter.fronius_symo_inverter_collector.requests.get",
        return_value=mock_response,
    ):
        with pytest.raises(RuntimeError, match="Something went wrong"):
            collector.collect()
