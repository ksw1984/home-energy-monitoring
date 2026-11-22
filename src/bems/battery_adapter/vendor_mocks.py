"""Offline mock endpoints matching the vendor transport shapes used by BEMS."""

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class MockBatteryEndpoint:
    """Small stateful endpoint used to exercise adapter behavior offline.

    ``vendor`` selects either Marstek's JSON-RPC request/response shape or
    Anker's Modbus register-style interface. Commands still share signed AC
    power semantics: positive charges and negative discharges.
    """

    vendor: str
    soc_percent: float = 50.0
    power_w: float = 0.0
    available: bool = True
    delay_seconds: float = 0.0
    fail_next: bool = False
    reject_commands: bool = False
    max_power_w: float | None = None

    async def request_marstek(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Asynchronously emulate one Marstek JSON-RPC request."""
        await self._delay()
        return self.marstek_request(method, params)

    async def read_anker_registers(self) -> dict[int, int]:
        """Read SOC and power using the mock-only register addresses."""
        await self._delay()
        return self.anker_registers()

    async def set_anker_power(self, power_w: float) -> int:
        """Write signed AC power and return the mock device's accepted watts."""
        await self._delay()
        return self.write_anker_power(power_w)

    async def _delay(self) -> None:
        """Apply configured latency without blocking the event loop."""
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)

    def __post_init__(self) -> None:
        """Validate the vendor selector and select its nominal power limit."""
        if self.vendor not in {"marstek", "anker"}:
            raise ValueError("vendor must be 'marstek' or 'anker'")
        if self.max_power_w is None:
            self.max_power_w = 2500.0 if self.vendor == "marstek" else 3500.0

    def marstek_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a JSON-RPC-shaped response or raise a configured mock failure."""
        if self.vendor != "marstek":
            raise RuntimeError("Marstek JSON-RPC requested from a non-Marstek mock")
        if self.fail_next:
            self.fail_next = False
            raise TimeoutError("mock Marstek endpoint timed out")
        if not self.available:
            raise ConnectionError("mock Marstek endpoint unavailable")
        params = params or {}
        if method == "ES.GetStatus":
            return {"jsonrpc": "2.0", "result": {"soc": self.soc_percent, "power": self.power_w}}
        if method == "ES.SetMode":
            if self.reject_commands:
                raise ValueError("mock Marstek device rejected command")
            requested = float(params.get("power", 0.0))
            limit = self.max_power_w or 0.0
            self.power_w = max(-limit, min(limit, requested))
            return {"jsonrpc": "2.0", "result": {"accepted": True, "power": self.power_w}}
        raise ValueError(f"unsupported mock Marstek method: {method}")

    def anker_registers(self) -> dict[int, int]:
        """Return SOC and power in mock registers 0 and 1."""
        if self.vendor != "anker":
            raise RuntimeError("Modbus registers requested from a non-Anker mock")
        if self.fail_next:
            self.fail_next = False
            raise TimeoutError("mock Anker Modbus endpoint timed out")
        if not self.available:
            raise ConnectionError("mock Anker Modbus endpoint unavailable")
        # Stable mock register convention, deliberately local to the mock;
        # production register addresses/scales require verified device docs.
        return {0: round(self.soc_percent * 10), 1: round(self.power_w)}

    def write_anker_power(self, power_w: float) -> int:
        """Apply the mock limit and return accepted integer watts."""
        if self.vendor != "anker":
            raise RuntimeError("Modbus write requested from a non-Anker mock")
        if self.fail_next:
            self.fail_next = False
            raise TimeoutError("mock Anker Modbus endpoint timed out")
        if not self.available:
            raise ConnectionError("mock Anker Modbus endpoint unavailable")
        if self.reject_commands:
            raise ValueError("mock Anker device rejected command")
        limit = self.max_power_w or 0.0
        self.power_w = max(-limit, min(limit, float(power_w)))
        return round(self.power_w)
