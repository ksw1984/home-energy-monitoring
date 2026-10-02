from __future__ import annotations

from src.collectors.shelly.shelly_gen3_collector.shelly_gen3_switch_collector import (
    ShellySwitchCollector,
)


def test_rpc_method():
    collector = ShellySwitchCollector(
        timezone="UTC",
        interval=10,
        source="test_shelly_switch",
        ip="192.168.178.206",
        timeout=2.0,
    )

    assert collector.rpc_method == "Switch.GetStatus"
