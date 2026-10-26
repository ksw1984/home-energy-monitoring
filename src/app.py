import asyncio
import logging
import signal

from src.bems.battery_adapter.factory import AdapterClient, create_adapters, with_mock_identities
from src.bems.services.live import LiveControlService
from src.calculators.calculator_factory import create_calculators
from src.collectors.collector_factory import create_collectors
from src.config.config import Config, config_obj
from src.databases.base_database import BaseDatabase
from src.databases.database_factory import create_databases
from src.estimators.estimator_factory import create_estimators
from src.logger.logging_config import setup_logging
from src.manager.measurement_bus import MeasurementBus
from src.manager.measurement_manager import MeasurementManager

logger = logging.getLogger(__name__)

#
#  poetry run python -m app
#


def _create_bems_live_service(
    config: Config,
    databases: list[BaseDatabase],
    bus: MeasurementBus,
) -> tuple[LiveControlService | None, list[AdapterClient]]:
    """Create the app-hosted BEMS live service when it is configured.

    The service runs inside the HEMS process so live control can consume the
    measurement bus at collector cadence. ``feed: database`` keeps the
    standalone ``live_cli`` path and deliberately starts nothing here.

    Args:
        config: Loaded application configuration.
        databases: Databases shared with the measurement manager.
        bus: Measurement stream to consume.

    Returns:
        The service and its vendor clients (to close on shutdown), or
        ``(None, [])`` when BEMS live control is not hosted by the app.
    """
    bems = config.bems

    if bems is None or not bems.enabled or bems.control.feed != "bus":
        return None, []

    devices = [device for device in bems.devices if device.enabled and device.mode == "live"]

    if not devices:
        return None, []

    mock_device_ids = {device.id for device in devices if device.attributes.get("mock") is True}

    if mock_device_ids:
        logger.info(
            "BEMS live control uses mock adapters for: %s",
            ", ".join(sorted(mock_device_ids)),
        )

    batteries, clients = create_adapters(devices, bems)
    service_config = with_mock_identities(bems, device_ids=mock_device_ids)
    service = LiveControlService(
        config=service_config,
        output_databases=[database for database in databases if database.enabled],
        batteries=batteries,
        bus=bus,
        command_log_csv=bems.control.command_log_csv,
    )
    return service, clients


async def run():
    logger.info("App.run()")

    collectors = create_collectors(config_obj)

    databases = create_databases(config_obj)

    calculators = create_calculators(config_obj.calculators)
    calculations = [calculation for calculator in config_obj.calculators if calculator.enabled for calculation in calculator.calculations]
    estimators = create_estimators(
        config_obj.estimators,
        calculations,
    )

    bus = MeasurementBus()

    manager = MeasurementManager(
        collectors=collectors,
        databases=databases,
        estimators=estimators,
        calculators=calculators,
        timezone=config_obj.collection.timezone,
        storage_config=config_obj.storage,
        bus=bus,
    )

    live_service, adapter_clients = _create_bems_live_service(config_obj, databases, bus)

    tasks = [
        asyncio.create_task(
            manager.run(),
            name="measurement-manager",
        )
    ]

    if live_service is not None:
        logger.info("BEMS live control attached to the measurement bus")
        tasks.append(
            asyncio.create_task(
                live_service.run_forever_bus(),
                name="bems-live",
            )
        )

    try:
        await asyncio.gather(*tasks)

    finally:
        # Cancel siblings so a manager failure cannot leave live control (and
        # its vendor sockets) running after the collection loops stopped.
        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("App shutting down")

        for client in adapter_clients:
            try:
                await client.close()
            except Exception:
                logger.exception("Failed to close BEMS adapter client")

        for database in databases:
            database.close()


def main():
    setup_logging()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def shutdown(signum, _frame):
        signal_name = signal.Signals(signum).name
        logger.info("Shutdown signal received: %s (%s)", signal_name, signum)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        loop.run_until_complete(run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Application stopped")
    finally:
        loop.close()


if __name__ == "__main__":  # pragma: no cover
    main()
