"""Offline tests for the Marstek Open API adapter and UDP envelope."""

import asyncio
import json
from datetime import datetime, UTC

from src.bems.batery_adapter.marstek import MarstekApiError, MarstekBatteryAdapter, MarstekUdpClient
from src.bems.domain import BatteryPowerCommand

import pytest


class FakeMarstekClient:
    """Capture API calls and return scripted results without network access."""

    instance_id = 0

    def __init__(self, results: list[dict]):
        self.results = results
        self.calls: list[tuple[str, dict]] = []

    async def request(self, method: str, params: dict | None = None) -> dict:
        self.calls.append((method, params or {"id": self.instance_id}))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_udp_client_sends_correlated_json_rpc_request(monkeypatch):
    """The client sends the documented method/params envelope and accepts its ID."""
    sent: list[bytes] = []

    class FakeSocket:
        def __init__(self, *_args):
            self.bound = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def setblocking(self, _blocking):
            pass

        def bind(self, address):
            self.bound = address

    async def run():
        async def fake_send(_sock, payload, _address):
            sent.append(payload)

        async def fake_recv(_sock, _size):
            return b'{"id":1,"src":"VenusE","result":{"bat_soc":50}}', ("127.0.0.1", 30000)

        monkeypatch.setattr("src.bems.marstek.socket.socket", FakeSocket)
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "sock_sendto", fake_send)
        monkeypatch.setattr(loop, "sock_recvfrom", fake_recv)
        return await MarstekUdpClient(host="127.0.0.1").request("ES.GetStatus")

    assert asyncio.run(run()) == {"bat_soc": 50}
    assert json.loads(sent[0]) == {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}


def test_udp_client_times_out_and_rejects_invalid_response(monkeypatch):
    """Timeouts and malformed responses fail visibly instead of returning fake state."""

    class FakeSocket:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def setblocking(self, _blocking):
            pass

        def bind(self, _address):
            pass

    async def run():
        timed_out = False

        async def fake_send(*_args):
            pass

        async def fake_recv(*_args):
            if timed_out:
                raise TimeoutError
            return b"not-json", ("127.0.0.1", 30000)

        monkeypatch.setattr("src.bems.marstek.socket.socket", FakeSocket)
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "sock_sendto", fake_send)
        monkeypatch.setattr(loop, "sock_recvfrom", fake_recv)
        client = MarstekUdpClient(host="127.0.0.1")
        with pytest.raises(MarstekApiError, match="invalid JSON"):
            await client.request("ES.GetStatus")

        timed_out = True
        with pytest.raises(TimeoutError, match=r"ES.GetStatus timed out"):
            await client.request("ES.GetStatus")

    asyncio.run(run())


def test_marstek_state_normalizes_soc_energy_and_power():
    """SOC and signed power map into normalized BEMS state with Wh units."""
    client = FakeMarstekClient([{"bat_soc": 60, "bat_power": -420}])
    battery = MarstekBatteryAdapter(
        client=client,
        capacity_wh=5120,
        max_charge_power_w=2500,
        max_discharge_power_w=2500,
    )
    state = asyncio.run(battery.get_state())
    assert state.soc_percent == 60
    assert state.energy_wh == 3072
    assert state.charge_power_w == 0
    assert state.discharge_power_w == 420


def test_marstek_identity_reads_model_and_exact_reported_version_token():
    """Compatibility gating uses the identity returned by the local device API."""
    client = FakeMarstekClient([{"device": "VenusE", "ver": 150}])
    battery = MarstekBatteryAdapter(client=client, capacity_wh=5120, max_charge_power_w=2500, max_discharge_power_w=2500)

    identity = asyncio.run(battery.get_identity())

    assert identity.model == "VenusE"
    assert identity.firmware_version == "150"
    assert client.calls[0][0] == "Marstek.GetDevice"


def test_marstek_state_rejects_missing_or_invalid_fields():
    """Missing actual power and out-of-range SOC are reported as API errors."""
    battery = MarstekBatteryAdapter(
        client=FakeMarstekClient([{"bat_soc": 40}]),
        capacity_wh=5120,
        max_charge_power_w=2500,
        max_discharge_power_w=2500,
    )
    with pytest.raises(MarstekApiError, match="bat_power"):
        asyncio.run(battery.get_state())

    battery.client = FakeMarstekClient([{"bat_soc": 101, "bat_power": 0}])
    with pytest.raises(MarstekApiError, match="SOC"):
        asyncio.run(battery.get_state())


def test_marstek_identity_requires_model_and_version():
    battery = MarstekBatteryAdapter(
        client=FakeMarstekClient([{"device": "VenusE"}]),
        capacity_wh=5120,
        max_charge_power_w=2500,
        max_discharge_power_w=2500,
    )

    with pytest.raises(MarstekApiError, match="version token"):
        asyncio.run(battery.get_identity())


def test_marstek_writes_are_opt_in_and_power_is_bounded():
    """Passive writes are disabled by default and clamp charge/discharge separately."""
    command = BatteryPowerCommand(datetime(2026, 10, 18, tzinfo=UTC), 3100)
    disabled = MarstekBatteryAdapter(
        client=FakeMarstekClient([]),
        capacity_wh=5120,
        max_charge_power_w=1800,
        max_discharge_power_w=800,
    )
    with pytest.raises(PermissionError, match="allow_control"):
        asyncio.run(disabled.set_power(command))

    client = FakeMarstekClient([{"set_result": True}, {"set_result": "ture"}])
    battery = MarstekBatteryAdapter(
        client=client,
        capacity_wh=5120,
        max_charge_power_w=1800,
        max_discharge_power_w=800,
        allow_control=True,
    )
    asyncio.run(battery.set_power(command))
    asyncio.run(battery.set_power(BatteryPowerCommand(command.timestamp, -1000)))
    assert client.calls[0] == (
        "ES.SetMode",
        {"id": 0, "config": {"mode": "Passive", "passive_cfg": {"power": -1800, "cd_time": 300}}},
    )
    assert client.calls[1][1]["config"]["passive_cfg"]["power"] == 800


def test_marstek_write_rejection_is_reported():
    """A false set_result is not treated as a successful setpoint."""
    battery = MarstekBatteryAdapter(
        client=FakeMarstekClient([{"set_result": False}]),
        capacity_wh=5120,
        max_charge_power_w=2500,
        max_discharge_power_w=2500,
        allow_control=True,
    )
    with pytest.raises(MarstekApiError, match="rejected"):
        asyncio.run(battery.set_power(BatteryPowerCommand(datetime.now(UTC), 100)))
