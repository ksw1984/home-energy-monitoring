"""Historical replay and persistence for independent BEMS simulation runs."""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

from src.bems.controller import GridBalancingController
from src.bems.domain import align_power_samples, BatteryPowerCommand, BatteryProfile, BemsPowerSample
from src.bems.economics import interval_cost_cents
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
    output_databases: list[BaseDatabase],
    device_ids: set[str] | None = None,
    run_id: str | None = None,
    resume: bool = False,
) -> str:
    """Replay available meter points and persist results under a unique run ID.

    A normal run starts at each configured SOC. ``resume`` restores the last
    complete checkpoint for the explicit run ID and skips processed timestamps.
    Input data is consumed in one-hour chunks to bound each query.
    """
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

    if resume and not run_id:
        raise ValueError("--resume requires an existing --run-id")
    run_id = run_id or uuid4().hex[:12]
    controller = GridBalancingController(deadband_w=config.control.deadband_w)
    batteries: dict[str, SimulatedBattery] = {}
    previous_samples = {}
    previous_actual_powers: dict[str, float] = {}
    reference_costs: dict[str, float] = {}
    battery_costs: dict[str, float] = {}
    outputs: list[Measurement] = []
    processed_samples = 0

    if resume:
        for device in selected:
            source = _simulation_source(device.id, run_id)
            checkpoint = await _load_checkpoint(history_database, source, start)
            if checkpoint is None:
                raise RuntimeError(f"No saved checkpoint found for {source}; cannot resume")
            checkpoint_time, values = checkpoint
            if checkpoint_time >= stop:
                raise ValueError("resume stop must be later than the saved checkpoint")
            batteries[device.id] = _create_battery(
                device,
                config,
                checkpoint_time,
                energy_wh=values["energy_stored"],
                charge_loss_wh=values["charge_loss_total"],
                discharge_loss_wh=values["discharge_loss_total"],
                actual_power_w=values["actual_power"],
                target_power_w=values["requested_power"],
            )
            previous_samples[device.id] = _sample_from_checkpoint(checkpoint_time, values)
            previous_actual_powers[device.id] = values["actual_power"]
            reference_costs[device.id] = values["cost_reference_total"]
            battery_costs[device.id] = values["cost_with_battery_total"]

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
                # A resumed run can reread its initial range; skip checkpoints
                # already persisted so neither energy nor costs are counted twice.
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
                )
                await battery.set_power(BatteryPowerCommand(timestamp=sample.timestamp, power_w=setpoint))
                source = _simulation_source(device.id, run_id)
                # Metrics share the source and timestamp but remain separate
                # measurements, including cumulative costs and restart state.
                outputs.extend(
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
                        Measurement(sample.timestamp, source, "checkpoint_grid_w", sample.grid_w, "W"),
                        Measurement(sample.timestamp, source, "checkpoint_household_w", sample.household_w, "W"),
                        Measurement(sample.timestamp, source, "checkpoint_heat_pump_w", sample.heat_pump_w, "W"),
                        Measurement(sample.timestamp, source, "checkpoint_pv_w", sample.pv_w or 0.0, "W"),
                    ]
                )
                previous_samples[device.id] = sample
                previous_actual_powers[device.id] = actual_power

        await _store_in_batches(output_databases, outputs)
        outputs.clear()
        chunk_start = chunk_stop

    if processed_samples == 0:
        raise RuntimeError("No matching historical grid and household measurement points found in the requested range")
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


CHECKPOINT_METRICS = (
    "requested_power",
    "actual_power",
    "energy_stored",
    "charge_loss_total",
    "discharge_loss_total",
    "cost_reference_total",
    "cost_with_battery_total",
    "checkpoint_grid_w",
    "checkpoint_household_w",
    "checkpoint_heat_pump_w",
    "checkpoint_pv_w",
)


async def _load_checkpoint(database: BaseDatabase, source: str, start: datetime):
    """Find the latest timestamp where every required checkpoint metric exists."""
    series = await asyncio.gather(*(_query_series(database, source=source, metric=metric, start=start, stop=None) for metric in CHECKPOINT_METRICS))
    by_metric = dict(zip(CHECKPOINT_METRICS, series, strict=True))
    common_times = set.intersection(*({point.timestamp for point in points} for points in series))
    if not common_times:
        return None
    timestamp = max(common_times)
    values = {metric: next(point.value for point in by_metric[metric] if point.timestamp == timestamp) for metric in CHECKPOINT_METRICS}
    return timestamp, values


def _sample_from_checkpoint(timestamp: datetime, values: dict[str, float]):
    """Restore prior cascade readings needed to continue interval costing."""
    return BemsPowerSample(
        timestamp=timestamp,
        grid_w=values["checkpoint_grid_w"],
        household_w=values["checkpoint_household_w"],
        heat_pump_w=values["checkpoint_heat_pump_w"],
        pv_w=values["checkpoint_pv_w"],
    )


def _simulation_source(device_id: str, run_id: str) -> str:
    """Build a stable, run-scoped measurement source for one simulation device."""
    return f"bems_sim_{device_id}_{run_id}"


async def _store_in_batches(databases: list[BaseDatabase], measurements: list[Measurement]) -> None:
    """Write one already bounded result batch to every enabled database."""
    if not measurements:
        return
    for database in databases:
        if database.enabled:
            await database.store(measurements)
