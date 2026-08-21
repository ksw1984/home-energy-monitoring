from datetime import datetime

import pytest
import requests
from unittest.mock import Mock, patch

from src.collectors.definitions.measurement import Measurement
from src.collectors.fronius_inverter.fronius_symo_inverter_collector import (
    FroniusSymoInverterCollector,
)

# ============================================================================
# Test data
# ============================================================================

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


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def collector():
    return FroniusSymoInverterCollector(
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
        "src.collectors.fronius_inverter.fronius_symo_inverter_collector.requests.get"
    ) as mock:
        yield mock


# ============================================================================
# Basic collection
#
# Tests that collect() returns the current PV power and does not directly
# record the Fronius energy counters during normal daytime operation.
# ============================================================================


def test_collect_returns_measurements(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    assert isinstance(result, list)
    assert len(result) == 1
    assert all(isinstance(measurement, Measurement) for measurement in result)


def test_collect_returns_only_pv_power(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    assert len(result) == 1
    assert result[0].metric == "pv_power"
    assert result[0].value == 10924
    assert result[0].unit == "W"


# ============================================================================
# Measurement metadata
#
# Tests that generated measurements contain the correct source, unit,
# timestamp and OBIS information.
# ============================================================================


def test_collect_returns_expected_source(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    assert all(measurement.source == "fronius" for measurement in result)


def test_collect_returns_expected_units(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    units = {measurement.metric: measurement.unit for measurement in result}

    assert units["pv_power"] == "W"


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


def test_collect_measurements_have_no_obis(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    assert all(measurement.obis is None for measurement in result)


# ============================================================================
# Day finalization
#
# Tests the logic that determines when the daily Fronius energy counters
# should be recorded:
#
# - only after sunset
# - only after five continuous minutes at 0 W
# - a new production value interrupts the zero-power period
# ============================================================================


def test_zero_power_before_sunset_does_not_finalize_day(
    collector,
):
    timestamp = datetime.fromisoformat("2026-08-15T14:55:00+02:00")

    result = collector._should_finalize_day(
        timestamp=timestamp,
        pv_power=0.0,
    )

    assert result is False


def test_zero_power_after_sunset_does_not_finalize_immediately(
    collector,
):
    collector._is_after_sunset = lambda timestamp: True

    timestamp = datetime.fromisoformat("2026-08-15T21:00:00+02:00")

    result = collector._should_finalize_day(
        timestamp=timestamp,
        pv_power=0.0,
    )

    assert result is False


def test_zero_power_for_five_minutes_after_sunset_finalizes_day(
    collector,
):
    collector._is_after_sunset = lambda timestamp: True

    start = datetime.fromisoformat("2026-08-15T21:00:00+02:00")

    assert (
        collector._should_finalize_day(
            timestamp=start,
            pv_power=0.0,
        )
        is False
    )

    end = datetime.fromisoformat("2026-08-15T21:05:00+02:00")

    assert (
        collector._should_finalize_day(
            timestamp=end,
            pv_power=0.0,
        )
        is True
    )


def test_power_breaks_zero_power_period(
    collector,
):
    collector._is_after_sunset = lambda timestamp: True

    start = datetime.fromisoformat("2026-08-15T21:00:00+02:00")

    assert (
        collector._should_finalize_day(
            timestamp=start,
            pv_power=0.0,
        )
        is False
    )

    # PV starts producing again before five minutes have passed.
    assert (
        collector._should_finalize_day(
            timestamp=datetime.fromisoformat("2026-08-15T21:03:00+02:00"),
            pv_power=100.0,
        )
        is False
    )

    # A new zero-power period starts.
    assert (
        collector._should_finalize_day(
            timestamp=datetime.fromisoformat("2026-08-15T21:04:00+02:00"),
            pv_power=0.0,
        )
        is False
    )


# ============================================================================
# Energy measurements
#
# Tests that E_Day, E_Year and E_Total are recorded together when the day
# is finalized, and that they are recorded only once per calendar day.
# ============================================================================


def test_daily_energy_is_recorded_after_five_minutes_zero_power(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response
    collector._is_after_sunset = lambda timestamp: True

    response = {
        **FRONIUS_RESPONSE,
        "Head": {
            **FRONIUS_RESPONSE["Head"],
            "Timestamp": "2026-08-15T21:00:00+02:00",
        },
        "Body": {
            **FRONIUS_RESPONSE["Body"],
            "Data": {
                **FRONIUS_RESPONSE["Body"]["Data"],
                "Site": {
                    **FRONIUS_RESPONSE["Body"]["Data"]["Site"],
                    "P_PV": 0,
                },
            },
        },
    }

    mock_response.json.return_value = response

    first = collector.collect()

    assert [m.metric for m in first] == ["pv_power"]

    response["Head"]["Timestamp"] = "2026-08-15T21:05:00+02:00"

    second = collector.collect()

    metrics = {m.metric: m for m in second}

    assert metrics["pv_power"].value == 0
    assert metrics["pv_energy_day"].value == 32880
    assert metrics["pv_energy_year"].value == 13947576
    assert metrics["pv_energy_total"].value == 90416904


def test_daily_energy_is_only_recorded_once(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response
    collector._is_after_sunset = lambda timestamp: True

    response = {
        **FRONIUS_RESPONSE,
        "Head": {
            **FRONIUS_RESPONSE["Head"],
            "Timestamp": "2026-08-15T21:00:00+02:00",
        },
        "Body": {
            **FRONIUS_RESPONSE["Body"],
            "Data": {
                **FRONIUS_RESPONSE["Body"]["Data"],
                "Site": {
                    **FRONIUS_RESPONSE["Body"]["Data"]["Site"],
                    "P_PV": 0,
                },
            },
        },
    }

    mock_response.json.return_value = response

    # Start zero-power period.
    first = collector.collect()

    assert [m.metric for m in first] == ["pv_power"]

    # Five minutes later: finalize the day.
    response["Head"]["Timestamp"] = "2026-08-15T21:05:00+02:00"

    second = collector.collect()

    second_metrics = [m.metric for m in second]

    assert second_metrics.count("pv_power") == 1
    assert second_metrics.count("pv_energy_day") == 1
    assert second_metrics.count("pv_energy_year") == 1
    assert second_metrics.count("pv_energy_total") == 1

    # Another collection on the same day must not record energy again.
    response["Head"]["Timestamp"] = "2026-08-15T21:10:00+02:00"

    third = collector.collect()

    assert [m.metric for m in third] == ["pv_power"]


# ============================================================================
# Current power interface
#
# get_current_power_watt() is deliberately separate from collect().
#
# collect() is used for persistence and therefore returns no measurement
# when the inverter is offline.
#
# get_current_power_watt() is used by control logic and therefore returns
# 0 W when the inverter cannot be reached.
# ============================================================================


def test_get_current_power_watt(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.get_current_power_watt()

    assert result == 10924


def test_get_current_power_watt_calls_fronius(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.get_current_power_watt()

    assert result == 10924

    mock_get.assert_called_once_with(
        "http://192.168.178.25/solar_api/v1/GetPowerFlowRealtimeData.fcgi",
        timeout=5,
    )


# ============================================================================
# Error handling
#
# Tests the distinction between HTTP/network errors and Fronius API errors.
# HTTP/network failures are handled by collect() without creating a fake
# measurement. API errors are raised as RuntimeError.
# ============================================================================


def test_http_error_does_not_create_measurement(
    collector,
    mock_response,
    mock_get,
):
    mock_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

    mock_get.return_value = mock_response

    result = collector.collect()

    assert result == []


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


# ============================================================================
# Offline behavior
#
# collect() must not persist a fake 0 W value when the inverter is offline.
# The control interface must nevertheless return 0 W so that consumers can
# safely treat an unreachable inverter as "no available PV power".
# ============================================================================


def test_collect_returns_no_measurement_when_inverter_is_offline(
    monkeypatch,
):
    collector = FroniusSymoInverterCollector(
        inverter_ip="192.168.178.25",
    )

    def raise_connection_error():
        raise requests.exceptions.ConnectTimeout("connection timed out")

    monkeypatch.setattr(
        collector,
        "_get_data",
        raise_connection_error,
    )

    measurements = collector.collect()

    assert measurements == []


def test_get_current_power_returns_zero_when_inverter_is_offline(
    monkeypatch,
):
    collector = FroniusSymoInverterCollector(
        inverter_ip="192.168.178.25",
    )

    def raise_connection_error():
        raise requests.exceptions.ConnectTimeout("connection timed out")

    monkeypatch.setattr(
        collector,
        "_get_data",
        raise_connection_error,
    )

    assert collector.get_current_power_watt() == 0.0
