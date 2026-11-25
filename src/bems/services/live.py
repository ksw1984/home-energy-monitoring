"""Standalone polling and control loop for explicitly enabled live batteries."""

import asyncio
import logging
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any

from src.bems.core.controller import GridBalancingController
from src.bems.core.domain import (
    align_power_samples,
    Battery,
    BatteryIdentity,
    BatteryPowerCommand,
    BatteryProfile,
    BatteryState,
    BemsPowerSample,
    estimate_power_without_battery,
)
from src.bems.core.reserve import night_reserve_wh
from src.bems.services.command_log import BatteryCommandLog
from src.bems.services.night_baseline_service import NightBaselineProvider
from src.config.config import BemsConfig, BemsDeviceConfig
from src.databases.base_database import BaseDatabase
from src.shared.measurement import Measurement
from src.shared.measurement_bus import MeasurementBus

logger = logging.getLogger(__name__)
QUERY_PAGE_SIZE = 1000

# A zero max measurement age would otherwise spin the bus wait loop. The
# failsafe still runs every cycle; this only bounds the wake-up frequency.
MIN_BUS_WAIT_S = 0.1


class LiveControlService:
    """Control injected battery adapters from stored rows or the measurement bus."""

    def __init__(  # noqa: PLR0913 - each keyword is one explicit service input.
        self,
        *,
        config: BemsConfig,
        output_databases: list[BaseDatabase],
        batteries: dict[str, Battery],
        input_database: BaseDatabase | None = None,
        bus: MeasurementBus | None = None,
        poll_interval_s: float = 5.0,
        command_log_csv: str | Path | None = None,
        night_baseline: NightBaselineProvider | None = None,
    ) -> None:
        """Create a live service fed by a database or the measurement bus.

        Args:
            config: BEMS inputs, limits, profiles and device settings.
            output_databases: Databases receiving battery state and outcomes.
            batteries: Mapping of enabled live device ID to adapter.
            input_database: Database used by :meth:`run_once` and
                :meth:`run_forever`.
            bus: In-process stream used by :meth:`run_forever_bus`.
            poll_interval_s: Database polling interval for the standalone
                service.
            command_log_csv: Optional CSV path receiving one row per setpoint
                sent to any adapter, including mock adapters.
            night_baseline: Optional measured night-baseline provider. When
                configured, its value replaces the fixed household baseline
                for the heat-pump reserve.

        Raises:
            ValueError: If the poll interval is not positive, no input source
                is configured, or adapters do not match enabled live devices.
        """
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")
        if input_database is None and bus is None:
            raise ValueError("input_database or bus is required")
        self.config = config
        self.input_database = input_database
        self.bus = bus
        self.output_databases = output_databases
        self.batteries = batteries
        self.poll_interval_s = poll_interval_s
        self.command_log = BatteryCommandLog(command_log_csv) if command_log_csv else None
        self.night_baseline = night_baseline
        self.controller = GridBalancingController(deadband_w=config.control.deadband_w)
        self.devices = {device.id: device for device in config.devices if device.enabled and device.mode == "live"}
        missing = self.devices.keys() - batteries.keys()
        if missing:
            raise ValueError(f"Missing battery adapters for enabled live devices: {', '.join(sorted(missing))}")
        unknown = batteries.keys() - self.devices.keys()
        if unknown:
            raise ValueError(f"Adapters provided for non-live or disabled devices: {', '.join(sorted(unknown))}")
        # Newest grid timestamp already processed. In bus mode it is the
        # ``after_timestamp`` cursor; in database mode it is the page cursor.
        self._grid_cursor: datetime | None = None
        self._last_fresh_input: datetime | None = None
        self._stale = False
        self._last_requested_power: dict[str, float] = {}
        self._last_command_at: dict[str, datetime] = {}
        self._configuration_lock = asyncio.Lock()
        # Bus mode replaces this with the observed grid cadence; the database
        # path keeps the configured poll interval.
        self.control_interval_s = (
            config.control.hems_mqtt_publish_interval_s if config.control.feed == "mqtt" else config.control.bems_db_poll_interval_s
        )

    async def run_once(self, *, now: datetime | None = None) -> int:
        """Process each newly stored grid point once and return the count handled."""
        if self.input_database is None:
            raise RuntimeError("run_once() requires input_database; use run_forever_bus() for bus mode")
        current_time = now or datetime.now(UTC)
        self._require_aware(current_time)
        grid_ref = self.config.inputs.grid
        start = None if self._grid_cursor is not None else current_time - timedelta(seconds=self.config.control.live_max_measurement_age_s)
        grid_points = await self._query(
            source=grid_ref.source,
            metric=grid_ref.metric,
            start=start,
            after=self._grid_cursor,
            stop=current_time + timedelta(microseconds=1),
        )
        if not grid_points:
            if self._inputs_are_stale(current_time):
                await self._apply_stale_failsafe(current_time)
            return 0

        # Advance the cursor before device control so adapter errors cannot replay a meter row.
        self._grid_cursor = max(point.timestamp for point in grid_points)
        tolerance = self.config.control.history_alignment_tolerance_s
        household_points, pv_points = await asyncio.gather(
            self._query(
                source=self.config.inputs.household.source,
                metric=self.config.inputs.household.metric,
                start=min(point.timestamp for point in grid_points) - timedelta(seconds=tolerance),
                stop=self._grid_cursor + timedelta(microseconds=1),
            ),
            self._query(
                source=self.config.inputs.pv.source,
                metric=self.config.inputs.pv.metric,
                start=min(point.timestamp for point in grid_points) - timedelta(seconds=tolerance),
                stop=self._grid_cursor + timedelta(microseconds=1),
            ),
        )
        try:
            samples = align_power_samples(
                grid_points,
                household_points,
                pv_points,
                tolerance_seconds=tolerance,
            )
        except ValueError:
            logger.exception("BEMS rejected invalid live power measurements")
            await self._apply_stale_failsafe(current_time)
            return 0
        handled = 0
        for sample in samples:
            age = (current_time - sample.timestamp).total_seconds()
            fresh = 0 <= age <= self.config.control.live_max_measurement_age_s
            if not fresh:
                await self._apply_stale_failsafe(current_time)
                continue
            self._last_fresh_input = sample.timestamp
            self._stale = False
            await self._control_sample(sample, current_time)
            handled += 1

        if not samples:
            await self._apply_stale_failsafe(current_time)
        return handled

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        """Poll the input database until cancelled or an optional stop event is set."""
        if self.input_database is None:
            raise RuntimeError("run_forever() requires input_database; use run_forever_bus() for bus mode")
        while stop_event is None or not stop_event.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("BEMS live polling cycle failed")
                try:
                    await self._apply_stale_failsafe(datetime.now(UTC))
                except Exception:
                    logger.exception("BEMS could not apply the polling-error failsafe")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.poll_interval_s) if stop_event else await asyncio.sleep(self.poll_interval_s)
            except TimeoutError:
                continue

    async def run_forever_bus(self, stop_event: asyncio.Event | None = None) -> None:
        """Control from the in-process measurement bus until cancelled.

        One control cycle runs per new grid timestamp. A wait timeout means the
        grid stream stalled, so the zero-power failsafe is applied; the
        failsafe is idempotent and does not resend an already-requested zero.
        Household and PV values are taken from the bus at the moment the grid
        sample arrives, and the database is never queried on this path.

        Args:
            stop_event: Optional event checked between waits. Cancellation is
                the primary shutdown mechanism because an in-flight bus wait is
                not interrupted by the event.
        """
        if self.bus is None:
            raise RuntimeError("run_forever_bus() requires a measurement bus")

        while stop_event is None or not stop_event.is_set():
            try:
                if not self.config.enabled:
                    await self._apply_stale_failsafe(datetime.now(UTC))
                    await asyncio.sleep(1.0)
                    continue
                grid_ref = self.config.inputs.grid
                wait_timeout = max(MIN_BUS_WAIT_S, self.config.control.live_max_measurement_age_s)
                newer = await self.bus.wait_for_next(
                    source=grid_ref.source,
                    metric=grid_ref.metric,
                    after_timestamp=self._grid_cursor,
                    timeout=wait_timeout,
                )

                if stop_event is not None and stop_event.is_set():
                    break

                if newer:
                    await self._control_from_bus(datetime.now(UTC))
                else:
                    await self._apply_stale_failsafe(datetime.now(UTC))

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("BEMS live bus cycle failed")
                try:
                    await self._apply_stale_failsafe(datetime.now(UTC))
                except Exception:
                    logger.exception("BEMS could not apply the bus-error failsafe")

    async def _control_from_bus(self, now: datetime) -> None:
        """Run one control cycle from the newest bus values.

        The grid sample drives the cycle. Household is required by the cascade
        split; when it is missing or older than the live input window,
        ``align_power_samples`` returns nothing and the zero-power failsafe is
        requested, matching the database path. PV is optional and only enriches
        the sample.

        Args:
            now: Timezone-aware wall clock used for freshness checks.
        """
        if self.bus is None:
            raise RuntimeError("bus mode is not configured")
        if not self.config.enabled:
            return

        grid_ref = self.config.inputs.grid
        grid = self.bus.latest(grid_ref.source, grid_ref.metric)

        if grid is None:
            return

        if self._grid_cursor is not None and grid.timestamp <= self._grid_cursor:
            return

        previous_cursor = self._grid_cursor

        # Advance before device control so an adapter error cannot replay the
        # same meter sample on the next wake-up.
        self._grid_cursor = grid.timestamp

        if previous_cursor is not None:
            spacing = (grid.timestamp - previous_cursor).total_seconds()

            if spacing > 0:
                # The heat-pump reserve cap is expressed per control decision,
                # so the observed grid cadence is the honest interval. The
                # database path keeps using the configured poll interval.
                self.control_interval_s = spacing

        household = self.bus.latest(self.config.inputs.household.source, self.config.inputs.household.metric)
        pv = self.bus.latest(self.config.inputs.pv.source, self.config.inputs.pv.metric)

        try:
            samples = align_power_samples(
                [grid],
                [household] if household is not None else [],
                [pv] if pv is not None else [],
                # Live control must not use household data that is older than
                # the live input window. Historical replay keeps its wider
                # alignment tolerance for irregular database samples.
                tolerance_seconds=min(
                    self.config.control.history_alignment_tolerance_s,
                    self.config.control.live_max_measurement_age_s,
                ),
            )
        except ValueError:
            logger.exception("BEMS rejected invalid live power measurements from the bus")
            await self._apply_stale_failsafe(now)
            return

        if not samples:
            await self._apply_stale_failsafe(now)
            return

        sample = samples[0]
        age = (now - sample.timestamp).total_seconds()

        if not 0 <= age <= self.config.control.live_max_measurement_age_s:
            await self._apply_stale_failsafe(now)
            return

        self._last_fresh_input = sample.timestamp
        self._stale = False
        await self._control_sample(sample, now)

    def _record_command(  # noqa: PLR0913 - mirrors the CSV row fields.
        self,
        *,
        timestamp: datetime,
        device_id: str,
        requested_power_w: float,
        accepted_power_w: float | None,
        soc_percent: float | None,
        status: str,
        error: str = "",
    ) -> None:
        """Append one command to the optional CSV log without affecting control."""
        if self.command_log is None:
            return

        self.command_log.record(
            timestamp=timestamp,
            device_id=device_id,
            requested_power_w=requested_power_w,
            accepted_power_w=accepted_power_w,
            soc_percent=soc_percent,
            status=status,
            error=error,
        )

    async def _control_sample(self, sample: BemsPowerSample, now: datetime) -> None:
        """Serialize a control cycle against runtime configuration changes."""
        async with self._configuration_lock:
            await self._control_sample_locked(sample, now)

    async def _control_sample_locked(self, sample: BemsPowerSample, now: datetime) -> None:
        """Calculate and dispatch a target per live device, isolating adapter failures."""
        timestamp = sample.timestamp
        outputs: list[Measurement] = []
        if self.night_baseline is not None:
            await self.night_baseline.refresh_completed_night(now)
        baseline_power_w = self.config.night.baseline_household_w
        if self.night_baseline is not None:
            measured_baseline_w = await self.night_baseline.power_for(timestamp)
            if measured_baseline_w is not None:
                baseline_power_w = measured_baseline_w
        for device_id, battery in self.batteries.items():
            device = self.devices[device_id]
            requested_power_w = self._last_requested_power.get(device_id, 0.0)
            profile = self._profile_for_device(device)
            household_reserve_wh, reserve_measurements = self._night_reserve_measurements(
                device=device,
                timestamp=timestamp,
                profile=profile,
                baseline_power_w=baseline_power_w,
            )
            outputs.extend(reserve_measurements)
            command_attempted = False
            command_sent = False
            device_state_read = False
            try:
                identity = await battery.get_identity()
                before = await battery.get_state()
                device_state_read = True
                compatible, reason = self._firmware_compatibility(device, identity)
                if not compatible:
                    logger.error(
                        "BEMS live control blocked for %s: %s (model=%s firmware=%s)",
                        device_id,
                        reason,
                        identity.model,
                        identity.firmware_version,
                    )
                    outputs.extend(
                        self._state_measurements(
                            device,
                            timestamp,
                            0.0,
                            before,
                            control_ok=0,
                            stale=0,
                        )
                    )
                    continue
                requested_power_w = self._calculate_device_setpoint(
                    device=device,
                    sample=sample,
                    state=before,
                    profile=profile,
                    reserve_wh=household_reserve_wh,
                )
                refresh_after = getattr(battery, "command_refresh_interval_s", None)
                last_command_at = self._last_command_at.get(device_id)
                refresh_due = (
                    refresh_after is not None and last_command_at is not None and (timestamp - last_command_at).total_seconds() >= refresh_after
                )
                if self._last_requested_power.get(device_id) != requested_power_w or refresh_due:
                    command_attempted = True
                    await battery.set_power(BatteryPowerCommand(timestamp=timestamp, power_w=requested_power_w))
                    command_sent = True
                    self._last_requested_power[device_id] = requested_power_w
                    self._last_command_at[device_id] = timestamp
                state = await battery.get_state()
                if command_sent:
                    self._record_command(
                        timestamp=timestamp,
                        device_id=device_id,
                        requested_power_w=requested_power_w,
                        accepted_power_w=state.charge_power_w - state.discharge_power_w,
                        soc_percent=state.soc_percent,
                        status="accepted",
                    )
                    outputs.extend(
                        self._command_measurements(
                            device,
                            timestamp,
                            requested_power_w,
                            state.charge_power_w - state.discharge_power_w,
                            status=1,
                        )
                    )
                outputs.extend(
                    self._state_measurements(
                        device,
                        timestamp,
                        requested_power_w,
                        state,
                        control_ok=1,
                        stale=0,
                    )
                )
            except Exception as exc:
                if command_attempted:
                    self._record_command(
                        timestamp=timestamp,
                        device_id=device_id,
                        requested_power_w=requested_power_w,
                        accepted_power_w=None,
                        soc_percent=None,
                        status="sent" if command_sent else "failed",
                        error=str(exc),
                    )
                    outputs.extend(
                        self._command_measurements(
                            device,
                            timestamp,
                            requested_power_w,
                            None,
                            status=0 if command_sent else -1,
                        )
                    )
                logger.exception("BEMS adapter operation failed for device %s", device_id)
                outputs.extend(
                    self._error_measurements(
                        device,
                        now,
                        requested_power_w,
                        stale=0,
                        device_alive=device_state_read,
                    )
                )
        await self._store(outputs)

    def _calculate_device_setpoint(
        self,
        *,
        device: BemsDeviceConfig,
        sample: BemsPowerSample,
        state: BatteryState,
        profile: BatteryProfile,
        reserve_wh: float,
    ) -> float:
        """Calculate a target using baseline mock meters or battery-adjusted live meters."""
        # Mock meters stay at battery-free household load. Real meter values
        # already include battery delivery and must be compensated to it.
        mock_meter = device.attributes.get("mock") is True
        grid_power_w = sample.grid_w if mock_meter else estimate_power_without_battery(sample.grid_w, state)
        household_power_w = sample.household_w if mock_meter else estimate_power_without_battery(sample.household_w, state)
        return self.controller.calculate_setpoint(
            grid_power_w=grid_power_w,
            state=state,
            profile=profile,
            device_max_output_power_w=device.max_output_power_w,
            global_max_output_power_w=self.config.control.max_output_power_w,
            household_power_w=household_power_w,
            heat_pump_power_w=sample.heat_pump_w,
            heat_pump_reserve_wh=reserve_wh,
            control_interval_s=self.control_interval_s,
        )

    def _night_reserve_measurements(
        self,
        *,
        device: BemsDeviceConfig,
        timestamp: datetime,
        profile: BatteryProfile,
        baseline_power_w: float,
    ) -> tuple[float, list[Measurement]]:
        """Calculate and report the reserve used to protect household night energy from heat-pump discharge."""
        reserve_wh = night_reserve_wh(
            timestamp=timestamp,
            profile=profile,
            baseline_household_w=baseline_power_w,
            latitude=self.config.night.latitude,
            longitude=self.config.night.longitude,
            timezone=self.config.night.timezone,
            dusk_margin_minutes=self.config.night.dusk_margin_minutes,
            dawn_margin_minutes=self.config.night.dawn_margin_minutes,
        )
        source = f"bems_live_{device.id}"
        measurements = [
            Measurement(timestamp, source, "night_baseline_power", baseline_power_w / 1000.0, "kW"),
            Measurement(timestamp, source, "battery_night_reserve_kwh", reserve_wh / 1000.0, "kWh"),
        ]
        return reserve_wh, measurements

    async def update_config(self, config: BemsConfig) -> None:
        """Apply safe live settings and battery power limits without rebuilding adapters.

        Device identity/connection settings, live device membership, battery
        profile selection and capacity are fixed for a process lifetime. Those
        changes need an explicit container restart; numeric control and power
        limits can be applied while running.
        """
        async with self._configuration_lock:
            next_devices = {device.id: device for device in config.devices if device.enabled and device.mode == "live"}
            self._validate_runtime_devices(config, next_devices)

            previous_grid = (self.config.inputs.grid.source, self.config.inputs.grid.metric)
            next_grid = (config.inputs.grid.source, config.inputs.grid.metric)
            self.config = config
            self.devices = next_devices
            self.controller.deadband_w = config.control.deadband_w
            if previous_grid != next_grid:
                self._grid_cursor = None
                self._last_fresh_input = None

            for device_id, device in self.devices.items():
                await self._apply_device_power_limits(device_id, device, self._profile_for_device(device))

            self._update_night_baseline_config(config)
            if not config.enabled:
                await self._apply_stale_failsafe_locked(datetime.now(UTC))
            logger.info("BEMS live configuration reloaded")

    def _validate_runtime_devices(self, config: BemsConfig, next_devices: dict[str, BemsDeviceConfig]) -> None:
        """Reject hardware or device topology changes that require adapter recreation."""
        if next_devices.keys() != self.devices.keys():
            raise ValueError("Changing enabled live devices requires restarting the BEMS container")
        for device_id, device in next_devices.items():
            current = self.devices[device_id]
            old_profile = self.config.profiles[current.profile]
            new_profile = config.profiles[device.profile]
            if device.profile != current.profile or device.attributes != current.attributes:
                raise ValueError(f"Changing profile or hardware identity for {device_id} requires restarting BEMS")
            if device.initial_soc_percent != current.initial_soc_percent or old_profile.capacity_wh != new_profile.capacity_wh:
                raise ValueError(f"Changing capacity or initial SOC for {device_id} requires restarting BEMS")

    async def _apply_device_power_limits(self, device_id: str, device: BemsDeviceConfig, profile: BatteryProfile) -> None:
        """Apply effective limits to the adapter and immediately clamp an old target."""
        charge_limit = min(profile.max_charge_power_w, device.max_output_power_w, self.config.control.max_output_power_w)
        discharge_limit = min(profile.max_discharge_power_w, device.max_output_power_w, self.config.control.max_output_power_w)
        battery: Any = self.batteries[device_id]
        if hasattr(battery, "max_charge_power_w"):
            battery.max_charge_power_w = charge_limit
        if hasattr(battery, "max_discharge_power_w"):
            battery.max_discharge_power_w = discharge_limit
        if hasattr(battery, "profile"):
            battery.profile = profile
            if hasattr(battery, "power_w"):
                power = battery.power_w
                battery.power_w = max(-discharge_limit, min(charge_limit, power))
        previous_target = self._last_requested_power.get(device_id, 0.0)
        bounded_target = max(-discharge_limit, min(charge_limit, previous_target))
        if bounded_target == previous_target:
            return
        timestamp = datetime.now(UTC)
        try:
            await battery.set_power(BatteryPowerCommand(timestamp, bounded_target))
        except Exception:
            logger.exception("Could not apply reduced BEMS power limit for %s; requesting zero", device_id)
            try:
                await battery.set_power(BatteryPowerCommand(datetime.now(UTC), 0.0))
                bounded_target = 0.0
            except Exception:
                logger.exception("BEMS zero-power command failed after configuration update for %s", device_id)
                return
        self._last_requested_power[device_id] = bounded_target
        self._last_command_at[device_id] = timestamp

    def _update_night_baseline_config(self, config: BemsConfig) -> None:
        """Refresh baseline settings and discard values cached under old settings."""
        if self.night_baseline is None:
            return
        self.night_baseline.config = config
        self.night_baseline.night = config.night
        self.night_baseline.estimation = config.night.baseline_estimation
        self.night_baseline._nights.clear()
        self.night_baseline._baseline_cache.clear()
        self.night_baseline._loaded_through = None
        if self.night_baseline.repository is not None:
            self.night_baseline.repository.source = config.night.baseline_estimation.measurement

    async def _apply_stale_failsafe(self, timestamp: datetime) -> None:
        """Serialize fail-safe writes with control and config reloads."""
        async with self._configuration_lock:
            await self._apply_stale_failsafe_locked(timestamp)

    async def _apply_stale_failsafe_locked(self, timestamp: datetime) -> None:
        """Request zero power when live inputs become stale.

        Zero is the only safe target without fresh grid data: it deactivates
        both charging and feed-in. The command is attempted before the
        identity/firmware checks used for normal control, because a safety stop
        must not depend on reading a version register. The adapter's own
        ``allow_control`` gate still applies.

        The failsafe is idempotent: once every device has accepted 0 W, later
        stale cycles only record state; they do not resend the command.
        """
        if self._stale and all(self._last_requested_power.get(device_id) == 0.0 for device_id in self.batteries):
            return
        self._stale = True
        outputs: list[Measurement] = []
        for device_id, battery in self.batteries.items():
            device = self.devices[device_id]
            zero_applied = self._last_requested_power.get(device_id) == 0.0
            zero_sent = False
            device_state_read = False
            try:
                if not zero_applied:
                    await battery.set_power(BatteryPowerCommand(timestamp=timestamp, power_w=0.0))
                    zero_sent = True
                    self._last_requested_power[device_id] = 0.0
                    self._last_command_at[device_id] = timestamp
                    zero_applied = True
            except Exception as exc:
                self._record_command(
                    timestamp=timestamp,
                    device_id=device_id,
                    requested_power_w=0.0,
                    accepted_power_w=None,
                    soc_percent=None,
                    status="failed",
                    error=str(exc),
                )
                outputs.extend(self._command_measurements(device, timestamp, 0.0, None, status=-1))
                logger.exception("BEMS stale-input zero command failed for device %s", device_id)

            # Diagnostics must not prevent or undo the zero command above.
            try:
                state = await battery.get_state()
                device_state_read = True
                identity = await battery.get_identity()
                compatible, reason = self._firmware_compatibility(device, identity)
                if not compatible:
                    logger.error("BEMS stale-input firmware unverified for %s: %s", device_id, reason)
                if zero_sent:
                    self._record_command(
                        timestamp=timestamp,
                        device_id=device_id,
                        requested_power_w=0.0,
                        accepted_power_w=state.charge_power_w - state.discharge_power_w,
                        soc_percent=state.soc_percent,
                        status="accepted",
                    )
                    outputs.extend(
                        self._command_measurements(
                            device,
                            timestamp,
                            0.0,
                            state.charge_power_w - state.discharge_power_w,
                            status=1,
                        )
                    )
                outputs.extend(
                    self._state_measurements(
                        device,
                        timestamp,
                        0.0,
                        state,
                        control_ok=1 if zero_applied else 0,
                        stale=1,
                    )
                )
            except Exception as exc:
                if zero_sent:
                    self._record_command(
                        timestamp=timestamp,
                        device_id=device_id,
                        requested_power_w=0.0,
                        accepted_power_w=None,
                        soc_percent=None,
                        status="sent",
                        error=str(exc),
                    )
                    outputs.extend(self._command_measurements(device, timestamp, 0.0, None, status=0))
                logger.exception("BEMS stale-input state read failed for device %s", device_id)
                outputs.extend(
                    self._error_measurements(
                        device,
                        timestamp,
                        0.0,
                        stale=1,
                        device_alive=device_state_read,
                    )
                )
        await self._store(outputs)

    def _inputs_are_stale(self, now: datetime) -> bool:
        """Return whether no fresh grid sample has arrived within the age limit."""
        if self._last_fresh_input is None:
            return True
        age = (now - self._last_fresh_input).total_seconds()
        return age < 0 or age > self.config.control.live_max_measurement_age_s

    def _profile_for_device(self, device: BemsDeviceConfig) -> BatteryProfile:
        """Build the domain profile selected by one configured live device."""
        item = self.config.profiles[device.profile]
        return BatteryProfile(
            id=device.profile,
            capacity_wh=item.capacity_wh,
            min_soc_percent=item.min_soc_percent,
            max_soc_percent=item.max_soc_percent,
            max_charge_power_w=item.max_charge_power_w,
            max_discharge_power_w=item.max_discharge_power_w,
            charge_efficiency=item.charge_efficiency,
            discharge_efficiency=item.discharge_efficiency,
        )

    @staticmethod
    def _firmware_compatibility(device: BemsDeviceConfig, identity: BatteryIdentity) -> tuple[bool, str]:
        """Require a configured exact model and firmware allowlist for live writes."""
        expected_model = device.attributes.get("expected_model")
        supported_versions = device.attributes.get("supported_firmware_versions")
        if not isinstance(expected_model, str) or not expected_model:
            return False, "attributes.expected_model is missing"
        if identity.model != expected_model:
            return False, f"reported model does not match expected model {expected_model!r}"
        if not isinstance(supported_versions, list) or not supported_versions:
            return False, "attributes.supported_firmware_versions must be a non-empty exact allowlist"
        if any(not isinstance(version, str) or not version or "*" in version for version in supported_versions):
            return False, "firmware allowlist entries must be non-empty exact strings without wildcards"
        if identity.firmware_version not in supported_versions:
            return False, "reported firmware is not explicitly allowlisted"
        return True, "compatible"

    async def _query(
        self,
        *,
        source: str,
        metric: str,
        start: datetime | None,
        stop: datetime,
        after: datetime | None = None,
    ) -> list[Measurement]:
        """Read one input series with bounded pages and a strict timestamp cursor.

        This helper belongs to database mode; bus mode never queries storage.

        Raises:
            RuntimeError: If no input database was configured.
        """
        if self.input_database is None:
            raise RuntimeError("database mode is not configured")

        rows: list[Measurement] = []
        cursor = after
        while True:
            page = await self.input_database.query_measurements(
                source=source,
                metrics=[metric],
                start=start if cursor is None else None,
                stop=stop,
                after=cursor,
                limit=QUERY_PAGE_SIZE,
            )
            if not page:
                return rows
            rows.extend(page)
            next_cursor = max(row.timestamp for row in page)
            if cursor is not None and next_cursor <= cursor:
                raise RuntimeError("Database query did not advance its timestamp cursor")
            cursor = next_cursor
            if len(page) < QUERY_PAGE_SIZE:
                return rows

    async def _store(self, measurements: list[Measurement]) -> None:
        """Write live readings and controller outcomes to each enabled database.

        A failing output database must not stop battery control, so each write
        is isolated and logged instead of aborting the control cycle.

        Args:
            measurements: Battery state and outcome measurements to persist.
        """
        if not measurements:
            return

        for database in self.output_databases:
            if not database.enabled:
                continue

            try:
                await database.store(measurements)
            except Exception:
                logger.exception(
                    "BEMS live output database %s failed",
                    database.__class__.__name__,
                )

    @staticmethod
    def _state_measurements(  # noqa: PLR0913 - this maps all state and outcome fields into one measurement batch.
        device: BemsDeviceConfig,
        timestamp: datetime,
        requested_power_w: float,
        state: BatteryState,
        *,
        control_ok: int,
        stale: int,
    ) -> list[Measurement]:
        """Represent the requested target, observed state, and service status."""
        source = f"bems_live_{device.id}"
        actual_power = state.charge_power_w - state.discharge_power_w
        # Adapters report state in UTC; align the displayed offset with the
        # sample timestamp so one batch never mixes timezone representations.
        state_timestamp = state.timestamp.astimezone(timestamp.tzinfo)
        measurements = [
            Measurement(timestamp, source, "battery_requested_power", requested_power_w / 1000.0, "kW"),
            Measurement(state_timestamp, source, "battery_actual_power", actual_power / 1000.0, "kW"),
            Measurement(state_timestamp, source, "battery_soc", state.soc_percent, "%"),
            Measurement(state_timestamp, source, "battery_energy_stored", state.energy_wh / 1000.0, "kWh"),
        ]
        if not control_ok:
            measurements.append(Measurement(timestamp, source, "battery_control_ok", 0.0, "bool"))
        if stale:
            measurements.append(Measurement(timestamp, source, "battery_stale_input", 1.0, "bool"))
        return measurements

    @staticmethod
    def _command_measurements(
        device: BemsDeviceConfig,
        timestamp: datetime,
        requested_power_w: float,
        delivered_power_w: float | None,
        *,
        status: int,
    ) -> list[Measurement]:
        """Represent one attempted command and its measured outcome."""
        source = f"bems_live_{device.id}"
        measurements = [
            Measurement(timestamp, source, "battery_command_requested_power", requested_power_w / 1000.0, "kW"),
            Measurement(timestamp, source, "battery_command_status", float(status), "code"),
        ]
        if delivered_power_w is not None:
            measurements.append(Measurement(timestamp, source, "battery_command_reported_power", delivered_power_w / 1000.0, "kW"))
        return measurements

    @staticmethod
    def _error_measurements(
        device: BemsDeviceConfig,
        timestamp: datetime,
        requested_power_w: float,
        *,
        stale: int,
        device_alive: bool,
    ) -> list[Measurement]:
        """Record a failed adapter cycle without inventing an actual power value."""
        source = f"bems_live_{device.id}"
        measurements = [
            Measurement(timestamp, source, "battery_requested_power", requested_power_w / 1000.0, "kW"),
            Measurement(timestamp, source, "battery_control_ok", 0.0, "bool"),
        ]
        if not device_alive:
            measurements.append(Measurement(timestamp, source, "battery_device_alive", 0.0, "bool"))
        if stale:
            measurements.append(Measurement(timestamp, source, "battery_stale_input", 1.0, "bool"))
        return measurements

    @staticmethod
    def _require_aware(timestamp: datetime) -> None:
        """Reject naive timestamps before comparing database and wall-clock time."""
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("BEMS live timestamps must be timezone-aware")
