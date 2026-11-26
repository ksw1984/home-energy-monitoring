import asyncio

from src.bems.battery_adapter.zendure import (
    INVERSE_MAX_POWER_PROPERTY,
    ZendureApiError,
    ZendureBatteryAdapter,
    ZendureZenSdkClient,
)

import httpx
import pytest


def _report(*, inverse_max_power: int = 3000, input_limit: int = 0, output_limit: int = 0):
    return {
        "sn": "HEE3NDP5P200169",
        "product": "solarFlow4000MixAC+",
        "properties": {
            "electricLevel": 98,
            "gridInputPower": 0,
            "outputHomePower": 0,
            "acMode": 2,
            "inputLimit": input_limit,
            "outputLimit": output_limit,
            INVERSE_MAX_POWER_PROPERTY: inverse_max_power,
        },
        "packData": [{"socLevel": 98, "softVersion": 4144}],
    }


def test_ensure_inverse_max_power_writes_and_reads_back():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path, request.content.decode() if request.content else ""))
        if request.url.path == "/properties/write":
            return httpx.Response(200, json={"success": True, "code": 200, "sn": "HEE3NDP5P200169"})
        return httpx.Response(200, json=_report(inverse_max_power=3000))

    client = ZendureZenSdkClient("192.168.178.190", serial_number="HEE3NDP5P200169")
    client._http = httpx.AsyncClient(base_url=client.base_url, transport=httpx.MockTransport(handler))

    try:
        asyncio.run(client.ensure_inverse_max_power(3000, attempts=2, retry_interval_s=0))
    finally:
        asyncio.run(client.close())

    assert requests[0][0:2] == ("POST", "/properties/write")
    assert '"inverseMaxPower":3000' in requests[0][2]
    assert requests[1][0:2] == ("GET", "/properties/report")


def test_ensure_inverse_max_power_retries_lagging_readback():
    reports = [_report(inverse_max_power=800), _report(inverse_max_power=3000)]
    writes = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal writes
        if request.url.path == "/properties/write":
            writes += 1
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json=reports.pop(0))

    client = ZendureZenSdkClient("192.168.178.190", serial_number="HEE3NDP5P200169")
    client._http = httpx.AsyncClient(base_url=client.base_url, transport=httpx.MockTransport(handler))

    try:
        asyncio.run(client.ensure_inverse_max_power(3000, attempts=3, retry_interval_s=0))
    finally:
        asyncio.run(client.close())

    assert writes == 1


def test_ensure_inverse_max_power_raises_on_mismatch():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/properties/write":
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json=_report(inverse_max_power=800))

    client = ZendureZenSdkClient("192.168.178.190", serial_number="HEE3NDP5P200169")
    client._http = httpx.AsyncClient(base_url=client.base_url, transport=httpx.MockTransport(handler))

    try:
        with pytest.raises(ZendureApiError, match="inverseMaxPower readback is 800 W, expected 3000 W"):
            asyncio.run(client.ensure_inverse_max_power(3000, attempts=2, retry_interval_s=0))
    finally:
        asyncio.run(client.close())


def test_configure_device_limits_writes_effective_discharge_limit():
    seen = []

    class FakeClient(ZendureZenSdkClient):
        def __init__(self):
            super().__init__("192.168.178.190", serial_number="HEE3NDP5P200169")

        async def ensure_inverse_max_power(self, power_w: int, *, attempts: int = 8, retry_interval_s: float = 0.5) -> None:
            seen.append((power_w, attempts, retry_interval_s))

    client = FakeClient()
    adapter = ZendureBatteryAdapter(
        client=client,
        capacity_wh=8000,
        max_charge_power_w=3000,
        max_discharge_power_w=3000,
        allow_control=True,
    )

    asyncio.run(adapter.configure_device_limits(discharge_limit_w=3500))

    assert seen == [(3000, 8, 0.5)]


def test_configure_device_limits_requires_allow_control():
    adapter = ZendureBatteryAdapter(
        client=ZendureZenSdkClient("192.168.178.190", serial_number="HEE3NDP5P200169"),
        capacity_wh=8000,
        max_charge_power_w=3000,
        max_discharge_power_w=3000,
        allow_control=False,
    )

    with pytest.raises(PermissionError, match="allow_control"):
        asyncio.run(adapter.configure_device_limits(discharge_limit_w=3000))
