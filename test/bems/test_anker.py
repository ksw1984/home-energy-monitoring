"""Offline tests for the Solarbank Max AC Modbus adapter."""

import asyncio
import struct
from datetime import datetime, UTC

from src.bems.battery_adapter.anker import (
    AnkerBatteryAdapter,
    AnkerModbusError,
    AnkerModbusTcpClient,
    BATTERY_POWER_REGISTER,
    OPERATING_MODE_REGISTER,
    POWER_SETPOINT_REGISTER,
    THIRD_PARTY_CONTROL_MODE,
)
from src.bems.core.domain import BatteryPowerCommand

import pytest


class FakeAnkerClient:
    """Capture Modbus operations and provide a scripted input-register block."""

    def __init__(self, *, raw_power: int = 0, soc: int = 50) -> None:
        self.raw_power = raw_power
        self.soc = soc
        self.calls: list[tuple[str, int, int]] = []

    async def read_device_identification(self) -> dict[int, str]:
        return {0: "Anker", 1: "A17E2", 2: "1.0.0"}

    async def read_input_registers(self, address: int, count: int) -> list[int]:
        assert address == BATTERY_POWER_REGISTER
        assert count == 7
        high_word, low_word = struct.unpack(">HH", struct.pack(">i", self.raw_power))
        return [high_word, low_word, 0, 0, 0, 0, self.soc]

    async def write_register(self, address: int, value: int) -> None:
        self.calls.append(("write16", address, value))

    async def write_int32(self, address: int, value: int) -> None:
        self.calls.append(("write32", address, value))

    async def close(self) -> None:
        pass


def test_anker_state_normalizes_signed_power_soc_and_energy():
    """Holding the known 32-bit power words and SOC yields normalized BEMS state."""
    charging = AnkerBatteryAdapter(
        client=FakeAnkerClient(raw_power=-625, soc=75),
        capacity_wh=7000,
        max_charge_power_w=3500,
        max_discharge_power_w=3500,
    )
    state = asyncio.run(charging.get_state())
    assert state.soc_percent == 75
    assert state.energy_wh == 5250
    assert state.charge_power_w == 625
    assert state.discharge_power_w == 0

    discharging = AnkerBatteryAdapter(
        client=FakeAnkerClient(raw_power=400),
        capacity_wh=7000,
        max_charge_power_w=3500,
        max_discharge_power_w=3500,
    )
    state = asyncio.run(discharging.get_state())
    assert state.charge_power_w == 0
    assert state.discharge_power_w == 400


def test_anker_identity_reads_product_code_and_firmware_revision():
    adapter = AnkerBatteryAdapter(
        client=FakeAnkerClient(),
        capacity_wh=7000,
        max_charge_power_w=3500,
        max_discharge_power_w=3500,
    )

    identity = asyncio.run(adapter.get_identity())

    assert identity.model == "A17E2"
    assert identity.firmware_version == "1.0.0"


def test_anker_state_rejects_invalid_soc():
    adapter = AnkerBatteryAdapter(
        client=FakeAnkerClient(soc=101),
        capacity_wh=7000,
        max_charge_power_w=3500,
        max_discharge_power_w=3500,
    )
    with pytest.raises(AnkerModbusError, match="SOC"):
        asyncio.run(adapter.get_state())


def test_anker_writes_are_opt_in_and_set_mode_before_bounded_power():
    """Write mode before the signed 32-bit setpoint and clamp each direction."""
    command_time = datetime(2026, 10, 18, tzinfo=UTC)
    client = FakeAnkerClient()
    disabled = AnkerBatteryAdapter(
        client=client,
        capacity_wh=7000,
        max_charge_power_w=2000,
        max_discharge_power_w=700,
    )
    with pytest.raises(PermissionError, match="allow_control"):
        asyncio.run(disabled.set_power(BatteryPowerCommand(command_time, 500)))
    assert client.calls == []

    adapter = AnkerBatteryAdapter(
        client=client,
        capacity_wh=7000,
        max_charge_power_w=2000,
        max_discharge_power_w=700,
        allow_control=True,
    )
    asyncio.run(adapter.set_power(BatteryPowerCommand(command_time, 2500)))
    asyncio.run(adapter.set_power(BatteryPowerCommand(command_time, -1000)))
    assert client.calls == [
        ("write16", OPERATING_MODE_REGISTER, THIRD_PARTY_CONTROL_MODE),
        ("write32", POWER_SETPOINT_REGISTER, -2000),
        ("write32", POWER_SETPOINT_REGISTER, 700),
    ]


def test_anker_tcp_client_encodes_signed_int32_write_and_validates_response():
    """Function code 16 writes high-word-first signed power to register 10071."""

    async def run():
        received: list[bytes] = []

        async def handle(reader, writer):
            header = await reader.readexactly(7)
            length = struct.unpack(">H", header[4:6])[0]
            pdu = await reader.readexactly(length - 1)
            received.append(pdu)
            transaction_id = struct.unpack(">H", header[:2])[0]
            response_pdu = bytes((0x10,)) + struct.pack(">HH", POWER_SETPOINT_REGISTER, 2)
            response_header = struct.pack(">HHHB", transaction_id, 0, len(response_pdu) + 1, header[6])
            writer.write(response_header + response_pdu)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            client = AnkerModbusTcpClient(host="127.0.0.1", port=port, timeout_s=1)
            await client.write_int32(POWER_SETPOINT_REGISTER, -2000)
            await client.close()
        return received

    received = asyncio.run(run())
    assert received == [bytes((0x10,)) + struct.pack(">HHBHH", POWER_SETPOINT_REGISTER, 2, 4, 0xFFFF, 0xF830)]


def test_anker_tcp_client_times_out_on_nonresponsive_device():
    """A connected peer that never answers surfaces a bounded timeout."""

    async def run():
        async def handle(_reader, _writer):
            await asyncio.sleep(1)

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            client = AnkerModbusTcpClient(host="127.0.0.1", port=port, timeout_s=0.01)
            with pytest.raises(TimeoutError, match="Anker Modbus request timed out"):
                await client.read_input_registers(BATTERY_POWER_REGISTER, 7)

    asyncio.run(run())


def test_anker_tcp_client_parses_modbus_device_identification():
    """Read standard Basic Device Identification objects for model and revision."""

    async def run():
        async def handle(reader, writer):
            header = await reader.readexactly(7)
            length = struct.unpack(">H", header[4:6])[0]
            pdu = await reader.readexactly(length - 1)
            assert pdu == bytes((0x2B, 0x0E, 0x01, 0x00))
            payload = bytes((0x2B, 0x0E, 0x01, 0x01, 0x00, 0x00, 0x03))
            for object_id, value in ((0, b"Anker"), (1, b"A17E2"), (2, b"1.0.0")):
                payload += bytes((object_id, len(value))) + value
            response_header = struct.pack(">HHHB", struct.unpack(">H", header[:2])[0], 0, len(payload) + 1, header[6])
            writer.write(response_header + payload)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            client = AnkerModbusTcpClient(host="127.0.0.1", port=port, timeout_s=1)
            result = await client.read_device_identification()
            await client.close()
        return result

    assert asyncio.run(run()) == {0: "Anker", 1: "A17E2", 2: "1.0.0"}
