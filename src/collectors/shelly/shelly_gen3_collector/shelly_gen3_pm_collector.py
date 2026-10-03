from __future__ import annotations

from src.collectors.shelly.shelly_gen3_collector.shelly_gen3_collector_base import ShellyGen3Collector


class ShellyPmCollector(ShellyGen3Collector):
    """Collector for Shelly Gen3 devices using PM1.

    Used by:

        - Shelly Plug PM Gen3

    Unlike Switch-based Shelly devices, the Plug PM has no relay.
    Its measurement component is PM1.
    """

    @property
    def rpc_method(self) -> str:
        return "PM1.GetStatus"
