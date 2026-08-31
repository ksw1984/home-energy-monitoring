from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.weather_forecast.open_meteo_weather_collector import (
    OpenMeteoWeatherCollector,
)


@pytest.fixture
def collector():
    return OpenMeteoWeatherCollector(
        latitude=50.0,
        longitude=10.0,
    )


@patch("src.collectors.weather_forecast.open_meteo_weather_collector.requests.get")
def test_collect_current_weather(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "current": {
            "time": "2026-08-26T20:00",
            "temperature_2m": 18.5,
            "cloud_cover": 75,
            "precipitation": 0.0,
            "rain": 0.0,
            "weather_code": 3,
            "shortwave_radiation": 120.0,
            "sunshine_duration": 0.0,
        }
    }
    mock_get.return_value = mock_response

    collector = OpenMeteoWeatherCollector(
        latitude=52.52,
        longitude=13.405,
    )

    measurements = collector.collect()

    assert len(measurements) == 7

    temperature = next(measurement for measurement in measurements if measurement.metric == "temperature")

    assert temperature.value == 18.5
    assert temperature.unit == "°C"
    assert temperature.measurement_type == "current"

    mock_get.assert_called_once()


@patch("src.collectors.weather_forecast.open_meteo_weather_collector.requests.get")
def test_collect_hourly_forecast(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "hourly": {
            "time": [
                "2026-08-26T20:00",
                "2026-08-26T21:00",
                "2026-08-26T22:00",
            ],
            "temperature_2m": [
                18.5,
                17.9,
                17.2,
            ],
            "cloud_cover": [
                75,
                80,
                82,
            ],
            "precipitation": [
                0.0,
                0.0,
                0.2,
            ],
        }
    }
    mock_get.return_value = mock_response

    collector = OpenMeteoWeatherCollector(
        latitude=52.52,
        longitude=13.405,
    )

    measurements = collector.collect()

    assert measurements

    temperature_measurements = [measurement for measurement in measurements if measurement.metric == "temperature"]

    assert len(temperature_measurements) == 3

    assert temperature_measurements[0].value == 18.5
    assert temperature_measurements[0].measurement_type == "forecast"

    assert temperature_measurements[1].value == 17.9
    assert temperature_measurements[1].measurement_type == "forecast"

    assert temperature_measurements[2].value == 17.2
    assert temperature_measurements[2].measurement_type == "forecast"


@patch("src.collectors.weather_forecast.open_meteo_weather_collector.requests.get")
def test_collect_returns_empty_list_when_api_unavailable(mock_get):
    import requests

    mock_get.side_effect = requests.exceptions.RequestException("Connection failed")

    collector = OpenMeteoWeatherCollector(
        latitude=52.52,
        longitude=13.405,
    )

    measurements = collector.collect()

    assert measurements == []


@pytest.mark.online
def test_open_meteo_api():
    collector = OpenMeteoWeatherCollector(
        latitude=52.52,
        longitude=13.405,
    )

    measurements = collector.collect()

    assert measurements


def test_parse_current_ignores_none_values(collector):
    current = {
        "time": "2026-08-17T12:00",
        "temperature_2m": 20.0,
        "cloud_cover": None,
    }

    result = collector._parse_current(current)

    assert len(result) == 1
    assert result[0].metric == "temperature"
    assert result[0].value == 20.0


def test_parse_timestamp_with_missing_timestamp(collector):
    before = datetime.now().astimezone()

    result = collector._parse_timestamp(None)

    after = datetime.now().astimezone()

    assert result.tzinfo is not None
    assert before <= result <= after


def test_measurement_raises_for_unknown_metric(collector):
    timestamp = datetime.now().astimezone()

    with pytest.raises(
        RuntimeError,
        match=r"No Open-Meteo metric definition for 'unknown_metric'",
    ):
        collector._measurement(
            timestamp=timestamp,
            metric="unknown_metric",
            value=20.0,
        )


def test_parse_hourly_ignores_none_values(collector):
    hourly = {
        "time": ["2026-08-17T12:00"],
        "temperature_2m": [20.0],
        "cloud_cover": [None],
    }

    result = collector._parse_hourly(hourly)

    metrics = [measurement.metric for measurement in result]

    assert metrics == ["temperature"]
    assert result[0].value == 20.0
