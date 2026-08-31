import logging
from datetime import datetime
from typing import Any

import requests

from src.collectors.base_collector import BaseCollector
from src.collectors.definitions.measurement import Measurement
from src.collectors.definitions.open_meteo import OPEN_METEO_METRICS

logger = logging.getLogger(__name__)
CURRENT_FIELDS = {
    "temperature_2m": "temperature",
    "cloud_cover": "cloud_cover",
    "precipitation": "precipitation",
    "rain": "rain",
    "weather_code": "weather_code",
    "shortwave_radiation": "shortwave_radiation",
    "sunshine_duration": "sunshine_duration",
    "wind_speed_10m": "wind_speed",
    "wind_direction_10m": "wind_direction",
    "relative_humidity_2m": "relative_humidity",
}

HOURLY_FIELDS = {
    "temperature_2m": "temperature",
    "cloud_cover": "cloud_cover",
    "precipitation": "precipitation",
    "precipitation_probability": "precipitation_probability",
    "weather_code": "weather_code",
    "shortwave_radiation": "shortwave_radiation",
    "sunshine_duration": "sunshine_duration",
    "wind_speed_10m": "wind_speed",
    "wind_direction_10m": "wind_direction",
    "relative_humidity_2m": "relative_humidity",
}


class OpenMeteoWeatherCollector(BaseCollector):
    """
    Collect current weather data from Open-Meteo.

    The collector retrieves the current weather conditions for the
    configured geographic coordinates and converts supported values
    into the common :class:`Measurement` format.
    """

    SOURCE = "open_meteo"
    API_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(
        self,
        timezone: str = "UTC",
        *,
        latitude: float,
        longitude: float,
    ) -> None:
        """Initialize the Open-Meteo weather collector.

        Args:
            latitude: Latitude of the weather location.
            longitude: Longitude of the weather location.
        """
        super().__init__(timezone)

        self.latitude = latitude
        self.longitude = longitude

    def collect(self) -> list[Measurement]:
        """Collect current weather and hourly forecast measurements."""
        try:
            data = self._get_data()
        except requests.exceptions.RequestException as exc:
            logger.error(f"Open-Meteo unavailable: {exc}. Returning no measurements.")
            return []

        measurements = []

        measurements.extend(self._parse_current(data.get("current", {})))

        measurements.extend(self._parse_hourly(data.get("hourly", {})))

        return measurements

    def _parse_current(
        self,
        current: dict[str, Any],
    ) -> list[Measurement]:
        """Parse current weather measurements."""

        if not current:
            return []

        timestamp = self._parse_timestamp(current.get("time"))

        measurements = []

        for api_field, metric in CURRENT_FIELDS.items():
            value = current.get(api_field)

            if value is None:
                continue

            measurements.append(
                self._measurement(
                    timestamp=timestamp,
                    metric=metric,
                    value=float(value),
                    measurement_type="current",
                )
            )

        return measurements

    def _parse_hourly(
        self,
        hourly: dict[str, Any],
    ) -> list[Measurement]:
        """Parse hourly weather forecast measurements."""

        if not hourly:
            return []

        timestamps = hourly.get("time", [])

        measurements = []

        for index, timestamp_value in enumerate(timestamps):
            timestamp = self._parse_timestamp(timestamp_value)

            for api_field, metric in HOURLY_FIELDS.items():
                values = hourly.get(api_field, [])

                if index >= len(values):
                    continue

                value = values[index]

                if value is None:
                    continue

                measurements.append(
                    self._measurement(
                        timestamp=timestamp,
                        metric=metric,
                        value=float(value),
                        measurement_type="forecast",
                    )
                )

        return measurements

    def _get_data(self) -> dict[str, Any]:
        """Fetch current weather and hourly forecast from Open-Meteo."""

        params: dict[str, str | float] = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "current": ",".join(CURRENT_FIELDS),
            "hourly": ",".join(HOURLY_FIELDS),
            "timezone": str(self.timezone),
        }

        response = requests.get(
            self.API_URL,
            params=params,
            timeout=10,
        )
        try:
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            logger.error(f"HTTPError {exc}")
            raise exc

        return response.json()

    def _parse_timestamp(self, timestamp: str | None) -> datetime:
        """Parse an Open-Meteo timestamp."""
        if timestamp is None:
            return self.now()

        return self.localize_timestamp(datetime.fromisoformat(timestamp))

    def _measurement(
        self,
        timestamp: datetime,
        metric: str,
        value: float,
        measurement_type: str = "current",
    ) -> Measurement:
        """Create a standardized Open-Meteo measurement."""
        try:
            definition = OPEN_METEO_METRICS[metric]
        except KeyError as exc:
            logger.error(f"No Open-Meteo metric definition for '{metric}'")
            raise RuntimeError(f"No Open-Meteo metric definition for '{metric}'") from exc

        return Measurement(
            timestamp=timestamp,
            source=self.SOURCE,
            metric=metric,
            value=value,
            unit=definition["unit"],
            measurement_type=measurement_type,
        )
