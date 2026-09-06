import asyncio
import logging
import signal

from src.collectors.collector_factory import create_collectors
from src.config import config_obj
from src.databases.database_factory import create_databases
from src.logger.logging_config import setup_logging
from src.manager.collector_manager import CollectorManager

logger = logging.getLogger(__name__)

#
#  poetry run python -m app
#


async def run():
    logger.info("App.run()")

    collectors = create_collectors(config_obj)

    databases = create_databases(config_obj)

    manager = CollectorManager(
        collectors=collectors,
        databases=databases,
        interval=config_obj.collection.interval,
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

    def shutdown(signum, frame):
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
