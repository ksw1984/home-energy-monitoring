from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from src.collectors.base_collector import BaseCollector
from src.collectors.definitions.common.measurement import Measurement

import requests

logger = logging.getLogger(__name__)


class ShellyGen3Collector(BaseCollector, ABC):
    """Base collector for Shelly Gen3 devices.

    Shelly Gen3 devices expose their measurements through the local
    HTTP RPC API.

    The concrete collector only has to provide the RPC method used
    to obtain the measurement status.

    Normalized units:

        power                  -> kW
        voltage                -> V
        current                -> A
        energy_total           -> kWh
        returned_energy_total  -> kWh
    """

    POWER_W_TO_KW = 0.001
    ENERGY_WH_TO_KWH = 0.001

    def __init__(  # noqa: PLR0913
        self,
        *,
        enabled: bool = True,
        timezone: str = "UTC",
        interval: int = 10,
        source: str = "shelly_gen3",
        ip: str = "192.168.178.50",
        timeout: float = 3.0,
    ) -> None:
        super().__init__(
            enabled=enabled,
            timezone=timezone,
            interval=interval,
            source=source,
        )

        self.ip = ip
        self.timeout = timeout

        self.base_url = f"http://{self.ip}"
        self.rpc_url = f"{self.base_url}/rpc"

        self.session = requests.Session()

    @property
    @abstractmethod
    def rpc_method(self) -> str:
        """Return the Shelly RPC method used for status."""

    def connect(self) -> None:
        """Initialize the collector.

        Shelly local HTTP RPC is stateless, so no persistent connection
        to the device is required.
        """
        if not self.enabled:
            logger.debug(
                "Shelly collector '%s' disabled",
                self.source,
            )
            return

        logger.debug(
            "Shelly collector '%s' configured for %s",
            self.source,
            self.ip,
        )

    def disconnect(self) -> None:
        """Close the HTTP session."""
        self.session.close()

    def collect(self) -> list[Measurement]:
        """Collect the current Shelly measurements."""
        if not self.enabled:
            return []

        status = self._get_status()

        timestamp = self.now()

        measurements: list[Measurement] = [
            Measurement(
                timestamp=timestamp,
                source=self.source,
                metric="power",
                value=self._get_power_kw(status),
                unit="kW",
            ),
            Measurement(
                timestamp=timestamp,
                source=self.source,
                metric="voltage",
                value=self._get_float(
                    status.get("voltage"),
                    "voltage",
                ),
                unit="V",
            ),
            Measurement(
                timestamp=timestamp,
                source=self.source,
                metric="current",
                value=self._get_float(
                    status.get("current"),
                    "current",
                ),
                unit="A",
            ),
        ]

        aenergy = self._require_dict(
            status.get("aenergy"),
            "aenergy",
        )

        measurements.append(
            Measurement(
                timestamp=timestamp,
                source=self.source,
                metric="energy_total",
                value=self._get_float(
                    aenergy.get("total"),
                    "aenergy.total",
                )
                * self.ENERGY_WH_TO_KWH,
                unit="kWh",
            )
        )

        ret_aenergy = status.get("ret_aenergy")

        if ret_aenergy is not None:
            ret_aenergy = self._require_dict(
                ret_aenergy,
                "ret_aenergy",
            )

            measurements.append(
                Measurement(
                    timestamp=timestamp,
                    source=self.source,
                    metric="returned_energy_total",
                    value=self._get_float(
                        ret_aenergy.get("total"),
                        "ret_aenergy.total",
                    )
                    * self.ENERGY_WH_TO_KWH,
                    unit="kWh",
                )
            )

        logger.debug(
            "Shelly '%s': power=%.3f kW voltage=%.3f V current=%.3f A",
            self.source,
            measurements[0].value,
            measurements[1].value,
            measurements[2].value,
        )

        return measurements

    def _get_status(self) -> dict[str, Any]:
        """Read the current status through the Shelly RPC API."""
        try:
            response = self.session.get(
                f"{self.rpc_url}/{self.rpc_method}",
                params={"id": 0},
                timeout=self.timeout,
            )

            response.raise_for_status()

        except requests.RequestException as exc:
            logger.warning(
                "Shelly '%s' at %s unavailable: %s",
                self.source,
                self.ip,
                exc,
            )
            raise

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Shelly returned invalid JSON: {response.text!r}",
            ) from exc

        if not isinstance(payload, dict):
            raise TypeError(
                f"Unexpected Shelly response type: {type(payload).__name__}",
            )

        if "error" in payload:
            raise RuntimeError(
                f"Shelly RPC error: {payload['error']!r}",
            )

        return payload

    def _get_power_kw(self, status: dict[str, Any]) -> float:
        """Convert Shelly instantaneous power from W to kW."""
        power_w = self._get_float(
            status.get("apower"),
            "apower",
        )

        # Explicit float conversion is intentional.
        # Shelly returns a numeric value, but InfluxDB field types
        # must remain consistent.
        return power_w * self.POWER_W_TO_KW

    @staticmethod
    def _get_float(
        value: Any,
        name: str,
    ) -> float:
        """Validate and normalize a numeric Shelly value to float."""
        if isinstance(value, bool):
            raise TypeError(
                f"Shelly field '{name}' must be numeric, got bool",
            )

        if not isinstance(value, (int, float)):
            raise TypeError(
                f"Shelly field '{name}' must be numeric, got {type(value).__name__}",
            )

        return float(value)

    @staticmethod
    def _require_dict(
        value: Any,
        name: str,
    ) -> dict[str, Any]:
        """Validate a nested Shelly response object."""
        if not isinstance(value, dict):
            raise TypeError(
                f"Shelly field '{name}' must be an object, got {type(value).__name__}",
            )

        return value
