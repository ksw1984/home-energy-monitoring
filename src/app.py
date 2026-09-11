import asyncio
import logging
import signal

from src.collectors.collector_factory import create_collectors
from src.config.config import config_obj
from src.databases.database_factory import create_databases
from src.estimators.estimator_factory import create_estimators
from src.logger.logging_config import setup_logging
from src.manager.measurement_manager import MeasurementManager

logger = logging.getLogger(__name__)

#
#  poetry run python -m app
#


async def run():
    logger.info("App.run()")

    collectors = create_collectors(config_obj)

    databases = create_databases(config_obj)

    estimators = create_estimators(config_obj.estimators)

    manager = MeasurementManager(
        collectors=collectors,
        databases=databases,
        estimators=estimators,
        calculators=[],
        timezone=config_obj.collection.timezone,
        storage_config=config_obj.storage,
    )

    try:
        await manager.run()
    finally:
        logger.info("App shutting down")

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
