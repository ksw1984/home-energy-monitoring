from src.bems.core.domain import BatteryProfile, BatteryState


class GridBalancingController:
    """Simple proportional controller that targets zero grid power."""

    def __init__(self, *, deadband_w: float = 20.0) -> None:
        """Create a zero-grid-target controller with a deadband in watts."""
        if deadband_w < 0:
            raise ValueError("deadband_w cannot be negative")
        self.deadband_w = deadband_w

    def calculate_setpoint(  # noqa: PLR0913, PLR0911 - keep setpoint inputs explicit and safety branches readable.
        self,
        *,
        grid_power_w: float,
        state: BatteryState,
        profile: BatteryProfile,
        device_max_output_power_w: float,
        global_max_output_power_w: float,
        household_power_w: float | None = None,
        heat_pump_power_w: float = 0.0,
        heat_pump_reserve_wh: float = 0.0,
        control_interval_s: float = 10.0,
    ) -> float:
        """Return signed AC power: positive charges and negative discharges."""
        if not state.available:
            return 0.0
        if grid_power_w < -self.deadband_w:
            requested_power_w = -grid_power_w
            if state.soc_percent >= profile.max_soc_percent:
                return 0.0
            charge_limit_w = min(
                profile.max_charge_power_w,
                max(0.0, device_max_output_power_w),
                max(0.0, global_max_output_power_w),
            )
            return min(requested_power_w, charge_limit_w)

        if grid_power_w <= self.deadband_w:
            return 0.0

        if state.soc_percent <= profile.min_soc_percent:
            return 0.0
        output_limit_w = min(
            profile.max_discharge_power_w,
            max(0.0, device_max_output_power_w),
            max(0.0, global_max_output_power_w),
        )
        if output_limit_w <= 0:
            return 0.0

        # Keep the configured import margin at the grid connection. This cap
        # applies to the combined household and heat-pump discharge request.
        grid_headroom_w = max(0.0, grid_power_w - self.deadband_w)
        if grid_headroom_w <= 0:
            return 0.0

        # Household demand has first claim on available battery output. Heat-pump
        # demand is served only by energy above the reserve for the coming night.
        household_demand_w = max(0.0, household_power_w if household_power_w is not None else grid_power_w)
        household_discharge_w = min(household_demand_w, output_limit_w, grid_headroom_w)
        energy_above_reserve_wh = max(0.0, state.energy_wh - heat_pump_reserve_wh)
        reserve_limited_hp_w = energy_above_reserve_wh * profile.discharge_efficiency * 3600.0 / max(1.0, control_interval_s)
        heat_pump_discharge_w = min(
            max(0.0, heat_pump_power_w),
            max(0.0, output_limit_w - household_discharge_w),
            max(0.0, grid_headroom_w - household_discharge_w),
            reserve_limited_hp_w,
        )
        return -(household_discharge_w + heat_pump_discharge_w)
