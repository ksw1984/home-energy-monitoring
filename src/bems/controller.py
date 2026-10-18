from src.bems.domain import BatteryProfile, BatteryState


class GridBalancingController:
    """Simple proportional controller that targets zero grid power."""

    def __init__(self, *, deadband_w: float = 20.0) -> None:
        """Create a zero-grid-target controller with a deadband in watts."""
        if deadband_w < 0:
            raise ValueError("deadband_w cannot be negative")
        self.deadband_w = deadband_w

    def calculate_setpoint(
        self,
        *,
        grid_power_w: float,
        state: BatteryState,
        profile: BatteryProfile,
        device_max_output_power_w: float,
        global_max_output_power_w: float,
    ) -> float:
        """Return signed AC power: positive charges and negative discharges."""
        if not state.available:
            return 0.0
        if abs(grid_power_w) <= self.deadband_w:
            return 0.0

        requested_power_w = -grid_power_w
        if requested_power_w > 0:
            if state.soc_percent >= profile.max_soc_percent:
                return 0.0
            return min(requested_power_w, profile.max_charge_power_w)

        if state.soc_percent <= profile.min_soc_percent:
            return 0.0
        output_limit_w = min(
            profile.max_discharge_power_w,
            max(0.0, device_max_output_power_w),
            max(0.0, global_max_output_power_w),
        )
        return max(requested_power_w, -output_limit_w)
