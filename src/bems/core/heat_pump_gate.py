"""Entry gate for heat-pump discharge from energy above the night reserve.

A surplus that only covers a few seconds of heat-pump load should not trigger a
short full-power burst. Discharge is admitted only when the surplus covers the
configured sustain window at a representative heat-pump load. Once admitted,
the gate does not re-check while the heat pump keeps drawing: rising demand
consumes the surplus faster and the discharge simply ends at the reserve.
"""

from dataclasses import dataclass


@dataclass
class HeatPumpDischargeGate:
    """Stateful entry decision for one battery's heat-pump discharge."""

    min_sustain_s: float
    reference_power_w: float

    def __post_init__(self) -> None:
        """Reject negative settings; zero sustain time disables the gate."""
        if self.min_sustain_s < 0 or self.reference_power_w < 0:
            raise ValueError("Heat-pump sustain time and reference power cannot be negative")

    @property
    def required_ac_wh(self) -> float:
        """AC energy needed to cover the sustain window at the reference load."""
        return self.reference_power_w * self.min_sustain_s / 3600.0

    def allows(
        self,
        *,
        active: bool,
        surplus_wh: float,
        heat_pump_power_w: float,
        discharge_efficiency: float,
    ) -> bool:
        """Return whether heat-pump discharge may start or continue.

        ``surplus_wh`` is cell-side energy above the night reserve. An active
        discharge is only stopped when the surplus is gone or the heat pump
        stops drawing; the sustain check applies to entry only.
        """
        if heat_pump_power_w <= 0 or discharge_efficiency <= 0:
            return False
        if active:
            return surplus_wh > 0.0
        return surplus_wh * discharge_efficiency >= self.required_ac_wh
