from datetime import datetime
from unittest.mock import Mock, patch

import pytest
import requests

from src.collectors.definitions.measurement import Measurement
from src.collectors.rademacher.umweltsensor_9475_collector import (
    RademacherEnvironmentSensorCollector,
)

RADEMACHER_RESPONSE = {
    "error_description": "OK",
    "error_code": 0,
    "payload": {
        "device": {
            "capabilities": [
                {
                    "name": "TEMP_CURR_DEG_MEA",
                    "value": "20.0",
                    "min_value": "-40.0",
                    "max_value": "80.0",
                    "read_only": True,
                    "timestamp": 1787129571,
                },
                {
                    "name": "RAIN_DETECTION_MEA",
                    "value": "true",
                    "read_only": True,
                    "timestamp": 1787129571,
                },
                {
                    "name": "SUN_DETECTION_MEA",
                    "value": "false",
                    "read_only": True,
                    "timestamp": 1787067675,
                },
                {
                    "name": "SUN_DIRECTION_MEA",
                    "value": "132.0",
                    "min_value": "0.0",
                    "max_value": "360.0",
                    "read_only": True,
                    "timestamp": 1787129571,
                },
                {
                    "name": "SUN_HEIGHT_DEG_MEA",
                    "value": "43",
                    "min_value": "-90",
                    "max_value": "90",
                    "read_only": True,
                    "timestamp": 1787129571,
                },
                {
                    "name": "WIND_SPEED_MS_MEA",
                    "value": "1.2",
                    "min_value": "0.0",
                    "max_value": "70.0",
                    "read_only": True,
                    "timestamp": 1787129571,
                },
                {
                    "name": "LIGHT_VAL_LUX_MEA",
                    "value": "7000",
                    "min_value": "0",
                    "max_value": "150000",
                    "read_only": True,
                    "timestamp": 1787129571,
                },
            ]
        }
    },
}


@pytest.fixture
def collector():
    return RademacherEnvironmentSensorCollector(
        smart_home_box_ip="192.168.178.19",
        device_id=50,
    )


@pytest.fixture
def mock_response():
    response = Mock()
    response.json.return_value = RADEMACHER_RESPONSE
    response.raise_for_status.return_value = None
    return response


@pytest.fixture
def mock_get():
    with patch("src.collectors.rademacher.umweltsensor_9475_collector.requests.get") as mock:
        yield mock


def test_collect_returns_measurements(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    assert isinstance(result, list)
    assert len(result) == 7

    assert all(isinstance(measurement, Measurement) for measurement in result)


def test_collect_returns_expected_metrics(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    metrics = {measurement.metric: measurement for measurement in result}

    assert metrics["temperature"].value == 20.0
    assert metrics["light"].value == 7000.0
    assert metrics["wind_speed"].value == 1.2
    assert metrics["rain_detected"].value == 1.0
    assert metrics["sun_detected"].value == 0.0
    assert metrics["sun_direction"].value == 132.0
    assert metrics["sun_height"].value == 43.0


def test_collect_returns_expected_units(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    units = {measurement.metric: measurement.unit for measurement in result}

    assert units["temperature"] == "°C"
    assert units["light"] == "lux"
    assert units["wind_speed"] == "m/s"
    assert units["rain_detected"] == ""
    assert units["sun_detected"] == ""
    assert units["sun_direction"] == "°"
    assert units["sun_height"] == "°"


def test_collect_returns_expected_source(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    assert all(measurement.source == "rademacher" for measurement in result)


def test_collect_returns_timestamps(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    measurements = {measurement.metric: measurement for measurement in result}

    expected_timestamp = datetime.fromtimestamp(1787129571).astimezone()

    assert measurements["temperature"].timestamp == expected_timestamp
    assert measurements["light"].timestamp == expected_timestamp
    assert measurements["wind_speed"].timestamp == expected_timestamp
    assert measurements["rain_detected"].timestamp == expected_timestamp
    assert measurements["sun_direction"].timestamp == expected_timestamp
    assert measurements["sun_height"].timestamp == expected_timestamp


def test_sun_detection_uses_own_timestamp(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    measurements = {measurement.metric: measurement for measurement in result}

    expected_timestamp = datetime.fromtimestamp(1787067675).astimezone()

    assert measurements["sun_detected"].timestamp == expected_timestamp


def test_collect_timestamps_have_timezone(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    result = collector.collect()

    assert all(measurement.timestamp.tzinfo is not None for measurement in result)


def test_collect_calls_correct_url(
    collector,
    mock_response,
    mock_get,
):
    mock_get.return_value = mock_response

    collector.collect()

    mock_get.assert_called_once_with(
        "http://192.168.178.19/devices/50",
        timeout=5,
    )


def test_http_error_is_handled(
    collector,
    mock_response,
    mock_get,
):
    mock_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

    mock_get.return_value = mock_response

    result = collector.collect()

    assert result == []


def test_connection_error_is_handled(
    collector,
    mock_get,
):
    mock_get.side_effect = requests.exceptions.ConnectTimeout("connection timed out")

    result = collector.collect()

    assert result == []


def test_rademacher_api_error_is_detected(
    collector,
    mock_response,
    mock_get,
):
    error_response = {
        **RADEMACHER_RESPONSE,
        "error_description": "Device unavailable",
        "error_code": 5001,
    }

    mock_response.json.return_value = error_response
    mock_get.return_value = mock_response

    with pytest.raises(
        RuntimeError,
        match="Device unavailable",
    ):
        collector.collect()


def test_invalid_response_is_detected(
    collector,
    mock_response,
    mock_get,
):
    mock_response.json.return_value = {
        "error_description": "OK",
        "error_code": 0,
        "payload": {},
    }

    mock_get.return_value = mock_response

    with pytest.raises(
        RuntimeError,
        match=r"payload\.device missing",
    ):
        collector.collect()


def test_missing_capability_is_ignored(
    collector,
    mock_response,
    mock_get,
):
    response = {
        **RADEMACHER_RESPONSE,
        "payload": {
            "device": {
                "capabilities": [
                    capability
                    for capability in RADEMACHER_RESPONSE["payload"]["device"]["capabilities"]
                    if capability["name"] != "WIND_SPEED_MS_MEA"
                ]
            }
        },
    }

    mock_response.json.return_value = response
    mock_get.return_value = mock_response

    result = collector.collect()

    metrics = {measurement.metric for measurement in result}

    assert "wind_speed" not in metrics
    assert len(result) == 6


def test_capability_without_value_is_ignored(
    collector,
    mock_response,
    mock_get,
):
    capabilities = [
        capability
        for capability in RADEMACHER_RESPONSE["payload"]["device"]["capabilities"]
        if capability["name"] != "TEMP_CURR_DEG_MEA"
    ]

    capabilities.append(
        {
            "name": "TEMP_CURR_DEG_MEA",
            "read_only": True,
            "timestamp": 1787129571,
        }
    )

    response = {
        **RADEMACHER_RESPONSE,
        "payload": {
            "device": {
                "capabilities": capabilities,
            }
        },
    }

    mock_response.json.return_value = response
    mock_get.return_value = mock_response

    result = collector.collect()

    metrics = {measurement.metric for measurement in result}

    assert "temperature" not in metrics
    assert len(result) == 6


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("20.1", 20.1),
        ("8000", 8000.0),
        ("1.2", 1.2),
        ("true", 1.0),
        ("false", 0.0),
        ("TRUE", 1.0),
        ("FALSE", 0.0),
        (True, 1.0),
        (False, 0.0),
        (42, 42.0),
        (42.5, 42.5),
    ],
)
def test_parse_value(value, expected):
    result = RademacherEnvironmentSensorCollector._parse_value(value)

    assert result == expected


def test_parse_timestamp():
    timestamp = 1787129571

    result = RademacherEnvironmentSensorCollector._parse_timestamp(timestamp)

    expected = datetime.fromtimestamp(timestamp).astimezone()

    assert result == expected
    assert result.tzinfo is not None


def test_parse_timestamp_with_missing_timestamp():
    before = datetime.now().astimezone()

    result = RademacherEnvironmentSensorCollector._parse_timestamp(None)

    after = datetime.now().astimezone()

    assert result.tzinfo is not None
    assert before <= result <= after


def test_parse_timestamp_with_minus_one():
    before = datetime.now().astimezone()

    result = RademacherEnvironmentSensorCollector._parse_timestamp(-1)

    after = datetime.now().astimezone()

    assert result.tzinfo is not None
    assert before <= result <= after


def test_collect_returns_empty_list_when_no_capabilities(
    collector,
    mock_response,
    mock_get,
):
    mock_response.json.return_value = {
        **RADEMACHER_RESPONSE,
        "payload": {
            "device": {
                "capabilities": [],
            }
        },
    }

    mock_get.return_value = mock_response

    result = collector.collect()

    assert result == []
