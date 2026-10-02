from __future__ import annotations

from src.collectors.shelly.shelly_gen3_collector.shelly_gen3_pm_collector import (
    ShellyPmCollector,
)


def test_rpc_method():
    collector = ShellyPmCollector(
        timezone="UTC",
        interval=10,
        source="test_shelly_pm",
        ip="192.168.178.201",
        timeout=2.0,
    )

    assert collector.rpc_method == "PM1.GetStatus"
