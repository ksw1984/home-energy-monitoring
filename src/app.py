import asyncio
import logging

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
        for database in databases:
            database.close()


def main():
    setup_logging()
    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover
    main()
