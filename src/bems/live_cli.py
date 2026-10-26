"""Standalone command-line runner for BEMS live control and adapter dry runs.

This entry point always feeds the controller from the database. The
in-process measurement bus is only available inside the HEMS app; ``live_cli``
stays for ``--mock`` dry runs and one-shot checks.
"""

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, UTC

from src.bems.battery_adapter.factory import AdapterClient, create_adapters
from src.bems.services.live import LiveControlService
from src.config.config import config_obj
from src.databases.database_factory import create_databases
from src.logger.logging_config import setup_logging


async def _run(*, dry_run: bool, once: bool) -> None:
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
    try:
        enabled = [database for database in databases if database.enabled]
        input_database = next((db for db in enabled if db.type == config.history_database), None)
        if input_database is None:
            raise RuntimeError(f"Configured BEMS database '{config.history_database}' is not enabled")
        for database in enabled:
            database.connect()
        batteries, clients = create_adapters(devices, config, dry_run=dry_run)
        service_config = config
        if dry_run:
            service_config = replace(
                config,
                devices=[
                    replace(
                        device,
                        attributes={"expected_model": "BEMS-MOCK", "supported_firmware_versions": ["mock"]},
                    )
                    if device.id in batteries
                    else device
                    for device in config.devices
                ],
            )
        service = LiveControlService(
            config=service_config,
            input_database=input_database,
            output_databases=enabled,
            batteries=batteries,
            poll_interval_s=config.control.live_poll_interval_s,
        )
        if once:
            await service.run_once(now=datetime.now(UTC))
        else:
            await service.run_forever()
    finally:
        for client in clients:
            await client.close()
        for database in databases:
            database.close()


def main() -> None:
    """Start the independent live service; real control is gated to future adapters."""
    parser = argparse.ArgumentParser(description="Run the separate BEMS live-control process")
    parser.add_argument("--mock", action="store_true", help="use in-memory batteries; never sends vendor commands")
    parser.add_argument("--once", action="store_true", help="run one polling cycle and exit (with --mock)")
    args = parser.parse_args()
    setup_logging()
    asyncio.run(_run(dry_run=args.mock, once=args.once))


if __name__ == "__main__":  # pragma: no cover
    main()
