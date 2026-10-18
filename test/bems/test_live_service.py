import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, UTC

from src.bems.domain import BatteryPowerCommand, BatteryState
from src.bems.live import LiveControlService
from src.collectors.definitions.common.measurement import Measurement
from src.config.config import config_obj
from src.databases.base_database import BaseDatabase

import pytest


class MemoryDatabase(BaseDatabase):
    """In-memory query/store backend for live-service tests."""

    def __init__(self, measurements: list[Measurement] | None = None) -> None:
        super().__init__(type="memory", enabled=True)
        self.measurements = measurements or []
        self.writes: list[Measurement] = []

    async def store(self, measurements: list[Measurement]) -> None:
        self.writes.extend(measurements)

    async def query_measurements(  # noqa: PLR0913 - implements the shared database query contract.
        self,
        *,
        source: str,
        metrics: list[str],
        start: datetime | None = None,
        stop: datetime | None = None,
        after: datetime | None = None,
        limit: int = 1000,
    ) -> list[Measurement]:
        return sorted(
            [
                point
                for point in self.measurements
                if point.source == source
                and point.metric in metrics
                and (start is None or point.timestamp >= start)
                and (stop is None or point.timestamp < stop)
                and (after is None or point.timestamp > after)
            ],
            key=lambda point: point.timestamp,
        )[:limit]


class RecordingBattery:
    """Record targets and expose them as immediately observed mock power."""

    def __init__(self, *, fail_reads: bool = False) -> None:
        self.targets: list[BatteryPowerCommand] = []
        self.timestamp = datetime(2026, 10, 18, tzinfo=UTC)
        self.power_w = 0.0
        self.fail_reads = fail_reads
        self.fail_sets = False
        self.command_refresh_interval_s: float | None = None

    async def get_state(self) -> BatteryState:
        if self.fail_reads:
            raise ConnectionError("mock read failed")
        return BatteryState(self.timestamp, 50, 3500, max(0, self.power_w), max(0, -self.power_w))

    async def set_power(self, command: BatteryPowerCommand) -> None:
        if self.fail_sets:
            raise ConnectionError("mock write failed")
        self.targets.append(command)
        self.timestamp = command.timestamp
        self.power_w = command.power_w


def live_config():
    """Use the repository's default BEMS settings with one enabled live device."""
    assert config_obj.bems is not None
    device = replace(config_obj.bems.devices[0], id="test_live", mode="live")
    return replace(config_obj.bems, devices=[device])


def input_measurements(timestamp: datetime) -> list[Measurement]:
    """Create a fresh cascade sample using the configured source names."""
    bems = config_obj.bems
    assert bems is not None
    return [
        Measurement(timestamp, bems.inputs.grid.source, bems.inputs.grid.metric, -500, "W"),
        Measurement(timestamp, bems.inputs.household.source, bems.inputs.household.metric, -300, "W"),
        Measurement(timestamp, bems.inputs.pv.source, bems.inputs.pv.metric, 200, "W"),
    ]


def test_live_service_processes_new_meter_rows_once_and_persists_observed_state():
    timestamp = datetime(2026, 10, 18, 12, tzinfo=UTC)
    now = timestamp + timedelta(seconds=2)
    input_database = MemoryDatabase(input_measurements(timestamp))
    output_database = MemoryDatabase()
    battery = RecordingBattery()
    service = LiveControlService(
        config=live_config(),
        input_database=input_database,
        output_databases=[output_database],
        batteries={"test_live": battery},
    )

    assert asyncio.run(service.run_once(now=now)) == 1
    assert asyncio.run(service.run_once(now=now)) == 0
    assert len(battery.targets) == 1
    assert battery.targets[0].power_w == -500
    metrics = {point.metric for point in output_database.writes}
    assert {"requested_power", "actual_power", "soc", "energy_stored", "control_ok"} <= metrics


def test_live_service_requests_zero_when_inputs_become_stale():
    timestamp = datetime(2026, 10, 18, 12, tzinfo=UTC)
    input_database = MemoryDatabase(input_measurements(timestamp))
    output_database = MemoryDatabase()
    battery = RecordingBattery()
    service = LiveControlService(
        config=live_config(),
        input_database=input_database,
        output_databases=[output_database],
        batteries={"test_live": battery},
    )

    asyncio.run(service.run_once(now=timestamp + timedelta(seconds=1)))
    asyncio.run(service.run_once(now=timestamp + timedelta(seconds=25)))

    assert [command.power_w for command in battery.targets] == [-500, 0]
    assert any(point.metric == "stale_input" and point.value == 1 for point in output_database.writes)


def test_adapter_failure_is_recorded_and_does_not_raise_from_poll_cycle():
    timestamp = datetime(2026, 10, 18, 12, tzinfo=UTC)
    output_database = MemoryDatabase()
    service = LiveControlService(
        config=live_config(),
        input_database=MemoryDatabase(input_measurements(timestamp)),
        output_databases=[output_database],
        batteries={"test_live": RecordingBattery(fail_reads=True)},
    )

    assert asyncio.run(service.run_once(now=timestamp + timedelta(seconds=1))) == 1
    assert any(point.metric == "control_ok" and point.value == 0 for point in output_database.writes)


def test_invalid_meter_unit_triggers_zero_power_failsafe():
    timestamp = datetime(2026, 10, 18, 12, tzinfo=UTC)
    data = input_measurements(timestamp)
    data[0] = replace(data[0], unit="MW")
    battery = RecordingBattery()
    service = LiveControlService(
        config=live_config(),
        input_database=MemoryDatabase(data),
        output_databases=[MemoryDatabase()],
        batteries={"test_live": battery},
    )

    assert asyncio.run(service.run_once(now=timestamp + timedelta(seconds=1))) == 0
    assert battery.targets and battery.targets[-1].power_w == 0


def test_stale_failsafe_retries_if_first_zero_command_fails():
    battery = RecordingBattery()
    battery.fail_sets = True
    service = LiveControlService(
        config=live_config(),
        input_database=MemoryDatabase(),
        output_databases=[MemoryDatabase()],
        batteries={"test_live": battery},
    )
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)

    asyncio.run(service.run_once(now=now))
    battery.fail_sets = False
    asyncio.run(service.run_once(now=now + timedelta(seconds=5)))

    assert [command.power_w for command in battery.targets] == [0]


def test_live_service_requires_adapters_only_for_enabled_live_devices():
    with pytest.raises(ValueError, match="Missing battery adapters"):
        LiveControlService(
            config=live_config(),
            input_database=MemoryDatabase(),
            output_databases=[],
            batteries={},
        )


def test_live_service_refreshes_time_limited_vendor_setpoints():
    """A vendor adapter can renew an unchanged command before it expires."""
    timestamp = datetime(2026, 10, 18, 12, tzinfo=UTC)
    input_database = MemoryDatabase(input_measurements(timestamp))
    battery = RecordingBattery()
    battery.command_refresh_interval_s = 20
    service = LiveControlService(
        config=live_config(),
        input_database=input_database,
        output_databases=[],
        batteries={"test_live": battery},
    )

    assert asyncio.run(service.run_once(now=timestamp + timedelta(seconds=2))) == 1
    assert len(battery.targets) == 1

    next_timestamp = timestamp + timedelta(seconds=22)
    input_database.measurements.extend(input_measurements(next_timestamp))
    assert asyncio.run(service.run_once(now=next_timestamp + timedelta(seconds=1))) == 1
    assert len(battery.targets) == 2
    assert battery.targets[0].power_w == battery.targets[1].power_w
