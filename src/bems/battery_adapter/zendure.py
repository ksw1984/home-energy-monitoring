"""Local Zendure ZenSDK HTTP client.

API:
    GET  /properties/report
    POST /properties/write

The device must be reachable on the same LAN.
No Zendure cloud connection is used.
"""

from __future__ import annotations

from typing import Any

import httpx


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
