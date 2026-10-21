import asyncio
from datetime import datetime, timedelta, UTC

from src.bems.controller import GridBalancingController
from src.bems.domain import (
    align_power_samples,
    BatteryPowerCommand,
    BatteryProfile,
    BatteryState,
    estimate_power_without_battery,
    normalize_grid_meter,
)
from src.bems.economics import interval_cost_cents
from src.bems.reserve import night_reserve_wh, ZeroSolarForecastProvider
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


def test_meter_import_sign_and_alignment_do_not_look_ahead():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    assert normalize_grid_meter(measurement("grid_active_power", 420, timestamp=t0)) == 420
    grid = [measurement("grid_active_power", 420, timestamp=t0 + timedelta(seconds=5))]
    household_future = [measurement("grid_active_power", 200, timestamp=t0 + timedelta(seconds=6))]
    assert align_power_samples(grid, household_future, [], tolerance_seconds=10) == []


def test_legacy_dlms_power_without_unit_is_interpreted_as_kw():
    point = measurement(
        "grid_active_power",
        0.42,
        source="meter_grid_dlms",
        unit="",
    )

    assert normalize_grid_meter(point) == pytest.approx(420)


def test_positive_meter_import_triggers_discharge_and_soc_falls():
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    sample = align_power_samples(
        [measurement("grid_active_power", 230, source="meter_grid_dlms", timestamp=timestamp)],
        [measurement("grid_active_power", 210, source="meter_household_dlms", timestamp=timestamp)],
        [],
        tolerance_seconds=10,
    )[0]
    battery = SimulatedBattery(profile=profile(), initial_soc_percent=50, max_output_power_w=800, ramp_seconds=0, timestamp=timestamp)
    controller = GridBalancingController(deadband_w=20)
    state = asyncio.run(battery.get_state())
    setpoint = controller.calculate_setpoint(
        grid_power_w=sample.grid_w,
        household_power_w=sample.household_w,
        heat_pump_power_w=sample.heat_pump_w,
        heat_pump_reserve_wh=100,
        state=state,
        profile=battery.profile,
        device_max_output_power_w=800,
        global_max_output_power_w=800,
    )
    asyncio.run(battery.set_power(BatteryPowerCommand(timestamp, setpoint)))
    state_after_discharge = battery.advance_to(timestamp + timedelta(minutes=1))

    assert sample.grid_w == 230
    assert setpoint == -210
    assert sample.grid_w + setpoint == 20
    assert battery.actual_power_w < 0
    assert state_after_discharge.soc_percent < state.soc_percent


def test_baseline_estimate_uses_actual_battery_delivery_not_requested_power():
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    actual = BatteryState(timestamp, 50, 500, charge_power_w=0, discharge_power_w=480)

    assert estimate_power_without_battery(20, actual) == 500

    charging = BatteryState(timestamp, 50, 500, charge_power_w=300, discharge_power_w=0)
    assert estimate_power_without_battery(800, charging) == 500


def test_export_requests_charge_but_honors_800_w_global_and_device_limits():
    controller = GridBalancingController(deadband_w=20)
    setpoint = controller.calculate_setpoint(
        grid_power_w=-2000,
        state=BatteryState(datetime(2026, 1, 1, tzinfo=UTC), 50, 500, 0, 0),
        profile=profile(max_charge_power_w=3500),
        device_max_output_power_w=800,
        global_max_output_power_w=800,
    )

    assert setpoint == 800


def test_simulated_battery_caps_charge_and_discharge_at_configured_limit():
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    battery = SimulatedBattery(
        profile=profile(max_charge_power_w=3500), initial_soc_percent=50, max_output_power_w=800, ramp_seconds=0, timestamp=timestamp
    )

    asyncio.run(battery.set_power(BatteryPowerCommand(timestamp, 2500)))
    assert battery.actual_power_w == 800

    asyncio.run(battery.set_power(BatteryPowerCommand(timestamp, -2500)))
    assert battery.actual_power_w == -800


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


def test_repeated_identical_setpoint_does_not_restart_power_ramp():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    battery = SimulatedBattery(profile=profile(), initial_soc_percent=50, max_output_power_w=1000, ramp_seconds=10, timestamp=t0)

    asyncio.run(battery.set_power(BatteryPowerCommand(t0, 500)))
    for second in range(1, 11):
        timestamp = t0 + timedelta(seconds=second)
        battery.advance_to(timestamp)
        asyncio.run(battery.set_power(BatteryPowerCommand(timestamp, 500)))

    assert battery.actual_power_w == 500


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


def test_controller_prioritizes_household_and_reserves_energy_from_heat_pump():
    controller = GridBalancingController(deadband_w=20)
    profile_ = profile()
    state = BatteryState(datetime(2026, 1, 1, tzinfo=UTC), 50, 500, 0, 0)
    kwargs = {
        "grid_power_w": 1000,
        "state": state,
        "profile": profile_,
        "device_max_output_power_w": 800,
        "global_max_output_power_w": 800,
        "household_power_w": 300,
        "heat_pump_power_w": 700,
        "control_interval_s": 10,
    }
    assert controller.calculate_setpoint(**kwargs, heat_pump_reserve_wh=500) == -300
    assert controller.calculate_setpoint(**kwargs, heat_pump_reserve_wh=0) == -800


def test_controller_keeps_configured_import_margin_and_prevents_grid_export():
    controller = GridBalancingController(deadband_w=20)
    setpoint = controller.calculate_setpoint(
        grid_power_w=500,
        state=BatteryState(datetime(2026, 1, 1, tzinfo=UTC), 50, 500, 0, 0),
        profile=profile(),
        device_max_output_power_w=800,
        global_max_output_power_w=800,
        household_power_w=300,
        heat_pump_power_w=700,
        heat_pump_reserve_wh=0,
    )

    assert setpoint == -480
    assert 500 + setpoint == 20


def test_night_reserve_and_forecast_default():
    timestamp = datetime(2026, 1, 15, 12, tzinfo=UTC)
    reserve_profile = profile(capacity_wh=7000)
    reserve = night_reserve_wh(
        timestamp=timestamp,
        profile=reserve_profile,
        baseline_household_w=250,
        latitude=52.4,
        longitude=13.7,
        timezone="Europe/Berlin",
    )
    assert reserve > 100
    before_dawn_reserve = night_reserve_wh(
        timestamp=datetime(2026, 1, 15, 2, tzinfo=UTC),
        profile=reserve_profile,
        baseline_household_w=250,
        latitude=52.4,
        longitude=13.7,
        timezone="Europe/Berlin",
    )
    assert reserve == 700
    assert before_dawn_reserve > reserve
    assert before_dawn_reserve <= reserve_profile.capacity_wh
    assert ZeroSolarForecastProvider().expected_pv_energy_kwh(timestamp) == 0


def test_daytime_reserve_does_not_hold_energy_for_the_following_night():
    local_morning = datetime(2026, 10, 19, 5, 5, tzinfo=UTC)
    reserve_profile = profile(capacity_wh=7000, max_discharge_power_w=3500)
    reserve = night_reserve_wh(
        timestamp=local_morning,
        profile=reserve_profile,
        baseline_household_w=250,
        latitude=52.4,
        longitude=13.7,
        timezone="Europe/Berlin",
    )
    assert reserve > 1037

    state = BatteryState(local_morning, 51, 3570, 0, 0)
    setpoint = GridBalancingController().calculate_setpoint(
        grid_power_w=2260,
        household_power_w=460,
        heat_pump_power_w=1800,
        heat_pump_reserve_wh=reserve,
        state=state,
        profile=reserve_profile,
        device_max_output_power_w=2000,
        global_max_output_power_w=2000,
    )
    assert setpoint == -2000


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
