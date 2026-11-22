"""Anker SOLIX Solarbank Max AC adapter using local Modbus TCP."""

import asyncio
import math
import struct
from datetime import datetime, UTC
from typing import Protocol

from src.bems.core.domain import BatteryIdentity, BatteryPowerCommand, BatteryState

MODBUS_DEFAULT_PORT = 502
MODBUS_DEFAULT_UNIT_ID = 1
MODBUS_RESPONSE_TIMEOUT_S = 3.0
MODBUS_MAX_REGISTER = 65535
MODBUS_MAX_UNIT_ID = 255
MODBUS_MAX_READ_COUNT = 125
MODBUS_MAX_PDU_LENGTH = 254
MODBUS_MIN_RESPONSE_LENGTH = 2
MODBUS_DEVICE_ID_HEADER_SIZE = 7
MODBUS_INPUT_READ = 0x04
MODBUS_WRITE_SINGLE = 0x06
MODBUS_WRITE_MULTIPLE = 0x10
MODBUS_ENCAPSULATED_INTERFACE = 0x2B
MODBUS_READ_DEVICE_ID = 0x0E
UINT16_MAX = 65535
SOC_REGISTER = 10014
BATTERY_POWER_REGISTER = 10008
BATTERY_STATE_REGISTER_COUNT = SOC_REGISTER - BATTERY_POWER_REGISTER + 1
OPERATING_MODE_REGISTER = 10064
POWER_SETPOINT_REGISTER = 10071
THIRD_PARTY_CONTROL_MODE = 3
INT32_MAX = 2**31 - 1
INT32_MIN = -(2**31)
SOC_MAX_PERCENT = 100


class AnkerModbusError(RuntimeError):
    """Raised when an Anker Modbus TCP operation fails or returns invalid data."""


class RegisterClient(Protocol):
    """Transport contract for a minimal Modbus register client."""

    async def read_input_registers(self, address: int, count: int) -> list[int]:
        """Read a consecutive range of input registers (function code 04)."""
        ...

    async def read_device_identification(self) -> dict[int, str]:
        """Read standard Modbus Device Identification objects."""
        ...

    async def write_register(self, address: int, value: int) -> None:
        """Write one holding register (function code 06)."""
        ...

    async def write_int32(self, address: int, value: int) -> None:
        """Write one signed 32-bit value to two holding registers (function code 16)."""
        ...

    async def close(self) -> None:
        """Release any open transport resources."""
        ...


class AnkerModbusTcpClient:
    """Small async Modbus TCP client for the Solarbank Max AC's required registers."""

    def __init__(
        self,
        *,
        host: str,
        port: int = MODBUS_DEFAULT_PORT,
        unit_id: int = MODBUS_DEFAULT_UNIT_ID,
        timeout_s: float = MODBUS_RESPONSE_TIMEOUT_S,
    ) -> None:
        """Create a client for a Modbus TCP endpoint enabled in the Anker app."""
        if not host:
            raise ValueError("Anker host is required")
        if not 1 <= port <= MODBUS_MAX_REGISTER:
            raise ValueError("Anker Modbus port must be between 1 and 65535")
        if not 0 <= unit_id <= MODBUS_MAX_UNIT_ID:
            raise ValueError("Modbus unit_id must be between 0 and 255")
        if timeout_s <= 0:
            raise ValueError("Anker Modbus timeout_s must be positive")
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout_s = timeout_s
        self._transaction_id = 0
        self._lock = asyncio.Lock()

    async def read_input_registers(self, address: int, count: int) -> list[int]:
        """Read a range of input registers and return unsigned 16-bit words."""
        if not 1 <= count <= MODBUS_MAX_READ_COUNT:
            raise ValueError("Modbus read count must be between 1 and 125")
        response = await self._transact(bytes((MODBUS_INPUT_READ,)) + struct.pack(">HH", address, count))
        if (
            len(response) < MODBUS_MIN_RESPONSE_LENGTH
            or response[0] != MODBUS_INPUT_READ
            or response[1] != count * 2
            or len(response) != count * 2 + MODBUS_MIN_RESPONSE_LENGTH
        ):
            raise AnkerModbusError("Anker returned an invalid input-register response")
        return list(struct.unpack(f">{count}H", response[2:]))

    async def read_device_identification(self) -> dict[int, str]:
        """Read the basic vendor/product/revision objects (0x00-0x02).

        Unsupported device identification is treated as a failed preflight by
        the caller; the adapter never guesses a firmware version from registers.
        """
        request = bytes((MODBUS_ENCAPSULATED_INTERFACE, MODBUS_READ_DEVICE_ID, 0x01, 0x00))
        response = await self._transact(request)
        if len(response) < MODBUS_DEVICE_ID_HEADER_SIZE or response[:2] != bytes((MODBUS_ENCAPSULATED_INTERFACE, MODBUS_READ_DEVICE_ID)):
            raise AnkerModbusError("Anker returned an invalid device-identification response")
        if response[2] & 0x80:
            raise AnkerModbusError("Anker does not support Modbus Device Identification")
        object_count = response[6]
        offset = MODBUS_DEVICE_ID_HEADER_SIZE
        objects: dict[int, str] = {}
        for _ in range(object_count):
            if offset + 2 > len(response):
                raise AnkerModbusError("Anker device-identification response is truncated")
            object_id, value_length = response[offset], response[offset + 1]
            offset += 2
            value = response[offset : offset + value_length]
            if len(value) != value_length:
                raise AnkerModbusError("Anker device-identification object is truncated")
            try:
                objects[object_id] = value.decode("ascii").strip()
            except UnicodeDecodeError as error:
                raise AnkerModbusError("Anker device-identification value is not ASCII") from error
            offset += value_length
        if offset != len(response):
            raise AnkerModbusError("Anker device-identification response has trailing data")
        return objects

    async def write_register(self, address: int, value: int) -> None:
        """Write one holding register and require the device's echoed response."""
        if not 0 <= value <= UINT16_MAX:
            raise ValueError("Modbus register value must be between 0 and 65535")
        response = await self._transact(bytes((MODBUS_WRITE_SINGLE,)) + struct.pack(">HH", address, value))
        expected = bytes((MODBUS_WRITE_SINGLE,)) + struct.pack(">HH", address, value)
        if response != expected:
            raise AnkerModbusError("Anker did not confirm the holding-register write")

    async def write_int32(self, address: int, value: int) -> None:
        """Write a signed 32-bit number high-word first to consecutive registers."""
        if not INT32_MIN <= value <= INT32_MAX:
            raise ValueError("Anker setpoint must fit a signed 32-bit register")
        high_word, low_word = struct.unpack(">HH", struct.pack(">i", value))
        body = bytes((MODBUS_WRITE_MULTIPLE,)) + struct.pack(">HHBHH", address, 2, 4, high_word, low_word)
        response = await self._transact(body)
        expected = bytes((MODBUS_WRITE_MULTIPLE,)) + struct.pack(">HH", address, 2)
        if response != expected:
            raise AnkerModbusError("Anker did not confirm the 32-bit setpoint write")

    async def _transact(self, pdu: bytes) -> bytes:
        """Exchange one Modbus TCP frame and validate its transaction and unit IDs."""
        async with self._lock:
            self._transaction_id = (self._transaction_id + 1) % 65536
            transaction_id = self._transaction_id
            frame = struct.pack(">HHHB", transaction_id, 0, len(pdu) + 1, self.unit_id) + pdu
            try:
                return await asyncio.wait_for(self._exchange(frame, transaction_id), timeout=self.timeout_s)
            except TimeoutError as error:
                raise TimeoutError(f"Anker Modbus request timed out after {self.timeout_s:g}s") from error
            except OSError as error:
                raise AnkerModbusError(f"Anker Modbus TCP request failed: {error}") from error

    async def _exchange(self, frame: bytes, transaction_id: int) -> bytes:
        """Open a TCP connection for one request, read the full MBAP frame, and close."""
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            writer.write(frame)
            await writer.drain()
            header = await reader.readexactly(7)
            reply_transaction, protocol_id, length = struct.unpack(">HHH", header[:6])
            if reply_transaction != transaction_id or protocol_id != 0 or header[6] != self.unit_id:
                raise AnkerModbusError("Anker Modbus response has a mismatched MBAP header")
            if not MODBUS_MIN_RESPONSE_LENGTH <= length <= MODBUS_MAX_PDU_LENGTH:
                raise AnkerModbusError(f"Anker Modbus response has invalid length {length}")
            pdu = await reader.readexactly(length - 1)
            if pdu[0] & 0x80:
                code = pdu[1] if len(pdu) > 1 else "unknown"
                raise AnkerModbusError(f"Anker Modbus exception response (code {code})")
            return pdu
        finally:
            writer.close()
            await writer.wait_closed()

    async def close(self) -> None:
        """There is no persistent connection to close; requests own their sockets."""


class AnkerBatteryAdapter:
    """Normalize Solarbank Max AC registers and gate real writes behind explicit opt-in."""

    def __init__(
        self,
        *,
        client: RegisterClient,
        capacity_wh: float,
        max_charge_power_w: float,
        max_discharge_power_w: float,
        allow_control: bool = False,
    ) -> None:
        """Create the adapter without changing operating mode or issuing a setpoint."""
        if min(capacity_wh, max_charge_power_w, max_discharge_power_w) <= 0:
            raise ValueError("Anker capacity and power limits must be positive")
        self.client = client
        self.capacity_wh = capacity_wh
        self.max_charge_power_w = max_charge_power_w
        self.max_discharge_power_w = max_discharge_power_w
        self.allow_control = allow_control
        self._third_party_mode_enabled = False

    async def get_state(self) -> BatteryState:
        """Read the contiguous power/SOC input registers and normalize their signs."""
        registers = await self.client.read_input_registers(BATTERY_POWER_REGISTER, BATTERY_STATE_REGISTER_COUNT)
        if len(registers) != BATTERY_STATE_REGISTER_COUNT or any(not 0 <= word <= UINT16_MAX for word in registers):
            raise AnkerModbusError("Anker returned an invalid register range for battery state")
        raw_power = struct.unpack(">i", struct.pack(">HH", *registers[:2]))[0]
        soc_percent = float(registers[SOC_REGISTER - BATTERY_POWER_REGISTER])
        if not 0 <= soc_percent <= SOC_MAX_PERCENT:
            raise AnkerModbusError(f"Anker SOC is outside 0..100: {soc_percent}")
        # Register 10008: negative means charging, positive means discharging.
        bems_power = -raw_power
        return BatteryState(
            timestamp=datetime.now(UTC),
            soc_percent=soc_percent,
            energy_wh=self.capacity_wh * soc_percent / SOC_MAX_PERCENT,
            charge_power_w=max(bems_power, 0.0),
            discharge_power_w=max(-bems_power, 0.0),
            available=True,
        )

    async def get_identity(self) -> BatteryIdentity:
        """Read model and firmware from standard Modbus identification objects."""
        objects = await self.client.read_device_identification()
        model = objects.get(1, "")
        firmware = objects.get(2, "")
        if not model or not firmware:
            raise AnkerModbusError("Anker identification lacks product code or firmware revision")
        return BatteryIdentity(model=model, firmware_version=firmware)

    async def set_power(self, command: BatteryPowerCommand) -> None:
        """Switch to third-party mode once, then write a bounded signed 32-bit target."""
        if not self.allow_control:
            raise PermissionError("Anker writes are disabled; set allow_control: true to enable them")
        if not math.isfinite(command.power_w):
            raise ValueError("Anker power command must be finite")
        bems_power = max(-self.max_discharge_power_w, min(self.max_charge_power_w, command.power_w))
        # Register 10071 uses negative = charge and positive = discharge, opposite to BEMS.
        vendor_power = round(-bems_power)
        if not self._third_party_mode_enabled:
            await self.client.write_register(OPERATING_MODE_REGISTER, THIRD_PARTY_CONTROL_MODE)
            self._third_party_mode_enabled = True
        await self.client.write_int32(POWER_SETPOINT_REGISTER, vendor_power)

    async def close(self) -> None:
        """Delegate shutdown to the transport client."""
        await self.client.close()
