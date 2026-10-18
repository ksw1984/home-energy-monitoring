"""Marstek Venus E local Open API adapter (UDP JSON-RPC)."""

import asyncio
import json
import math
import socket
from datetime import datetime, UTC
from typing import Any

from src.bems.domain import BatteryPowerCommand, BatteryState

MAX_UDP_PORT = 65535
MAX_DATAGRAM_SIZE = 65535
MAX_SOC_PERCENT = 100


class MarstekApiError(RuntimeError):
    """Raised when a Marstek UDP request or response is invalid."""


class MarstekUdpClient:
    """Serialize JSON-RPC datagrams to one Marstek device and validate replies."""

    def __init__(self, *, host: str, port: int = 30000, timeout_s: float = 2.0, instance_id: int = 0) -> None:
        """Create a client; Open API must be enabled on the device beforehand."""
        if not host:
            raise ValueError("Marstek host is required")
        if not 1 <= port <= MAX_UDP_PORT:
            raise ValueError("Marstek UDP port must be between 1 and 65535")
        if timeout_s <= 0:
            raise ValueError("Marstek timeout_s must be positive")
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self.instance_id = instance_id
        self._request_id = 0
        self._lock = asyncio.Lock()

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one request, bind the local UDP port to the API port, and match its ID."""
        async with self._lock:
            self._request_id += 1
            request_id = self._request_id
            payload = json.dumps(
                {"id": request_id, "method": method, "params": params or {"id": self.instance_id}},
                separators=(",", ":"),
            ).encode("utf-8")
            loop = asyncio.get_running_loop()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
                udp_socket.setblocking(False)  # noqa: FBT003 - socket API exposes this as a positional flag.
                try:
                    # Marstek devices may silently drop requests from a different source port.
                    udp_socket.bind(("0.0.0.0", self.port))  # noqa: S104 - required to receive device replies on the LAN.
                    await loop.sock_sendto(udp_socket, payload, (self.host, self.port))
                    raw, _peer = await asyncio.wait_for(loop.sock_recvfrom(udp_socket, MAX_DATAGRAM_SIZE), self.timeout_s)
                except TimeoutError as error:
                    raise TimeoutError(f"Marstek {method} timed out after {self.timeout_s:g}s") from error
                except OSError as error:
                    raise MarstekApiError(f"Marstek UDP request {method} failed: {error}") from error

            try:
                response = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise MarstekApiError("Marstek returned invalid JSON") from error
            if not isinstance(response, dict) or response.get("id") != request_id:
                raise MarstekApiError("Marstek response has an invalid request ID or envelope")
            if "error" in response:
                raise MarstekApiError(f"Marstek API error for {method}: {response['error']}")
            result = response.get("result")
            if not isinstance(result, dict):
                raise MarstekApiError(f"Marstek {method} response has no result object")
            return result

    def close(self) -> None:
        """Release resources; the client opens and closes a UDP socket per request."""
        # Kept as a lifecycle hook so adapters can be shut down uniformly.


class MarstekBatteryAdapter:
    """Expose Venus E 3.0 state and explicit Passive mode commands as a BEMS battery."""

    def __init__(  # noqa: PLR0913 - constructor keeps the vendor connection, limits, and explicit safety switches together.
        self,
        *,
        client: MarstekUdpClient,
        capacity_wh: float,
        max_charge_power_w: float,
        max_discharge_power_w: float,
        allow_control: bool = False,
        passive_countdown_s: int = 300,
    ) -> None:
        """Create an adapter; writes stay disabled unless explicitly enabled."""
        if capacity_wh <= 0 or max_charge_power_w <= 0 or max_discharge_power_w <= 0:
            raise ValueError("Marstek capacity and maximum power must be positive")
        if passive_countdown_s <= 0:
            raise ValueError("passive_countdown_s must be positive")
        self.client = client
        self.capacity_wh = capacity_wh
        self.max_charge_power_w = max_charge_power_w
        self.max_discharge_power_w = max_discharge_power_w
        self.allow_control = allow_control
        self.passive_countdown_s = passive_countdown_s
        # Passive commands expire; the service should refresh them before expiry.
        self.command_refresh_interval_s = min(passive_countdown_s / 2.0, 60.0)

    async def get_state(self) -> BatteryState:
        """Read SOC and battery power from ES.GetStatus and normalize the sign."""
        result = await self.client.request("ES.GetStatus")
        soc = self._number(result, "bat_soc")
        power = self._number(result, "bat_power")
        if not 0 <= soc <= MAX_SOC_PERCENT:
            raise MarstekApiError(f"Marstek SOC is outside 0..100: {soc}")
        # Open API convention: positive battery power charges; negative discharges.
        return BatteryState(
            timestamp=datetime.now(UTC),
            soc_percent=soc,
            energy_wh=self.capacity_wh * soc / 100.0,
            charge_power_w=max(power, 0.0),
            discharge_power_w=max(-power, 0.0),
            available=True,
        )

    async def set_power(self, command: BatteryPowerCommand) -> None:
        """Set signed AC power in Passive mode, only after explicit write opt-in."""
        if not self.allow_control:
            raise PermissionError("Marstek writes are disabled; set allow_control: true to enable them")
        if not math.isfinite(command.power_w):
            raise ValueError("Marstek power command must be finite")
        # Marstek Passive mode uses negative values to charge and positive to discharge;
        # BEMS uses the opposite sign convention (positive charges).
        bems_power_w = max(-self.max_discharge_power_w, min(self.max_charge_power_w, command.power_w))
        power_w = round(-bems_power_w)
        result = await self.client.request(
            "ES.SetMode",
            {
                "id": self.client.instance_id,
                "config": {
                    "mode": "Passive",
                    "passive_cfg": {"power": power_w, "cd_time": self.passive_countdown_s},
                },
            },
        )
        if result.get("set_result") not in (True, "ture"):
            raise MarstekApiError(f"Marstek rejected Passive power command: {result}")

    @staticmethod
    def _number(result: dict[str, Any], field: str) -> float:
        """Require a finite numeric API field rather than inventing an actual value."""
        value = result.get(field)
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise MarstekApiError(f"Marstek response is missing valid numeric field '{field}'")
        return float(value)
