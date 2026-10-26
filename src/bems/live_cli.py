"""Standalone command-line runner for BEMS live control and adapter dry runs.

This entry point always feeds the controller from the database. The
in-process measurement bus is only available inside the HEMS app; ``live_cli``
stays for ``--mock`` dry runs and one-shot checks.
"""

import argparse
import asyncio
import csv
import logging
from datetime import datetime, UTC
from pathlib import Path

from src.bems.battery_adapter.factory import AdapterClient, create_adapters, with_mock_identities
from src.bems.core.domain import Battery
from src.bems.services.live import LiveControlService, MockBatteryAdapter
from src.config.config import config_obj
from src.databases.database_factory import create_databases
from src.logger.logging_config import setup_logging

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


async def _run(*, dry_run: bool, once: bool, mock_csv: str) -> None:
    """Run mock batteries or explicitly configured vendor live adapters."""
    config = config_obj.bems
    if config is None or not config.enabled:
        raise RuntimeError("BEMS is not enabled in config.yaml")
    if not dry_run and config.control.feed == "bus":
        raise RuntimeError("BEMS control.feed is 'bus'; run live control inside the HEMS app or set feed: database")
    devices = [device for device in config.devices if device.enabled and device.mode == "live"]
    if not devices:
        raise RuntimeError("No enabled BEMS devices with mode: live are configured")
    databases = create_databases(config_obj)
    clients: list[AdapterClient] = []
    batteries: dict[str, Battery] = {}
    try:
        enabled = [database for database in databases if database.enabled]
        input_database = next((db for db in enabled if db.type == config.history_database), None)
        if input_database is None:
            raise RuntimeError(f"Configured BEMS database '{config.history_database}' is not enabled")
        for database in enabled:
            database.connect()
        batteries, clients = create_adapters(devices, config, dry_run=dry_run)
        mock_device_ids = set(batteries) if dry_run else {device.id for device in devices if device.attributes.get("mock") is True}
        service_config = with_mock_identities(config, device_ids=mock_device_ids)
        service = LiveControlService(
            config=service_config,
            input_database=input_database,
            output_databases=enabled,
            batteries=batteries,
            poll_interval_s=config.control.live_poll_interval_s,
            command_log_csv=config.control.command_log_csv,
        )
        if once:
            await service.run_once(now=datetime.now(UTC))
        else:
            await service.run_forever()
    finally:
        if dry_run:
            _write_mock_commands(Path(mock_csv), batteries)
        for client in clients:
            await client.close()
        for database in databases:
            database.close()


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
