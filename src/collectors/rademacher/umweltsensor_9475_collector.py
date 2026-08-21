from datetime import datetime
from typing import Any

import requests

from src.collectors.base_collector import BaseCollector
from src.collectors.definitions.measurement import Measurement
from src.collectors.definitions.rademacher import RADEMACHER_METRICS


class RademacherEnvironmentSensorCollector(BaseCollector):
    """
    Collect environmental measurements from a Rademacher Smart Home Box.

    The collector reads the configured environment sensor from the Rademacher
    Smart Home Box API and converts supported capabilities into the common
    :class:`Measurement` format.

    Supported measurements are:

    - temperature
    - light
    - wind speed
    - rain detection
    - sun detection
    - sun direction
    - sun height

    Rademacher returns capability values mostly as strings. Boolean values
    are normalized to ``1.0`` and ``0.0`` so that all measurements can be
    represented as numeric values.

    If the Smart Home Box is unavailable, ``collect()`` returns an empty
    list instead of producing measurements with potentially misleading
    default values.
    """

    SOURCE = "rademacher"

    def __init__(
        self,
        smart_home_box_ip="192.168.178.19",
        device_id=50,
    ) -> None:
        """Initialize the Rademacher environment sensor collector.

        Args:
            smart_home_box_ip: IP address of the Rademacher Smart Home Box.
            device_id: Rademacher device ID of the environment sensor in the Smart Home Box.
        """
        self.smart_home_box_ip = smart_home_box_ip
        self.device_id = device_id
        self.sensor_url = f"http://{smart_home_box_ip}/devices/{device_id}"

    def _get_data(self) -> dict[str, Any]:
        """Fetch and validate the environment sensor data.

        Returns
            The ``payload.device`` dictionary returned by the Rademacher API.

        Raises:
            requests.exceptions.RequestException: If the HTTP request fails.
            RuntimeError: If the Rademacher API reports an error or returns
                an invalid response structure.
        """
        response = requests.get(
            self.sensor_url,
            timeout=5,
        )
        response.raise_for_status()

        data = response.json()

        if data.get("error_code") != 0:
            raise RuntimeError(f"Rademacher API error: " f"{data.get('error_description', 'Unknown error')}")

        try:
            return data["payload"]["device"]
        except KeyError as exc:
            raise RuntimeError("Invalid Rademacher API response: " "payload.device missing") from exc

    def collect(self) -> list[Measurement]:
        """Collect all supported measurements from the environment sensor.

        Each supported Rademacher capability is converted into a
        :class:`Measurement`. Capabilities that are not available or do not
        contain a value are skipped.

        Returns:
            A list of available environment measurements.

        Note:
            If the Smart Home Box cannot be reached, an empty list is
            returned. No artificial zero values are recorded because zero
            would represent an actual sensor reading rather than an
            unavailable sensor.
        """
        try:
            device = self._get_data()

        except requests.exceptions.RequestException as exc:
            print(f"Rademacher environment sensor unavailable: {exc}. " "Returning no measurements.")
            return []

        capabilities = {capability["name"]: capability for capability in device.get("capabilities", [])}

        measurements = []

        for capability_name, metric_name in [
            ("TEMP_CURR_DEG_MEA", "temperature"),
            ("LIGHT_VAL_LUX_MEA", "light"),
            ("WIND_SPEED_MS_MEA", "wind_speed"),
            ("RAIN_DETECTION_MEA", "rain_detected"),
            ("SUN_DETECTION_MEA", "sun_detected"),
            ("SUN_DIRECTION_MEA", "sun_direction"),
            ("SUN_HEIGHT_DEG_MEA", "sun_height"),
        ]:
            capability = capabilities.get(capability_name)

            if capability is None:
                continue

            if "value" not in capability:
                continue

            value = self._parse_value(capability["value"])

            timestamp = self._parse_timestamp(capability.get("timestamp"))

            measurements.append(
                self._measurement(
                    timestamp=timestamp,
                    metric=metric_name,
                    value=value,
                )
            )

        return measurements

    @staticmethod
    def _parse_value(value) -> float:
        """Convert a Rademacher capability value to a numeric value.

        Rademacher commonly returns numeric values as strings. Boolean
        values are converted to ``1.0`` and ``0.0``.

        Examples:
            ``"20.1"`` -> ``20.1``
            ``"8000"`` -> ``8000.0``
            ``True`` -> ``1.0``
            ``False`` -> ``0.0``

        Args:
            value: Raw capability value returned by the Rademacher API.

        Returns:
            The normalized numeric value.

        Raises:
            ValueError: If the value cannot be converted to a float.
            TypeError: If the value is not convertible to a float.
        """

        if isinstance(value, bool):
            return 1.0 if value else 0.0

        if isinstance(value, str):
            normalized = value.lower()

            if normalized == "true":
                return 1.0

            if normalized == "false":
                return 0.0

        return float(value)

    @staticmethod
    def _parse_timestamp(timestamp) -> datetime:
        """Convert a Rademacher Unix timestamp to a timezone-aware datetime.

        Rademacher uses Unix timestamps for capability measurements.
        A missing timestamp or ``-1`` means that no valid timestamp was
        provided, in which case the current local time is used.

        Args:
            timestamp: Raw Unix timestamp returned by the API.

        Returns:
            A timezone-aware datetime representing the measurement time.
        """

        if timestamp is None or timestamp == -1:
            return datetime.now().astimezone()

        return datetime.fromtimestamp(float(timestamp)).astimezone()

    def _measurement(
        self,
        timestamp: datetime,
        metric: str,
        value: float,
    ) -> Measurement:
        """Create a standardized measurement for a Rademacher metric.

        Args:
            timestamp: Timestamp of the sensor measurement.
            metric: Internal metric name.
            value: Numeric measurement value.

        Returns:
            A :class:`Measurement` containing the source, metric, value,
            timestamp, and configured unit.

        Raises:
            RuntimeError: If no definition exists for the requested metric.
        """
        try:
            definition = RADEMACHER_METRICS[metric]
        except KeyError as exc:
            raise RuntimeError(f"No Rademacher metric definition for '{metric}'") from exc

        return Measurement(
            timestamp=timestamp,
            source=self.SOURCE,
            metric=metric,
            value=float(value),
            unit=definition["unit"],
        )
