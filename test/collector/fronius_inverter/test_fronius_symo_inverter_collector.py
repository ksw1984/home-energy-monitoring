from datetime import datetime

import pytest
import requests
from unittest.mock import Mock, patch

from src.collector.definitions.measurement import Measurement
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


@pytest.fixture
def mock_get():
    with patch(
        "src.collector.fronius_inverter.fronius_symo_inverter_collector.requests.get"
    ) as mock:
        yield mock


def test_collect_returns_measurements(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    assert isinstance(result, list)
    assert len(result) == 4

    assert all(isinstance(measurement, Measurement) for measurement in result)


def test_collect_returns_expected_metrics(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    metrics = {measurement.metric: measurement for measurement in result}

    assert metrics["pv_power"].value == 10924
    assert metrics["pv_energy_day"].value == 32880
    assert metrics["pv_energy_year"].value == 13947576
    assert metrics["pv_energy_total"].value == 90416904


def test_collect_returns_expected_units(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    units = {measurement.metric: measurement.unit for measurement in result}

    assert units["pv_power"] == "W"
    assert units["pv_energy_day"] == "Wh"
    assert units["pv_energy_year"] == "Wh"
    assert units["pv_energy_total"] == "Wh"


def test_collect_returns_expected_source(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    assert all(measurement.source == "fronius" for measurement in result)


def test_collect_returns_timestamp(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    expected_timestamp = datetime.fromisoformat("2026-08-15T11:43:28+02:00")

    assert all(measurement.timestamp == expected_timestamp for measurement in result)


def test_collect_timestamp_has_timezone(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    assert all(measurement.timestamp.tzinfo is not None for measurement in result)

    assert all(
        measurement.timestamp.utcoffset().total_seconds() == 2 * 60 * 60
        for measurement in result
    )


def test_collect_current_power_watt(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect_current_power_watt()

    assert result == 10924


def test_collect_current_power_uses_collect(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect_current_power_watt()

    assert result == 10924

    mock_get.assert_called_once_with(
        "http://192.168.178.25/solar_api/v1/GetPowerFlowRealtimeData.fcgi",
        timeout=5,
    )


def test_http_error_is_propagated(
    collector,
    mock_response,
    mock_get,
):
    mock_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

    mock_get.return_value = mock_response

    with pytest.raises(requests.HTTPError):
        collector.collect()


def test_fronius_api_error_is_detected(
    collector,
    mock_response,
    mock_get,
):
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
    mock_get.return_value = mock_response

    with pytest.raises(
        RuntimeError,
        match="Something went wrong",
    ):
        collector.collect()


def test_collect_measurements_have_no_obis(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    assert all(measurement.obis is None for measurement in result)
