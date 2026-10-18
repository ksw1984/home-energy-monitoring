"""Standalone command-line runner for BEMS live control and adapter dry runs."""

import argparse
import asyncio
from datetime import datetime, UTC

from src.bems.domain import Battery, BatteryProfile
from src.bems.live import LiveControlService, MockBatteryAdapter
from src.config.config import BemsConfig, config_obj
from src.databases.database_factory import create_databases
from src.logger.logging_config import setup_logging


def _profile(config: BemsConfig, profile_id: str) -> BatteryProfile:
    """Convert a configured profile into the shared live battery model."""
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


async def _run(*, dry_run: bool, once: bool) -> None:
    """Run the injected mock adapters or fail closed until vendor adapters exist."""
    config = config_obj.bems
    if config is None or not config.enabled:
        raise RuntimeError("BEMS is not enabled in config.yaml")
    devices = [device for device in config.devices if device.enabled and device.mode == "live"]
    if not devices:
        raise RuntimeError("No enabled BEMS devices with mode: live are configured")
    if not dry_run:
        raise RuntimeError("Vendor adapters are not available yet; use --mock for a safe service dry run")

    databases = create_databases(config_obj)
    try:
        enabled = [database for database in databases if database.enabled]
        input_database = next((db for db in enabled if db.type == config.history_database), None)
        if input_database is None:
            raise RuntimeError(f"Configured BEMS database '{config.history_database}' is not enabled")
        for database in enabled:
            database.connect()
        batteries: dict[str, Battery] = {
            device.id: MockBatteryAdapter(
                profile=_profile(config, device.profile),
                initial_soc_percent=device.initial_soc_percent,
            )
            for device in devices
        }
        service = LiveControlService(
            config=config,
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
