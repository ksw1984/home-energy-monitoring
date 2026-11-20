"""In-memory battery adapter used for dry runs without hardware."""

import logging
from dataclasses import dataclass
from datetime import datetime, UTC

from src.bems.core.domain import BatteryIdentity, BatteryPowerCommand, BatteryProfile, BatteryState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MockBatteryCommand:
    """One accepted mock setpoint plus the state that resulted from it."""

    timestamp: datetime
    requested_power_w: float
    accepted_power_w: float
    soc_percent: float


class MockBatteryAdapter:
    """In-memory Battery adapter for a standalone control-service dry run.

    Every accepted setpoint is stored in :attr:`commands` so a caller can
    export what the controller would have requested without touching hardware.
    """

    def __init__(self, *, profile: BatteryProfile, initial_soc_percent: float) -> None:
        """Create a zero-power mock battery at the selected initial SOC."""
        if not profile.min_soc_percent <= initial_soc_percent <= profile.max_soc_percent:
            raise ValueError("initial SOC must be within the profile limits")
        self.profile = profile
        self.energy_wh = profile.capacity_wh * initial_soc_percent / 100.0
        self.power_w = 0.0
        self.timestamp = datetime.now(UTC)
        self.available = True
        self.fail_operations = False
        self.commands: list[MockBatteryCommand] = []

    async def get_state(self) -> BatteryState:
        """Return the mock's current SOC, energy, and signed power."""
        if self.fail_operations:
            raise ConnectionError("mock battery unavailable")
        return BatteryState(
            timestamp=self.timestamp,
            soc_percent=100.0 * self.energy_wh / self.profile.capacity_wh,
            energy_wh=self.energy_wh,
            charge_power_w=max(0.0, self.power_w),
            discharge_power_w=max(0.0, -self.power_w),
            available=self.available,
        )

    async def get_identity(self) -> BatteryIdentity:
        """Expose a synthetic identity for explicitly requested dry-run control."""
        return BatteryIdentity("BEMS-MOCK", "mock")

    async def set_power(self, command: BatteryPowerCommand) -> None:
        """Accept an instantaneous, profile-limited target for dry-run purposes."""
        if self.fail_operations:
            raise ConnectionError("mock battery unavailable")
        charge_limit = self.profile.max_charge_power_w
        discharge_limit = self.profile.max_discharge_power_w
        self.power_w = min(charge_limit, max(-discharge_limit, command.power_w))
        self.timestamp = command.timestamp
        self.commands.append(
            MockBatteryCommand(
                timestamp=command.timestamp,
                requested_power_w=command.power_w,
                accepted_power_w=self.power_w,
                soc_percent=100.0 * self.energy_wh / self.profile.capacity_wh,
            )
        )
        logger.info(
            "BEMS mock battery accepted %.1f W (requested %.1f W) at %s",
            self.power_w,
            command.power_w,
            command.timestamp.isoformat(),
        )
