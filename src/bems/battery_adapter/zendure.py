"""Local Zendure ZenSDK HTTP client.

API:
    GET  /properties/report
    POST /properties/write

The device must be reachable on the same LAN.
No Zendure cloud connection is used.
"""

from __future__ import annotations

import math
from datetime import datetime, UTC
from typing import Any

from src.bems.core.domain import BatteryIdentity, BatteryPowerCommand, BatteryState

import httpx

MAX_SOC_PERCENT = 100


class ZendureApiError(RuntimeError):
    """Raised when the Zendure local API returns an invalid response."""


class ZendureZenSdkClient:
    """Lazy async client for the Zendure SolarFlow local ZenSDK API."""

    def __init__(
        self,
        host: str,
        *,
        port: int = 80,
        serial_number: str | None = None,
        timeout_s: float = 3.0,
    ) -> None:
        if not host:
            raise ValueError("Zendure host must not be empty")

        self.host = host
        self.port = port
        self.serial_number = serial_number
        self.configured_serial_number = serial_number
        self.timeout_s = timeout_s

        self._http: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def _client(self) -> httpx.AsyncClient:
        """Create the HTTP session lazily."""
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout_s),
                headers={"Accept": "application/json"},
            )
        return self._http

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = await self._client()

        try:
            response = await client.request(method, path, json=json)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise ZendureApiError(f"Zendure API request failed: {method} {path}: {exc}") from exc
        except ValueError as exc:
            raise ZendureApiError(f"Zendure returned invalid JSON for {path}") from exc

        if not isinstance(payload, dict):
            raise ZendureApiError(f"Unexpected Zendure response for {path}: {type(payload).__name__}")

        return payload

    async def get_report(self) -> dict[str, Any]:
        """Read the complete device report."""
        report = await self._request_json(
            "GET",
            "/properties/report",
        )

        # The documented response contains the serial number at the top level.
        # Some firmware versions may expose properties in a different shape.
        serial = report.get("sn")
        if isinstance(serial, str) and serial:
            self.serial_number = serial

        return report

    @staticmethod
    def _properties(report: dict[str, Any]) -> dict[str, Any]:
        """Return the properties section, tolerating flat responses."""
        properties = report.get("properties", report)
        if not isinstance(properties, dict):
            raise ZendureApiError("Zendure report has no valid properties object")
        return properties

    async def get_properties(self) -> dict[str, Any]:
        """Read normalized device properties."""
        report = await self.get_report()
        return self._properties(report)

    async def write_properties(
        self,
        properties: dict[str, int],
    ) -> dict[str, Any]:
        """Write one or more writable device properties."""
        if not properties:
            raise ValueError("At least one property must be supplied")

        if self.serial_number is None:
            # The API requires the device serial number in every write.
            await self.get_report()

        if not self.serial_number:
            raise ZendureApiError("Cannot write to Zendure: serial number is unknown")

        payload = {
            "sn": self.serial_number,
            "properties": properties,
        }

        return await self._request_json(
            "POST",
            "/properties/write",
            json=payload,
        )

    async def set_charge_power(self, power_w: int) -> dict[str, Any]:
        """Set AC charging power limit in watts."""
        if power_w < 0:
            raise ValueError("Charge power must be >= 0")

        return await self.write_properties(
            {
                "acMode": 1,
                "outputLimit": 0,
                "inputLimit": power_w,
            }
        )

    async def set_discharge_power(self, power_w: int) -> dict[str, Any]:
        """Set AC output/discharge power limit in watts."""
        if power_w < 0:
            raise ValueError("Discharge power must be >= 0")

        return await self.write_properties(
            {
                "acMode": 2,
                "inputLimit": 0,
                "outputLimit": power_w,
            }
        )

    async def stop_power(self) -> dict[str, Any]:
        """Set both AC power limits to zero."""
        return await self.write_properties(
            {
                "inputLimit": 0,
                "outputLimit": 0,
            }
        )

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None


class ZendureBatteryAdapter:
    """Normalize SolarFlow state and gated power commands for BEMS."""

    def __init__(
        self,
        *,
        client: ZendureZenSdkClient,
        capacity_wh: float,
        max_charge_power_w: float,
        max_discharge_power_w: float,
        allow_control: bool = False,
    ) -> None:
        if min(capacity_wh, max_charge_power_w, max_discharge_power_w) <= 0:
            raise ValueError("Zendure capacity and power limits must be positive")
        self.client = client
        self.capacity_wh = capacity_wh
        self.max_charge_power_w = max_charge_power_w
        self.max_discharge_power_w = max_discharge_power_w
        self.allow_control = allow_control

    async def get_state(self) -> BatteryState:
        """Read average SOC and measured AC charge/discharge power."""
        report = await self.client.get_report()
        properties = self.client._properties(report)
        pack_data = report.get("packData", [])
        pack_soc = None
        if isinstance(pack_data, list):
            soc_values = [self._number(pack, "socLevel") for pack in pack_data if isinstance(pack, dict) and "socLevel" in pack]
            if soc_values:
                pack_soc = sum(soc_values) / len(soc_values)
        soc = self._number(properties, "electricLevel", default=pack_soc)
        if not 0 <= soc <= MAX_SOC_PERCENT:
            raise ZendureApiError(f"Zendure SOC is outside 0..100: {soc}")

        # gridInputPower/outputHomePower represent AC power at the grid/home.
        # Pack-level fields are fallbacks for firmware that omits AC totals.
        charge_power = (
            self._number(properties, "gridInputPower") if "gridInputPower" in properties else self._number(properties, "outputPackPower", default=0.0)
        )
        discharge_power = (
            self._number(properties, "outputHomePower")
            if "outputHomePower" in properties
            else self._number(properties, "packInputPower", default=0.0)
        )
        if charge_power < 0 or discharge_power < 0:
            raise ZendureApiError("Zendure reported negative charge or discharge power")

        return BatteryState(
            timestamp=datetime.now(UTC),
            soc_percent=soc,
            energy_wh=self.capacity_wh * soc / 100.0,
            charge_power_w=charge_power,
            discharge_power_w=discharge_power,
            available=True,
        )

    async def get_identity(self) -> BatteryIdentity:
        """Read product and battery-pack firmware for the live allowlist check."""
        report = await self.client.get_report()
        properties = self.client._properties(report)
        reported_serial = report.get("sn")
        if self.client.configured_serial_number and reported_serial != self.client.configured_serial_number:
            raise ZendureApiError("Zendure serial number does not match the configured device")
        product = report.get("product", report.get("model", properties.get("product")))
        if not isinstance(product, str) or not product.strip():
            raise ZendureApiError("Zendure report is missing a product/model identity")

        firmware = report.get("firmwareVersion", properties.get("firmwareVersion"))
        pack_data = report.get("packData", [])
        if firmware is None and isinstance(pack_data, list):
            firmware = next((pack.get("softVersion") for pack in pack_data if isinstance(pack, dict) and pack.get("softVersion") is not None), None)
        if isinstance(firmware, bool) or not isinstance(firmware, str | int | float):
            raise ZendureApiError("Zendure report is missing a verifiable firmware version")
        firmware_version = str(firmware).strip()
        if not firmware_version:
            raise ZendureApiError("Zendure report returned an empty firmware version")
        return BatteryIdentity(model=product, firmware_version=firmware_version)

    async def set_power(self, command: BatteryPowerCommand) -> None:
        """Send a bounded AC target, only after explicit write opt-in."""
        if not self.allow_control:
            raise PermissionError("Zendure writes are disabled; set allow_control: true to enable them")
        if not math.isfinite(command.power_w):
            raise ValueError("Zendure power command must be finite")

        power_w = min(self.max_charge_power_w, max(-self.max_discharge_power_w, command.power_w))
        if power_w > 0:
            await self.client.set_charge_power(round(power_w))
        elif power_w < 0:
            await self.client.set_discharge_power(round(abs(power_w)))
        else:
            await self.client.stop_power()

    @staticmethod
    def _number(properties: dict[str, Any], key: str, *, default: float | None = None) -> float:
        """Read a finite numeric ZenSDK property, accepting numeric strings."""
        value = properties.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            raise ZendureApiError(f"Zendure report is missing numeric property '{key}'")
        try:
            result = float(value)
        except ValueError as error:
            raise ZendureApiError(f"Zendure property '{key}' is not numeric") from error
        if not math.isfinite(result):
            raise ZendureApiError(f"Zendure property '{key}' is not finite")
        return result
