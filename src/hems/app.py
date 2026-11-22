"""HEMS monitoring and control process entry point."""

import asyncio
import logging
import signal

from src.config.config import config_obj
from src.databases.database_factory import create_databases
from src.hems.calculators.calculator_factory import create_calculators
from src.hems.collectors.collector_factory import create_collectors
from src.hems.estimators.estimator_factory import create_estimators
from src.hems.manager.measurement_manager import MeasurementManager
from src.logger.logging_config import setup_logging
from src.shared.measurement_bus import MeasurementBus
from src.shared.mqtt import MqttMeasurementPublisher

logger = logging.getLogger(__name__)


async def run() -> None:
    """Run HEMS collection, calculation and persistence without starting BEMS."""
    collectors = create_collectors(config_obj)
    databases = create_databases(config_obj)
    calculators = create_calculators(config_obj.calculators)
    calculations = [calculation for calculator in config_obj.calculators if calculator.enabled for calculation in calculator.calculations]
    estimators = create_estimators(config_obj.estimators, calculations)
    bus = MeasurementBus()
    publisher = MqttMeasurementPublisher()
    publisher.configure(config_obj)
    await publisher.start()
    manager = MeasurementManager(
        collectors=collectors,
        databases=databases,
        estimators=estimators,
        calculators=calculators,
        timezone=config_obj.collection.timezone,
        storage_config=config_obj.storage,
        bus=bus,
        live_publisher=publisher.publish,
        configure_live_publisher=publisher.configure,
    )
    try:
        await manager.run()
    finally:
        await publisher.close()
        for database in databases:
            database.close()


def main() -> None:
    """Start HEMS with graceful signal handling."""
    setup_logging()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def shutdown(signum, _frame) -> None:
        logger.info("Shutdown signal received: %s", signal.Signals(signum).name)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        loop.run_until_complete(run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("HEMS stopped")
    finally:
        loop.close()


if __name__ == "__main__":  # pragma: no cover
    main()
