"""Historical replay and local report generation for independent BEMS runs."""

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from src.bems.controller import GridBalancingController
from src.bems.domain import align_power_samples, BatteryPowerCommand, BatteryProfile
from src.bems.economics import interval_cost_cents
from src.bems.report import append_measurements, build_device_report
from src.bems.reserve import night_reserve_wh
from src.bems.simulation import SimulatedBattery
from src.collectors.definitions.common.measurement import Measurement
from src.config.config import BemsConfig, BemsDeviceConfig
from src.databases.base_database import BaseDatabase

QUERY_PAGE_SIZE = 1000
SIMULATION_CHUNK = timedelta(hours=1)


async def _query_series(
    database: BaseDatabase,
    *,
    source: str,
    metric: str,
    start: datetime | None,
    stop: datetime | None,
) -> list[Measurement]:
    """Read one source/metric in cursor-paginated timestamp order."""
    result: list[Measurement] = []
    cursor = None
    while True:
        page = await database.query_measurements(
            source=source,
            metrics=[metric],
            start=start if cursor is None else None,
            stop=stop,
            after=cursor,
            limit=QUERY_PAGE_SIZE,
        )
        if not page:
            return result
        result.extend(page)
        next_cursor = page[-1].timestamp
        if cursor is not None and next_cursor <= cursor:
            raise RuntimeError("Database query did not advance its timestamp cursor")
        cursor = next_cursor
        if len(page) < QUERY_PAGE_SIZE:
            return result


def _profile_from_config(profile_id: str, config: BemsConfig) -> BatteryProfile:
    """Translate a selected application profile to the simulation domain model."""
    item = config.profiles[profile_id]
    return BatteryProfile(
        id=profile_id,
        capacity_wh=item.capacity_wh,
        min_soc_percent=item.min_soc_percent,
        max_soc_percent=item.max_soc_percent,
        max_charge_power_w=item.max_charge_power_w,
        max_discharge_power_w=item.max_discharge_power_w,
        charge_efficiency=item.charge_efficiency,
        discharge_efficiency=item.discharge_efficiency,
    )


async def simulate_historical(  # noqa: C901, PLR0912, PLR0913, PLR0915 - orchestration keeps one ordered simulation state machine.
    *,
    start: datetime,
    stop: datetime,
    config: BemsConfig,
    history_database: BaseDatabase,
    output_dir: Path,
    device_ids: set[str] | None = None,
    run_id: str | None = None,
) -> str:
    """Replay meter history and write a local report without writing to databases."""
    if start.tzinfo is None or stop.tzinfo is None:
        raise ValueError("historical simulation range must include timezone offsets")
    if stop <= start:
        raise ValueError("simulation stop must be later than start")
    if not config.enabled:
        raise RuntimeError("BEMS is disabled in config.yaml")

    selected = [
        device for device in config.devices if device.enabled and device.mode == "simulation" and (device_ids is None or device.id in device_ids)
    ]
    if device_ids is not None:
        unknown = device_ids - {device.id for device in config.devices}
        if unknown:
            raise ValueError(f"Unknown BEMS devices: {', '.join(sorted(unknown))}")
    if not selected:
        raise ValueError("No enabled simulation devices selected")

    run_id = run_id or uuid4().hex[:12]
    report_dir = output_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    controller = GridBalancingController(deadband_w=config.control.deadband_w)
    batteries: dict[str, SimulatedBattery] = {}
    previous_samples = {}
    previous_actual_powers: dict[str, float] = {}
    reference_costs: dict[str, float] = {}
    battery_costs: dict[str, float] = {}
    outputs: dict[str, list[Measurement]] = defaultdict(list)
    result_files = {device.id: report_dir / f"measurements_{device.id}.jsonl" for device in selected}
    for result_file in result_files.values():
        result_file.write_text("", encoding="utf-8")
    processed_samples = 0

    chunk_start = start
    while chunk_start < stop:
        chunk_stop = min(chunk_start + SIMULATION_CHUNK, stop)
        input_start = chunk_start - timedelta(seconds=config.control.history_alignment_tolerance_s)
        grid_ref = config.inputs.grid
        household_ref = config.inputs.household
        pv_ref = config.inputs.pv
        grid_measurements, household_measurements, pv_measurements = await asyncio.gather(
            _query_series(
                history_database,
                source=grid_ref.source,
                metric=grid_ref.metric,
                start=chunk_start,
                stop=chunk_stop,
            ),
            _query_series(
                history_database,
                source=household_ref.source,
                metric=household_ref.metric,
                start=input_start,
                stop=chunk_stop,
            ),
            _query_series(
                history_database,
                source=pv_ref.source,
                metric=pv_ref.metric,
                start=input_start,
                stop=chunk_stop,
            ),
        )
        samples = align_power_samples(
            grid_measurements,
            household_measurements,
            pv_measurements,
            tolerance_seconds=config.control.history_alignment_tolerance_s,
        )
        samples = [sample for sample in samples if chunk_start <= sample.timestamp < chunk_stop]
        processed_samples += len(samples)

        for device in selected:
            battery = batteries.get(device.id)
            if battery is None:
                battery = _create_battery(device, config, samples[0].timestamp if samples else chunk_start)
                batteries[device.id] = battery
                reference_costs[device.id] = 0.0
                battery_costs[device.id] = 0.0
                previous_actual_powers[device.id] = 0.0

            for sample in samples:
                prior_checkpoint = previous_samples.get(device.id)
                if prior_checkpoint is not None and sample.timestamp <= prior_checkpoint.timestamp:
                    continue
                previous_time = battery.timestamp
                gap = previous_time is not None and (sample.timestamp - previous_time).total_seconds() > config.control.history_alignment_tolerance_s
                state = battery.skip_to(sample.timestamp) if gap else battery.advance_to(sample.timestamp)
                actual_power = battery.actual_power_w

                previous_sample = previous_samples.get(device.id)
                if previous_sample is not None and not gap:
                    duration_seconds = (sample.timestamp - previous_sample.timestamp).total_seconds()
                    reference_cost, with_battery_cost = interval_cost_cents(
                        previous_sample,
                        battery_power_start_w=previous_actual_powers[device.id],
                        battery_power_end_w=actual_power,
                        duration_seconds=duration_seconds,
                        tariffs=config.tariffs,
                    )
                    reference_costs[device.id] += reference_cost
                    battery_costs[device.id] += with_battery_cost

                grid_with_battery_w = sample.grid_w + actual_power
                setpoint = controller.calculate_setpoint(
                    grid_power_w=grid_with_battery_w,
                    state=state,
                    profile=battery.profile,
                    device_max_output_power_w=device.max_output_power_w,
                    global_max_output_power_w=config.control.max_output_power_w,
                    household_power_w=sample.household_w,
                    heat_pump_power_w=sample.heat_pump_w,
                    heat_pump_reserve_wh=night_reserve_wh(
                        timestamp=sample.timestamp,
                        profile=battery.profile,
                        baseline_household_w=config.night.baseline_household_w,
                        latitude=config.night.latitude,
                        longitude=config.night.longitude,
                        timezone=config.night.timezone,
                        dusk_margin_minutes=config.night.dusk_margin_minutes,
                        dawn_margin_minutes=config.night.dawn_margin_minutes,
                    ),
                    control_interval_s=config.control.history_alignment_tolerance_s or 10.0,
                )
                await battery.set_power(BatteryPowerCommand(timestamp=sample.timestamp, power_w=setpoint))
                source = _simulation_source(device.id, run_id)
                # Metrics share the source and timestamp but remain separate
                # measurements, including cumulative costs and restart state.
                outputs[device.id].extend(
                    [
                        Measurement(sample.timestamp, source, "requested_power", setpoint, "W"),
                        Measurement(sample.timestamp, source, "actual_power", actual_power, "W"),
                        Measurement(sample.timestamp, source, "soc", state.soc_percent, "%"),
                        Measurement(sample.timestamp, source, "energy_stored", state.energy_wh, "Wh"),
                        Measurement(sample.timestamp, source, "charge_loss_total", battery.charge_loss_wh, "Wh"),
                        Measurement(sample.timestamp, source, "discharge_loss_total", battery.discharge_loss_wh, "Wh"),
                        Measurement(sample.timestamp, source, "grid_power_reference", sample.grid_w, "W"),
                        Measurement(sample.timestamp, source, "grid_power_with_battery", grid_with_battery_w, "W"),
                        Measurement(sample.timestamp, source, "cost_reference_total", reference_costs[device.id], "ct"),
                        Measurement(sample.timestamp, source, "cost_with_battery_total", battery_costs[device.id], "ct"),
                        Measurement(
                            sample.timestamp,
                            source,
                            "savings_total",
                            reference_costs[device.id] - battery_costs[device.id],
                            "ct",
                        ),
                        Measurement(sample.timestamp, source, "household_power_reference", sample.household_w, "W"),
                        Measurement(sample.timestamp, source, "heat_pump_power_reference", sample.heat_pump_w, "W"),
                        Measurement(sample.timestamp, source, "pv_power_reference", sample.pv_w or 0.0, "W"),
                    ]
                )
                previous_samples[device.id] = sample
                previous_actual_powers[device.id] = actual_power

        for device in selected:
            append_measurements(result_files[device.id], outputs[device.id])
            outputs[device.id].clear()
        chunk_start = chunk_stop

    if processed_samples == 0:
        raise RuntimeError("No matching historical grid and household measurement points found in the requested range")
    for device in selected:
        build_device_report(
            measurement_path=result_files[device.id],
            report_dir=report_dir,
            device_id=device.id,
            timezone=config.night.timezone,
            chart_start=start,
            chart_stop=stop,
            max_interval_seconds=config.control.history_alignment_tolerance_s,
        )
    (report_dir / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>BEMS simulation reports</title><h1>BEMS simulation reports</h1><ul>'
        + "".join(f'<li><a href="report_{device.id}.html">{device.id}</a></li>' for device in selected)
        + "</ul>",
        encoding="utf-8",
    )
    return run_id


def _create_battery(device: BemsDeviceConfig, config: BemsConfig, start: datetime, **restored: float) -> SimulatedBattery:
    """Build one isolated battery, optionally restoring persisted state fields."""
    return SimulatedBattery(
        profile=_profile_from_config(device.profile, config),
        initial_soc_percent=device.initial_soc_percent,
        max_output_power_w=min(device.max_output_power_w, config.control.max_output_power_w),
        ramp_seconds=config.control.simulation_ramp_seconds,
        timestamp=start,
        **restored,
    )


def _simulation_source(device_id: str, run_id: str) -> str:
    """Build a stable, run-scoped measurement source for one simulation device."""
    return f"bems_sim_{device_id}_{run_id}"
