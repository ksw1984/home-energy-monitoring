from datetime import datetime, UTC

from src.bems.core.controller import GridBalancingController
from src.bems.core.domain import BatteryProfile, BatteryState
from src.bems.core.heat_pump_gate import HeatPumpDischargeGate

import pytest

EFFICIENCY = 0.97


def gate(min_sustain_s=60.0, reference_power_w=2000.0) -> HeatPumpDischargeGate:
    return HeatPumpDischargeGate(min_sustain_s=min_sustain_s, reference_power_w=reference_power_w)


def decision(gate_: HeatPumpDischargeGate, *, active=False, surplus_wh=20.0, heat_pump_power_w=1900.0):
    return gate_.allows(
        active=active,
        surplus_wh=surplus_wh,
        heat_pump_power_w=heat_pump_power_w,
        discharge_efficiency=EFFICIENCY,
    )


def profile() -> BatteryProfile:
    return BatteryProfile(
        id="test",
        capacity_wh=8000.0,
        min_soc_percent=5.0,
        max_soc_percent=100.0,
        max_charge_power_w=2000.0,
        max_discharge_power_w=2000.0,
        charge_efficiency=0.9,
        discharge_efficiency=EFFICIENCY,
    )


def test_gate_requires_energy_for_the_whole_sustain_window():
    assert gate().required_ac_wh == pytest.approx(2000 * 60 / 3600)
    # 20 Wh cell-side * 0.97 = 19.4 Wh AC, less than the required 33.3 Wh AC.
    assert decision(gate()) is False


def test_gate_admits_a_surplus_that_covers_the_window():
    exact = 2000 * 60 / 3600 / EFFICIENCY
    assert decision(gate(), surplus_wh=exact) is True
    assert decision(gate(), surplus_wh=36.0) is True


def test_active_discharge_continues_when_demand_rises():
    # 3 Wh is far below the entry threshold, but a started burst is not
    # re-evaluated; rising demand only consumes the surplus faster.
    assert decision(gate(), active=True, surplus_wh=3.0, heat_pump_power_w=3000.0) is True


def test_active_discharge_stops_at_the_reserve_or_when_heat_pump_stops():
    assert decision(gate(), active=True, surplus_wh=0.0) is False
    assert decision(gate(), active=True, heat_pump_power_w=0.0) is False


def test_zero_sustain_disables_the_gate():
    assert decision(gate(min_sustain_s=0.0), surplus_wh=0.1) is True


def test_negative_settings_are_rejected():
    with pytest.raises(ValueError):
        gate(min_sustain_s=-1)
    with pytest.raises(ValueError):
        gate(reference_power_w=-1)


def test_closed_gate_keeps_household_discharge_only():
    controller = GridBalancingController(deadband_w=20)
    state = BatteryState(datetime(2026, 1, 1, tzinfo=UTC), 50, 400, 0, 0)
    kwargs = {
        "grid_power_w": 2200,
        "state": state,
        "profile": profile(),
        "device_max_output_power_w": 2000,
        "global_max_output_power_w": 2000,
        "household_power_w": 300,
        "heat_pump_power_w": 1900,
        "control_interval_s": 10,
    }

    # Gate closed: hand the controller the current energy as its reserve, so
    # the heat-pump surplus is zero while household demand may still draw.
    blocked = controller.calculate_setpoint(**kwargs, minimum_household_reserve_wh=400)
    assert blocked == -300

    # Gate open: the original reserve is used and the heat pump gets the rest.
    admitted = controller.calculate_setpoint(**kwargs, minimum_household_reserve_wh=380)
    assert admitted == -2000
