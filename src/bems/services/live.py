"""Standalone polling and control loop for explicitly enabled live batteries."""

import asyncio
import logging
from datetime import datetime, timedelta, UTC

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
from src.collectors.definitions.common.measurement import Measurement
from src.config.config import BemsConfig, BemsDeviceConfig
from src.databases.base_database import BaseDatabase
from src.manager.measurement_bus import MeasurementBus

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
        # Bus mode replaces this with the observed grid cadence; the database
        # path keeps the configured poll interval.
        self.control_interval_s = config.control.live_poll_interval_s

    async def run_once(self, *, now: datetime | None = None) -> int:
        """Process each newly stored grid point once and return the count handled."""
        if self.input_database is None:
            raise RuntimeError("run_once() requires input_database; use run_forever_bus() for bus mode")
        current_time = now or datetime.now(UTC)
        self._require_aware(current_time)
        grid_ref = self.config.inputs.grid
        start = None if self._grid_cursor is not None else current_time - timedelta(seconds=self.config.control.max_measurement_age_s)
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
            fresh = 0 <= age <= self.config.control.max_measurement_age_s
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

        grid_ref = self.config.inputs.grid
        wait_timeout = max(MIN_BUS_WAIT_S, self.config.control.max_measurement_age_s)

        while stop_event is None or not stop_event.is_set():
            try:
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
        split; when it is missing or too old, ``align_power_samples`` returns
        nothing and the zero-power failsafe is requested, matching the
        database path. PV is optional and only enriches the sample.

        Args:
            now: Timezone-aware wall clock used for freshness checks.
        """
        if self.bus is None:
            raise RuntimeError("bus mode is not configured")

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
                tolerance_seconds=self.config.control.history_alignment_tolerance_s,
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

        if not 0 <= age <= self.config.control.max_measurement_age_s:
            await self._apply_stale_failsafe(now)
            return

        self._last_fresh_input = sample.timestamp
        self._stale = False
        await self._control_sample(sample, now)

    async def _control_sample(self, sample: BemsPowerSample, now: datetime) -> None:
        """Calculate and dispatch a target per live device, isolating adapter failures."""
        timestamp = sample.timestamp
        outputs: list[Measurement] = []
        for device_id, battery in self.batteries.items():
            device = self.devices[device_id]
            requested_power_w = self._last_requested_power.get(device_id, 0.0)
            profile = self._profile_for_device(device)
            try:
                identity = await battery.get_identity()
                before = await battery.get_state()
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
                            identity=identity,
                            firmware_compatible=False,
                        )
                    )
                    continue
                requested_power_w = self.controller.calculate_setpoint(
                    grid_power_w=estimate_power_without_battery(sample.grid_w, before),
                    state=before,
                    profile=profile,
                    device_max_output_power_w=device.max_output_power_w,
                    global_max_output_power_w=self.config.control.max_output_power_w,
                    household_power_w=estimate_power_without_battery(sample.household_w, before),
                    heat_pump_power_w=sample.heat_pump_w,
                    heat_pump_reserve_wh=night_reserve_wh(
                        timestamp=timestamp,
                        profile=profile,
                        baseline_household_w=self.config.night.baseline_household_w,
                        latitude=self.config.night.latitude,
                        longitude=self.config.night.longitude,
                        timezone=self.config.night.timezone,
                        dusk_margin_minutes=self.config.night.dusk_margin_minutes,
                        dawn_margin_minutes=self.config.night.dawn_margin_minutes,
                    ),
                    control_interval_s=self.control_interval_s,
                )
                refresh_after = getattr(battery, "command_refresh_interval_s", None)
                last_command_at = self._last_command_at.get(device_id)
                refresh_due = (
                    refresh_after is not None and last_command_at is not None and (timestamp - last_command_at).total_seconds() >= refresh_after
                )
                if self._last_requested_power.get(device_id) != requested_power_w or refresh_due:
                    await battery.set_power(BatteryPowerCommand(timestamp=timestamp, power_w=requested_power_w))
                    self._last_requested_power[device_id] = requested_power_w
                    self._last_command_at[device_id] = timestamp
                state = await battery.get_state()
                outputs.extend(
                    self._state_measurements(
                        device,
                        timestamp,
                        requested_power_w,
                        state,
                        control_ok=1,
                        stale=0,
                        identity=identity,
                        firmware_compatible=True,
                    )
                )
            except Exception:
                logger.exception("BEMS adapter operation failed for device %s", device_id)
                outputs.extend(self._error_measurements(device, now, requested_power_w, stale=0))
        await self._store(outputs)

    async def _apply_stale_failsafe(self, timestamp: datetime) -> None:
        """Request zero power once when inputs become stale, if adapters respond."""
        if self._stale and all(self._last_requested_power.get(device_id) == 0.0 for device_id in self.batteries):
            return
        self._stale = True
        outputs: list[Measurement] = []
        for device_id, battery in self.batteries.items():
            device = self.devices[device_id]
            try:
                identity = await battery.get_identity()
                compatible, reason = self._firmware_compatibility(device, identity)
                if not compatible:
                    logger.error("BEMS stale-input failsafe blocked for %s: %s", device_id, reason)
                    state = await battery.get_state()
                    outputs.extend(
                        self._state_measurements(
                            device,
                            timestamp,
                            0.0,
                            state,
                            control_ok=0,
                            stale=1,
                            identity=identity,
                            firmware_compatible=False,
                        )
                    )
                    continue
                if self._last_requested_power.get(device_id) != 0.0:
                    await battery.set_power(BatteryPowerCommand(timestamp=timestamp, power_w=0.0))
                    self._last_requested_power[device_id] = 0.0
                    self._last_command_at[device_id] = timestamp
                state = await battery.get_state()
                outputs.extend(
                    self._state_measurements(
                        device,
                        timestamp,
                        0.0,
                        state,
                        control_ok=1,
                        stale=1,
                        identity=identity,
                        firmware_compatible=True,
                    )
                )
            except Exception:
                logger.exception("BEMS stale-input failsafe failed for device %s", device_id)
                outputs.extend(self._error_measurements(device, timestamp, 0.0, stale=1))
        await self._store(outputs)

    def _inputs_are_stale(self, now: datetime) -> bool:
        """Return whether no fresh grid sample has arrived within the age limit."""
        if self._last_fresh_input is None:
            return True
        age = (now - self._last_fresh_input).total_seconds()
        return age < 0 or age > self.config.control.max_measurement_age_s

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
        identity: BatteryIdentity,
        firmware_compatible: bool,
    ) -> list[Measurement]:
        """Represent the requested target, observed state, and service status."""
        source = f"bems_live_{device.id}"
        actual_power = state.charge_power_w - state.discharge_power_w
        measurements = [
            Measurement(timestamp, source, "requested_power", requested_power_w, "W"),
            Measurement(state.timestamp, source, "actual_power", actual_power, "W"),
            Measurement(state.timestamp, source, "soc", state.soc_percent, "%"),
            Measurement(state.timestamp, source, "energy_stored", state.energy_wh, "Wh"),
            Measurement(timestamp, source, "battery_available", float(state.available), "bool"),
            Measurement(timestamp, source, "control_ok", float(control_ok), "bool"),
            Measurement(timestamp, source, "stale_input", float(stale), "bool"),
            Measurement(timestamp, source, "device_alive", 1.0, "bool"),
            Measurement(timestamp, source, "firmware_compatible", float(firmware_compatible), "bool"),
        ]
        try:
            version_code = float(identity.firmware_version)
        except ValueError:
            version_code = None
        if version_code is not None:
            measurements.append(Measurement(timestamp, source, "firmware_version_code", version_code, "version"))
        return measurements

    @staticmethod
    def _error_measurements(device: BemsDeviceConfig, timestamp: datetime, requested_power_w: float, *, stale: int) -> list[Measurement]:
        """Record a failed adapter cycle without inventing an actual power value."""
        source = f"bems_live_{device.id}"
        return [
            Measurement(timestamp, source, "requested_power", requested_power_w, "W"),
            Measurement(timestamp, source, "battery_available", 0.0, "bool"),
            Measurement(timestamp, source, "control_ok", 0.0, "bool"),
            Measurement(timestamp, source, "stale_input", float(stale), "bool"),
            Measurement(timestamp, source, "device_alive", 0.0, "bool"),
            Measurement(timestamp, source, "firmware_compatible", 0.0, "bool"),
        ]

    @staticmethod
    def _require_aware(timestamp: datetime) -> None:
        """Reject naive timestamps before comparing database and wall-clock time."""
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("BEMS live timestamps must be timezone-aware")


class MockBatteryAdapter:
    """In-memory Battery adapter for a standalone control-service dry run."""

    def __init__(self, *, profile: BatteryProfile, initial_soc_percent: float) -> None:
        """Create a zero-power mock battery at the selected initial SOC."""
        if not profile.min_soc_percent <= initial_soc_percent <= profile.max_soc_percent:
            raise ValueError("initial SOC must be within the profile limits")
        self.profile = profile
        self.energy_wh = profile.capacity_wh * initial_soc_percent / 100.0
        self.power_w = 0.0
        self.timestamp = datetime.now(UTC)
        self.available = True
        self.fail_operations = False

    async def get_state(self) -> BatteryState:
        """Return the mock's current SOC, energy, and signed power."""
        if self.fail_operations:
            raise ConnectionError("mock battery unavailable")
        return BatteryState(
            timestamp=self.timestamp,
            soc_percent=100.0 * self.energy_wh / self.profile.capacity_wh,
            energy_wh=self.energy_wh,
            charge_power_w=max(0.0, self.power_w),
            discharge_power_w=max(0.0, -self.power_w),
            available=self.available,
        )

    async def get_identity(self) -> BatteryIdentity:
        """Expose a synthetic identity for explicitly requested dry-run control."""
        return BatteryIdentity("BEMS-MOCK", "mock")

    async def set_power(self, command: BatteryPowerCommand) -> None:
        """Accept an instantaneous, profile-limited target for dry-run purposes."""
        if self.fail_operations:
            raise ConnectionError("mock battery unavailable")
        charge_limit = self.profile.max_charge_power_w
        discharge_limit = self.profile.max_discharge_power_w
        self.power_w = min(charge_limit, max(-discharge_limit, command.power_w))
        self.timestamp = command.timestamp
