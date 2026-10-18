import asyncio
from datetime import datetime, timedelta, UTC

from src.bems.controller import GridBalancingController
from src.bems.domain import align_power_samples, BatteryPowerCommand, BatteryProfile, BatteryState, normalize_grid_meter
from src.bems.economics import interval_cost_cents
from src.bems.simulation import SimulatedBattery
from src.bems.vendor_mocks import MockBatteryEndpoint
from src.collectors.definitions.common.measurement import Measurement
from src.config.config import _load_bems_config, BemsTariffConfig, config_obj

import pytest


def profile(**overrides):
    values = {
        "id": "test",
        "capacity_wh": 1000.0,
        "min_soc_percent": 10.0,
        "max_soc_percent": 100.0,
        "max_charge_power_w": 1000.0,
        "max_discharge_power_w": 1000.0,
        "charge_efficiency": 0.9,
        "discharge_efficiency": 0.8,
    }
    values.update(overrides)
    return BatteryProfile(**values)


def measurement(metric, value, *, source="meter", timestamp=None, unit="W"):
    return Measurement(timestamp or datetime(2026, 1, 1, tzinfo=UTC), source, metric, value, unit)


def test_default_bems_profiles_are_loaded():
    assert config_obj.bems is not None
    assert config_obj.bems.enabled
    assert config_obj.bems.profiles["anker_solarbank_max_ac"].capacity_wh == 7000
    assert config_obj.bems.profiles["marstek_venus_e_3_5120"].capacity_wh == 5120


def test_bems_config_rejects_unknown_profile_reference():
    with pytest.raises(ValueError, match="unknown profile"):
        _load_bems_config({"bems": {"profiles": {}, "devices": [{"id": "x", "profile": "missing"}]}})


def test_meter_sign_is_normalized_and_alignment_does_not_look_ahead():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    assert normalize_grid_meter(measurement("grid_active_power", -420, timestamp=t0)) == 420
    grid = [measurement("grid_active_power", -420, timestamp=t0 + timedelta(seconds=5))]
    household_future = [measurement("grid_active_power", -200, timestamp=t0 + timedelta(seconds=6))]
    assert align_power_samples(grid, household_future, [], tolerance_seconds=10) == []


def test_battery_applies_charge_and_discharge_efficiencies():
    battery = SimulatedBattery(
        profile=profile(), initial_soc_percent=50, max_output_power_w=1000, ramp_seconds=0, timestamp=datetime(2026, 1, 1, tzinfo=UTC)
    )
    asyncio.run(battery.set_power(BatteryPowerCommand(battery.timestamp, 100)))
    state = battery.advance_to(battery.timestamp + timedelta(hours=1))
    assert state.energy_wh == pytest.approx(590)
    assert battery.charge_loss_wh == pytest.approx(10)

    asyncio.run(battery.set_power(BatteryPowerCommand(battery.timestamp, -80)))
    state = battery.advance_to(battery.timestamp + timedelta(hours=1))
    assert state.energy_wh == pytest.approx(490)
    assert battery.discharge_loss_wh == pytest.approx(20)


def test_battery_ramp_and_soc_boundary_limit_actual_energy():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    battery = SimulatedBattery(profile=profile(), initial_soc_percent=99.9, max_output_power_w=1000, ramp_seconds=10, timestamp=t0)
    asyncio.run(battery.set_power(BatteryPowerCommand(t0, 1000)))
    state = battery.advance_to(t0 + timedelta(seconds=10))
    assert state.energy_wh <= 1000
    assert battery.charge_loss_wh > 0


def test_controller_clamps_output_and_honors_soc_limits():
    controller = GridBalancingController(deadband_w=20)
    state = BatteryState(datetime.now(UTC), 50, 500, 0, 0)
    assert (
        controller.calculate_setpoint(grid_power_w=1500, state=state, profile=profile(), device_max_output_power_w=700, global_max_output_power_w=800)
        == -700
    )
    low = BatteryState(datetime.now(UTC), 10, 100, 0, 0)
    assert (
        controller.calculate_setpoint(grid_power_w=300, state=low, profile=profile(), device_max_output_power_w=700, global_max_output_power_w=800)
        == 0
    )


def test_costs_use_household_tariff_and_export_tariff():
    sample = type("Sample", (), {"grid_w": 1000, "household_w": 600, "heat_pump_w": 400})()
    tariffs = BemsTariffConfig(7.8, 22, 30)
    baseline, with_battery = interval_cost_cents(sample, battery_power_start_w=0, battery_power_end_w=-600, duration_seconds=3600, tariffs=tariffs)
    assert baseline == pytest.approx(26.8)
    assert with_battery == pytest.approx(17.8)


def test_vendor_mocks_model_protocol_shapes_and_failure_modes():
    marstek = MockBatteryEndpoint("marstek", max_power_w=1000)
    response = asyncio.run(marstek.request_marstek("ES.SetMode", {"power": 1500}))
    assert response["result"]["power"] == 1000
    assert asyncio.run(marstek.request_marstek("ES.GetStatus"))["result"]["soc"] == 50

    anker = MockBatteryEndpoint("anker")
    asyncio.run(anker.set_anker_power(-5000))
    assert asyncio.run(anker.read_anker_registers())[1] == -3500
    anker.fail_next = True
    with pytest.raises(TimeoutError):
        asyncio.run(anker.read_anker_registers())
