from __future__ import annotations

from src.collectors.shelly.shelly_gen3_collector.shelly_gen3_collector_base import ShellyGen3Collector


class ShellySwitchCollector(ShellyGen3Collector):
    """Collector for Shelly Gen3 devices using Switch:0.

    Used by devices such as:

        - Shelly Plug M Gen3
        - Shelly Outdoor Plug S Gen3
    """

    @property
    def rpc_method(self) -> str:
        return "Switch.GetStatus"
