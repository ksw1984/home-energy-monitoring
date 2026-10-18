"""Standalone polling and control loop for explicitly enabled live batteries."""

import asyncio
import logging
from datetime import datetime, timedelta, UTC

from src.bems.controller import GridBalancingController
from src.bems.domain import align_power_samples, Battery, BatteryPowerCommand, BatteryProfile, BatteryState
from src.collectors.definitions.common.measurement import Measurement
from src.config.config import BemsConfig, BemsDeviceConfig
from src.databases.base_database import BaseDatabase

logger = logging.getLogger(__name__)
QUERY_PAGE_SIZE = 1000


class LiveControlService:
    """Consume new meter rows and control injected battery adapters independently of HEMS."""

    def __init__(
        self,
        *,
        config: BemsConfig,
        input_database: BaseDatabase,
        output_databases: list[BaseDatabase],
        batteries: dict[str, Battery],
        poll_interval_s: float = 5.0,
    ) -> None:
        """Create a live service; ``batteries`` maps enabled live device IDs to adapters."""
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")
        self.config = config
        self.input_database = input_database
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
        self._grid_cursor: datetime | None = None
        self._last_fresh_input: datetime | None = None
        self._stale = False
        self._last_requested_power: dict[str, float] = {}
        self._last_command_at: dict[str, datetime] = {}

    async def run_once(self, *, now: datetime | None = None) -> int:
        """Process each newly stored grid point once and return the count handled."""
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
            await self._control_sample(current_time, sample.grid_w, current_time)
            handled += 1

        if not samples:
            await self._apply_stale_failsafe(current_time)
        return handled

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        """Poll until cancelled or an optional stop event is set."""
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

    async def _control_sample(self, timestamp: datetime, grid_power_w: float, now: datetime) -> None:
        """Calculate and dispatch a target per live device, isolating adapter failures."""
        outputs: list[Measurement] = []
        for device_id, battery in self.batteries.items():
            device = self.devices[device_id]
            requested_power_w = self._last_requested_power.get(device_id, 0.0)
            profile = self._profile_for_device(device)
            try:
                before = await battery.get_state()
                requested_power_w = self.controller.calculate_setpoint(
                    grid_power_w=grid_power_w,
                    state=before,
                    profile=profile,
                    device_max_output_power_w=device.max_output_power_w,
                    global_max_output_power_w=self.config.control.max_output_power_w,
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
                outputs.extend(self._state_measurements(device, timestamp, requested_power_w, state, control_ok=1, stale=0))
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
                if self._last_requested_power.get(device_id) != 0.0:
                    await battery.set_power(BatteryPowerCommand(timestamp=timestamp, power_w=0.0))
                    self._last_requested_power[device_id] = 0.0
                    self._last_command_at[device_id] = timestamp
                state = await battery.get_state()
                outputs.extend(self._state_measurements(device, timestamp, 0.0, state, control_ok=1, stale=1))
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

    async def _query(
        self,
        *,
        source: str,
        metric: str,
        start: datetime | None,
        stop: datetime,
        after: datetime | None = None,
    ) -> list[Measurement]:
        """Read one input series with bounded pages and a strict timestamp cursor."""
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
        """Write live readings and controller outcomes to each enabled database."""
        if measurements:
            for database in self.output_databases:
                if database.enabled:
                    await database.store(measurements)

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
        return [
            Measurement(timestamp, source, "requested_power", requested_power_w, "W"),
            Measurement(state.timestamp, source, "actual_power", actual_power, "W"),
            Measurement(state.timestamp, source, "soc", state.soc_percent, "%"),
            Measurement(state.timestamp, source, "energy_stored", state.energy_wh, "Wh"),
            Measurement(timestamp, source, "battery_available", float(state.available), "bool"),
            Measurement(timestamp, source, "control_ok", float(control_ok), "bool"),
            Measurement(timestamp, source, "stale_input", float(stale), "bool"),
        ]

    @staticmethod
    def _error_measurements(device: BemsDeviceConfig, timestamp: datetime, requested_power_w: float, *, stale: int) -> list[Measurement]:
        """Record a failed adapter cycle without inventing an actual power value."""
        source = f"bems_live_{device.id}"
        return [
            Measurement(timestamp, source, "requested_power", requested_power_w, "W"),
            Measurement(timestamp, source, "battery_available", 0.0, "bool"),
            Measurement(timestamp, source, "control_ok", 0.0, "bool"),
            Measurement(timestamp, source, "stale_input", float(stale), "bool"),
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

    async def set_power(self, command: BatteryPowerCommand) -> None:
        """Accept an instantaneous, profile-limited target for dry-run purposes."""
        if self.fail_operations:
            raise ConnectionError("mock battery unavailable")
        charge_limit = self.profile.max_charge_power_w
        discharge_limit = self.profile.max_discharge_power_w
        self.power_w = min(charge_limit, max(-discharge_limit, command.power_w))
        self.timestamp = command.timestamp
