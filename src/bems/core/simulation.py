"""Stateful AC-coupled battery simulation with losses, limits, and ramping."""

from datetime import datetime, timedelta
from itertools import pairwise

from src.bems.core.domain import BatteryPowerCommand, BatteryProfile, BatteryState


class SimulatedBattery:
    """AC-coupled battery model with ramping and round-trip losses."""

    def __init__(  # noqa: PLR0913 - explicit state parameters make checkpoint restoration deterministic.
        self,
        *,
        profile: BatteryProfile,
        initial_soc_percent: float = 50.0,
        max_output_power_w: float = 800.0,
        ramp_seconds: float = 10.0,
        timestamp: datetime | None = None,
        energy_wh: float | None = None,
        charge_loss_wh: float = 0.0,
        discharge_loss_wh: float = 0.0,
        actual_power_w: float = 0.0,
        target_power_w: float = 0.0,
    ) -> None:
        """Initialize from SOC or a checkpoint; powers use signed AC watts.

        ``max_output_power_w`` limits AC charge and discharge. Checkpoint energy and
        cumulative losses use Wh.
        """
        if not profile.min_soc_percent <= initial_soc_percent <= profile.max_soc_percent:
            raise ValueError("initial SOC must be within the profile limits")
        if max_output_power_w < 0 or ramp_seconds < 0:
            raise ValueError("power and ramp limits cannot be negative")

        self.profile = profile
        self.max_output_power_w = min(max_output_power_w, profile.max_discharge_power_w)
        self.ramp_seconds = ramp_seconds
        self.energy_wh = profile.capacity_wh * initial_soc_percent / 100.0 if energy_wh is None else energy_wh
        if not self._min_energy_wh() <= self.energy_wh <= self._max_energy_wh():
            raise ValueError("restored energy must be within the profile SOC limits")
        self.charge_loss_wh = charge_loss_wh
        self.discharge_loss_wh = discharge_loss_wh
        self.timestamp = timestamp
        self.target_power_w = self._power_limit(target_power_w)
        self.actual_power_w = self._power_limit(actual_power_w)
        self._ramp_start_power_w = self.actual_power_w
        self._ramp_start: datetime | None = timestamp

    @property
    def soc_percent(self) -> float:
        """Return stored energy as a percentage of nominal capacity."""
        return 100.0 * self.energy_wh / self.profile.capacity_wh

    async def set_power(self, command: BatteryPowerCommand) -> None:
        """Advance to command time and begin ramping toward its bounded target."""
        self.advance_to(command.timestamp)
        limit = self._power_limit(command.power_w)
        if limit == self.target_power_w:
            return
        self._ramp_start_power_w = self.actual_power_w
        self._ramp_start = command.timestamp
        self.target_power_w = limit
        if self.ramp_seconds == 0:
            self.actual_power_w = self.target_power_w

    async def get_state(self) -> BatteryState:
        """Return the normalized state without advancing simulation time."""
        timestamp = self.timestamp
        if timestamp is None:
            raise RuntimeError("simulated battery has no timestamp yet")
        return self._state_at(timestamp)

    def advance_to(self, timestamp: datetime) -> BatteryState:
        """Integrate signed power and conversion losses up to ``timestamp``."""
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("simulation timestamps must be timezone-aware")
        if self.timestamp is None:
            self.timestamp = timestamp
            self._ramp_start = timestamp
            return self._state_at(timestamp)
        if timestamp < self.timestamp:
            raise ValueError("simulation time cannot move backwards")

        start = self.timestamp
        duration = (timestamp - start).total_seconds()
        if duration == 0:
            return self._state_at(timestamp)

        boundaries = {start, timestamp}
        if self._ramp_start is not None and self.ramp_seconds > 0:
            ramp_end_time = self._ramp_start + timedelta(seconds=self.ramp_seconds)
            if start < ramp_end_time < timestamp:
                boundaries.add(ramp_end_time)
        ordered = sorted(boundaries)
        saturated = False
        for left, right in pairwise(ordered):
            left_power = self._power_at(left)
            right_power = self._power_at(right)
            seconds = (right - left).total_seconds()
            if left_power * right_power < 0:
                zero_fraction = abs(left_power) / (abs(left_power) + abs(right_power))
                zero_time = left + (right - left) * zero_fraction
                saturated |= self._integrate_segment(left_power, 0.0, (zero_time - left).total_seconds())
                saturated |= self._integrate_segment(0.0, right_power, (right - zero_time).total_seconds())
            else:
                saturated |= self._integrate_segment(left_power, right_power, seconds)

        self.timestamp = timestamp
        if saturated:
            self.actual_power_w = 0.0
            self.target_power_w = 0.0
            self._ramp_start_power_w = 0.0
            self._ramp_start = timestamp
        else:
            self.actual_power_w = self._power_at(timestamp)
        return self._state_at(timestamp)

    def skip_to(self, timestamp: datetime) -> BatteryState:
        """Move past a data gap without assuming any power flow."""
        """Skip a data gap with zero flow and cancel any pending ramp."""
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("simulation timestamps must be timezone-aware")
        if self.timestamp is not None and timestamp < self.timestamp:
            raise ValueError("simulation time cannot move backwards")
        self.timestamp = timestamp
        self.target_power_w = 0.0
        self.actual_power_w = 0.0
        self._ramp_start_power_w = 0.0
        self._ramp_start = timestamp
        return self._state_at(timestamp)

    def _integrate_segment(self, start_power_w: float, end_power_w: float, seconds: float) -> bool:
        """Integrate a linear segment and report whether an SOC bound clips it."""
        if seconds <= 0:
            return False
        average_power_w = (start_power_w + end_power_w) / 2.0
        if average_power_w == 0:
            return False
        requested_energy_wh = abs(average_power_w) * seconds / 3600.0

        if average_power_w > 0:
            available_internal_wh = max(0.0, self._max_energy_wh() - self.energy_wh)
            ac_energy_wh = min(requested_energy_wh, available_internal_wh / self.profile.charge_efficiency)
            internal_delta_wh = ac_energy_wh * self.profile.charge_efficiency
            self.energy_wh += internal_delta_wh
            self.charge_loss_wh += ac_energy_wh - internal_delta_wh
        else:
            available_internal_wh = max(0.0, self.energy_wh - self._min_energy_wh())
            ac_energy_wh = min(requested_energy_wh, available_internal_wh * self.profile.discharge_efficiency)
            internal_delta_wh = ac_energy_wh / self.profile.discharge_efficiency
            self.energy_wh -= internal_delta_wh
            self.discharge_loss_wh += internal_delta_wh - ac_energy_wh

        self.energy_wh = min(self._max_energy_wh(), max(self._min_energy_wh(), self.energy_wh))
        return ac_energy_wh + 1e-9 < requested_energy_wh

    def _power_at(self, timestamp: datetime) -> float:
        """Evaluate the configured linear power ramp at a timestamp."""
        if self._ramp_start is None or self.ramp_seconds == 0:
            return self.target_power_w
        elapsed = max(0.0, (timestamp - self._ramp_start).total_seconds())
        fraction = min(1.0, elapsed / self.ramp_seconds)
        return self._ramp_start_power_w + (self.target_power_w - self._ramp_start_power_w) * fraction

    def _power_limit(self, power_w: float) -> float:
        """Clamp charge/discharge commands to their distinct AC limits."""
        if power_w > 0:
            return min(power_w, self.profile.max_charge_power_w, self.max_output_power_w)
        return max(power_w, -self.max_output_power_w)

    def _min_energy_wh(self) -> float:
        """Return the minimum permitted stored energy in Wh."""
        return self.profile.capacity_wh * self.profile.min_soc_percent / 100.0

    def _max_energy_wh(self) -> float:
        """Return the maximum permitted stored energy in Wh."""
        return self.profile.capacity_wh * self.profile.max_soc_percent / 100.0

    def _state_at(self, timestamp: datetime) -> BatteryState:
        """Build an immutable public snapshot from the internal state."""
        return BatteryState(
            timestamp=timestamp,
            soc_percent=self.soc_percent,
            energy_wh=self.energy_wh,
            charge_power_w=max(0.0, self.actual_power_w),
            discharge_power_w=max(0.0, -self.actual_power_w),
        )
