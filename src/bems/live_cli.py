"""Standalone command-line runner for BEMS live control and adapter dry runs.

This entry point always feeds the controller from the database. The
in-process measurement bus is only available inside the HEMS app; ``live_cli``
stays for ``--mock`` dry runs and one-shot checks.
"""

import argparse
import asyncio
import csv
import logging
import signal
from datetime import datetime, UTC
from pathlib import Path

from src.bems.battery_adapter.factory import AdapterClient, create_adapters, with_mock_identities
from src.bems.battery_adapter.mock import MockBatteryAdapter
from src.bems.core.domain import Battery, BatteryPowerCommand
from src.bems.services.live import LiveControlService
from src.bems.services.night_baseline_service import NightBaselineProvider
from src.config.config import CONFIG_FILE, config_obj, load_config
from src.databases.database_factory import create_databases
from src.logger.logging_config import setup_logging
from src.shared.measurement_bus import MeasurementBus
from src.shared.mqtt import MqttMeasurementSubscriber

logger = logging.getLogger(__name__)


def _write_mock_commands(path: Path, batteries: dict[str, Battery]) -> None:
    """Write accepted mock setpoints to a CSV for offline plotting.

    The file has one row per command with the requested and accepted power and
    the mock SOC at command time. It is intentionally independent of the
    databases so a dry run can be inspected without querying InfluxDB.

    Args:
        path: Destination CSV path. Parent directories are created.
        batteries: Device ID to adapter mapping used by the live service.
    """
    mock_adapters = [(device_id, battery) for device_id, battery in batteries.items() if isinstance(battery, MockBatteryAdapter)]

    if not mock_adapters:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0

    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "timestamp",
                "device_id",
                "requested_power_w",
                "accepted_power_w",
                "soc_percent",
            ]
        )

        for device_id, adapter in mock_adapters:
            for command in adapter.commands:
                writer.writerow(
                    [
                        command.timestamp.isoformat(),
                        device_id,
                        command.requested_power_w,
                        command.accepted_power_w,
                        command.soc_percent,
                    ]
                )
                row_count += 1

    logger.info("Wrote %d mock battery commands to %s", row_count, path)


def _connect_enabled_databases(databases) -> list:
    """Connect available output/history databases without blocking live MQTT control."""
    connected = []
    for database in databases:
        if not database.enabled:
            continue
        try:
            database.connect()
            connected.append(database)
        except Exception:
            logger.exception("BEMS database %s unavailable; live MQTT control continues", database.type)
    return connected


def _install_stop_handlers(stop_event: asyncio.Event) -> None:
    """Request a graceful bus shutdown on SIGTERM/SIGINT."""
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:

            def _handle_signal(_signum: int, _frame: object) -> None:
                loop.call_soon_threadsafe(stop_event.set)

            signal.signal(signum, _handle_signal)


async def _run_bus(service: LiveControlService, subscriber: MqttMeasurementSubscriber, *, dry_run: bool) -> None:
    """Run bus control until a shutdown signal or cancellation requests stop."""
    stop_event = asyncio.Event()
    _install_stop_handlers(stop_event)
    watcher = asyncio.create_task(_watch_config(service, subscriber, dry_run=dry_run))
    try:
        await service.run_forever_bus(stop_event=stop_event)
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


async def _run(*, dry_run: bool, once: bool, mock_csv: str) -> None:
    """Run mock batteries or explicitly configured vendor live adapters."""
    config = config_obj.bems
    if config is None or not config.enabled:
        raise RuntimeError("BEMS is not enabled in config.yaml")
    devices = [device for device in config.devices if device.enabled and device.mode == "live"]
    if not devices:
        raise RuntimeError("No enabled BEMS devices with mode: live are configured")
    databases = create_databases(config_obj)
    clients: list[AdapterClient] = []
    batteries: dict[str, Battery] = {}
    subscriber: MqttMeasurementSubscriber | None = None
    try:
        enabled = [database for database in databases if database.enabled]
        connected_databases = _connect_enabled_databases(enabled)
        input_database = next((db for db in connected_databases if db.type == config.history_database), None)
        if once and input_database is None:
            raise RuntimeError(f"Configured BEMS database '{config.history_database}' is not enabled")
        batteries, clients = create_adapters(devices, config, dry_run=dry_run)
        mock_device_ids = set(batteries) if dry_run else {device.id for device in devices if device.attributes.get("mock") is True}
        service_config = with_mock_identities(config, device_ids=mock_device_ids)
        night_baseline = NightBaselineProvider(config, input_database) if config.night.baseline_estimation.enabled and input_database else None
        bus = MeasurementBus()
        command_log_csv = config.control.command_log_csv if config.control.command_log_enabled else None
        service = LiveControlService(
            config=service_config,
            input_database=input_database if once else None,
            bus=None if once else bus,
            output_databases=connected_databases,
            batteries=batteries,
            poll_interval_s=config.control.bems_db_poll_interval_s,
            command_log_csv=command_log_csv,
            night_baseline=night_baseline,
        )
        await service.initialize()
        if once:
            await service.run_once(now=datetime.now(UTC))
        else:
            subscriber = MqttMeasurementSubscriber(
                bus,
                sources=[(config.inputs.grid.source, config.inputs.grid.metric), (config.inputs.household.source, config.inputs.household.metric)],
            )
            await subscriber.start()
            await _run_bus(service, subscriber, dry_run=dry_run)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("BEMS shutdown requested")
    finally:
        await _shutdown_failsafe(batteries, dry_run=dry_run, once=once)
        if subscriber is not None:
            await subscriber.close()
        if dry_run:
            _write_mock_commands(Path(mock_csv), batteries)
        for client in clients:
            await client.close()
        for database in databases:
            database.close()


SHUTDOWN_FAILSAFE_TIMEOUT_S = 5.0


async def _shutdown_failsafe(batteries: dict[str, Battery], *, dry_run: bool, once: bool) -> None:
    """Best-effort zero command on process shutdown.

    Docker stop sends SIGTERM and later SIGKILL. Without this, the last
    requested battery setpoint can remain active after BEMS exits. The
    ``--once`` mode is a diagnostic single cycle and should not change device
    state on exit; dry-run mode never sends vendor commands.
    """
    if dry_run or once or not batteries:
        return

    async def _send_zero(device_id: str, battery: Battery) -> None:
        try:
            await asyncio.wait_for(
                battery.set_power(BatteryPowerCommand(timestamp=datetime.now(UTC), power_w=0.0)),
                timeout=SHUTDOWN_FAILSAFE_TIMEOUT_S,
            )
            logger.info("BEMS shutdown failsafe set %s to 0 W", device_id)
        except Exception:
            logger.exception("BEMS shutdown failsafe failed for %s", device_id)

    await asyncio.gather(*(_send_zero(device_id, battery) for device_id, battery in batteries.items()))


async def _watch_config(service: LiveControlService, subscriber: MqttMeasurementSubscriber, *, dry_run: bool) -> None:
    """Reload BEMS control settings from the shared root config file."""
    try:
        observed_mtime = CONFIG_FILE.stat().st_mtime_ns
    except OSError:
        logger.exception("Cannot watch configuration file %s", CONFIG_FILE)
        return

    while True:
        await asyncio.sleep(0.5)
        try:
            current_mtime = CONFIG_FILE.stat().st_mtime_ns
        except OSError:
            logger.exception("Cannot stat configuration file %s", CONFIG_FILE)
            continue
        if current_mtime == observed_mtime:
            continue
        try:
            full_config = load_config()
            bems = full_config.bems
            if bems is None:
                raise ValueError("The shared config.yaml has no BEMS section")
        except Exception:
            logger.exception("BEMS could not parse updated configuration; will retry")
            continue
        observed_mtime = current_mtime
        try:
            mock_ids = {device.id for device in bems.devices if device.attributes.get("mock") is True}
            if dry_run:
                mock_ids.update(device.id for device in bems.devices if device.enabled and device.mode == "live")
            service_config = with_mock_identities(bems, device_ids=mock_ids)
            await service.update_config(service_config)
            subscriber.update_sources(
                [
                    (bems.inputs.grid.source, bems.inputs.grid.metric),
                    (bems.inputs.household.source, bems.inputs.household.metric),
                ]
            )
        except Exception:
            logger.exception("BEMS rejected updated configuration; keeping the last valid settings")


def main() -> None:
    """Start the independent live service; real control is gated to future adapters."""
    parser = argparse.ArgumentParser(description="Run the separate BEMS live-control process")
    parser.add_argument("--mock", action="store_true", help="use in-memory batteries; never sends vendor commands")
    parser.add_argument("--once", action="store_true", help="run one polling cycle and exit (with --mock)")
    parser.add_argument(
        "--mock-csv",
        default="data/bems_mock_commands.csv",
        help="CSV file for commands accepted by --mock batteries (default: %(default)s)",
    )
    args = parser.parse_args()
    setup_logging()
    asyncio.run(_run(dry_run=args.mock, once=args.once, mock_csv=args.mock_csv))


if __name__ == "__main__":  # pragma: no cover
    main()
