from datetime import datetime

import requests

from src.collectors.base_collector import BaseCollector
from src.collectors.definitions.measurement import Measurement
from src.collectors.definitions.rademacher import RADEMACHER_METRICS


class RademacherEnvironmentSensorCollector(BaseCollector):
    SOURCE = "rademacher"

    def __init__(
        self,
        smart_home_box_ip="192.168.178.19",
        device_id=50,
    ):
        self.smart_home_box_ip = smart_home_box_ip
        self.device_id = device_id
        self.sensor_url = f"http://{smart_home_box_ip}/devices/{device_id}"

    def _get_data(self) -> dict:
        response = requests.get(
            self.sensor_url,
            timeout=5,
        )
        response.raise_for_status()

        data = response.json()

        if data.get("error_code") != 0:
            raise RuntimeError(
                f"Rademacher API error: "
                f"{data.get('error_description', 'Unknown error')}"
            )

        try:
            return data["payload"]["device"]
        except KeyError as exc:
            raise RuntimeError(
                "Invalid Rademacher API response: " "payload.device missing"
            ) from exc

    def collect(self) -> list[Measurement]:
        try:
            device = self._get_data()

        except requests.exceptions.RequestException as exc:
            print(
                f"Rademacher environment sensor unavailable: {exc}. "
                "Returning no measurements."
            )
            return []

        capabilities = {
            capability["name"]: capability
            for capability in device.get("capabilities", [])
        }

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
        """
        Rademacher liefert Werte als Strings.

        Beispiele:
            "20.1"  -> 20.1
            "8000"  -> 8000.0
            "1.2"   -> 1.2
            "true"  -> 1.0
            "false" -> 0.0
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
        """
        Rademacher liefert Unix-Timestamps.

        Beispiel:
            1787129571
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
        try:
            definition = RADEMACHER_METRICS[metric]
        except KeyError as exc:
            raise RuntimeError(
                f"No Rademacher metric definition for '{metric}'"
            ) from exc

        return Measurement(
            timestamp=timestamp,
            source=self.SOURCE,
            metric=metric,
            value=float(value),
            unit=definition["unit"],
        )
