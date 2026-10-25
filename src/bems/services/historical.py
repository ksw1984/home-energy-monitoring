"""Historical replay and local report generation for independent BEMS runs."""

import asyncio
import logging
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path

from src.bems.core.controller import GridBalancingController
from src.bems.core.domain import align_power_samples, BatteryPowerCommand, BatteryProfile, estimate_power_without_battery
from src.bems.core.economics import interval_cost_cents
from src.bems.core.reserve import night_reserve_wh
from src.bems.core.simulation import SimulatedBattery
from src.bems.reporting.report import append_measurements, build_device_report
from src.collectors.definitions.common.measurement import Measurement
from src.config.config import BemsConfig, BemsDeviceConfig
from src.databases.base_database import BaseDatabase

QUERY_PAGE_SIZE = 1000
SIMULATION_CHUNK = timedelta(hours=1)
logger = logging.getLogger(__name__)


def _measurement_cadence(measurements: list[Measurement]) -> str:
    """Summarize raw sample spacing without printing every database point."""
    timestamps = sorted({item.timestamp for item in measurements})
    intervals = [(right - left).total_seconds() for left, right in pairwise(timestamps)]
    if not intervals:
        return "insufficient points"
    return f"median={statistics.median(intervals):.3f}s max={max(intervals):.3f}s"


def _measurement_value_range(measurements: list[Measurement]) -> str:
    """Summarize unnormalized values so meter-sign assumptions are visible."""
    if not measurements:
        return "no values"
    values = [item.value for item in measurements]
    return f"{min(values):+.3f}..{max(values):+.3f} {measurements[0].unit or '(unit missing)'}"


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

    if run_id is None:
        profile_parts = []
        for device in selected:
            profile = config.profiles[device.profile]
            effective_limit = min(
                device.max_output_power_w, config.control.max_output_power_w, profile.max_charge_power_w, profile.max_discharge_power_w
            )
            profile_parts.append(f"{device.id}-soc{device.initial_soc_percent:g}-max{device.max_output_power_w:g}-effective{effective_limit:g}")
        safe_start = start.isoformat(timespec="seconds").replace(":", "-")
        generated_at = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
        run_id = f"{safe_start}_{'_'.join(profile_parts)}_{generated_at}"
    report_dir = output_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    total_chunks = math.ceil((stop - start).total_seconds() / SIMULATION_CHUNK.total_seconds())
    logger.info(
        "Historical simulation %s started: %s to %s, devices=%s, output=%s",
        run_id,
        start.isoformat(),
        stop.isoformat(),
        ", ".join(device.id for device in selected),
        report_dir,
    )
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
    chunk_number = 0
    while chunk_start < stop:
        chunk_number += 1
        chunk_stop = min(chunk_start + SIMULATION_CHUNK, stop)
        logger.info(
            "[%d/%d] Reading grid, household and PV measurements for %s to %s",
            chunk_number,
            total_chunks,
            chunk_start.isoformat(),
            chunk_stop.isoformat(),
        )
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
        logger.info(
            "Fetched raw measurements: grid=%d (%s, raw %s), household=%d (%s, raw %s), PV=%d (%s, raw %s)",
            len(grid_measurements),
            _measurement_cadence(grid_measurements),
            _measurement_value_range(grid_measurements),
            len(household_measurements),
            _measurement_cadence(household_measurements),
            _measurement_value_range(household_measurements),
            len(pv_measurements),
            _measurement_cadence(pv_measurements),
            _measurement_value_range(pv_measurements),
        )
        samples = align_power_samples(
            grid_measurements,
            household_measurements,
            pv_measurements,
            tolerance_seconds=config.control.history_alignment_tolerance_s,
        )
        samples = [sample for sample in samples if chunk_start <= sample.timestamp < chunk_stop]
        processed_samples += len(samples)
        logger.info(
            "Alignment kept %d/%d grid points; %d grid points had no household reading within %.1fs",
            len(samples),
            len(grid_measurements),
            len(grid_measurements) - len(samples),
            config.control.history_alignment_tolerance_s,
        )
        logger.info("[%d/%d] Aligned %d measurement points; simulating %d device(s)", chunk_number, total_chunks, len(samples), len(selected))

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
                # Do not integrate energy through stale measurement periods;
                # this uses the configured live-data freshness limit.
                gap = previous_time is not None and (sample.timestamp - previous_time).total_seconds() > config.control.max_measurement_age_s
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
                household_with_battery_w = sample.household_w + actual_power
                setpoint = controller.calculate_setpoint(
                    # Replay a meter that includes the simulated battery, then
                    # recover its baseline from delivered (not requested) power.
                    grid_power_w=estimate_power_without_battery(grid_with_battery_w, state),
                    state=state,
                    profile=battery.profile,
                    device_max_output_power_w=device.max_output_power_w,
                    global_max_output_power_w=config.control.max_output_power_w,
                    household_power_w=estimate_power_without_battery(household_with_battery_w, state),
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
        logger.info(
            "[%d/%d] Simulation section complete; %d points processed in total",
            chunk_number,
            total_chunks,
            processed_samples,
        )
        chunk_start = chunk_stop

    if processed_samples == 0:
        raise RuntimeError("No matching historical grid and household measurement points found in the requested range")
    logger.info("Generating CSV summaries and plots for %d device(s)", len(selected))
    for index, device in enumerate(selected, start=1):
        logger.info("[%d/%d] Generating report and plots for %s", index, len(selected), device.id)
        build_device_report(
            measurement_path=result_files[device.id],
            report_dir=report_dir,
            device_id=device.id,
            timezone=config.night.timezone,
            chart_start=start,
            chart_stop=stop,
            max_interval_seconds=config.control.history_alignment_tolerance_s,
            capacity_wh=config.profiles[device.profile].capacity_wh,
            tariffs=config.tariffs,
            start_soc_percent=device.initial_soc_percent,
            configured_max_power_w=device.max_output_power_w,
            effective_max_power_w=min(
                device.max_output_power_w,
                config.control.max_output_power_w,
                config.profiles[device.profile].max_charge_power_w,
                config.profiles[device.profile].max_discharge_power_w,
            ),
        )
    (report_dir / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>BEMS simulation reports</title><h1>BEMS simulation reports</h1><ul>'
        + "".join(f'<li><a href="report_{device.id}.html">{device.id}</a></li>' for device in selected)
        + "</ul>",
        encoding="utf-8",
    )
    logger.info("Historical simulation complete: %d points; report index=%s", processed_samples, report_dir / "index.html")
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
