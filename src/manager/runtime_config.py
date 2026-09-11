import asyncio
import logging

from src.collectors.base_collector import BaseCollector
from src.databases.base_database import BaseDatabase

logger = logging.getLogger(__name__)


async def configure_collectors(
    collectors: list[BaseCollector],
    config,
) -> None:
    collector_configs = {item.attributes["source"]: item for item in config.collectors}

    for collector in collectors:
        collector_config = collector_configs.get(collector.source)

        if collector_config is None:
            logger.warning(
                "No configuration found for collector %s (source=%s)",
                collector.__class__.__name__,
                collector.source,
            )
            continue

        if collector_config.interval is None:
            logger.warning(
                "No interval configured for collector %s (source=%s)",
                collector.__class__.__name__,
                collector.source,
            )
            continue

        collector.configure_runtime(
            interval=collector_config.interval,
            enabled=collector_config.enabled,
        )

        logger.info(
            "Configured collector %s (source=%s): enabled=%s interval=%ss",
            collector.__class__.__name__,
            collector.source,
            collector.enabled,
            collector.interval,
        )


async def configure_databases(
    databases: list[BaseDatabase],
    config,
) -> None:
    database_configs = {item.type: item for item in config.databases}

    for database in databases:
        database_config = database_configs.get(database.type)

        if database_config is None:
            logger.warning(
                "No configuration found for database %s (type=%s)",
                database.__class__.__name__,
                database.type,
            )
            continue

        was_enabled = database.enabled
        should_be_enabled = database_config.enabled

        database.configure_runtime(
            enabled=should_be_enabled,
        )

        if not was_enabled and should_be_enabled:
            try:
                await asyncio.to_thread(database.connect)

            except Exception:
                logger.exception(
                    "Failed to connect database %s (type=%s)",
                    database.__class__.__name__,
                    database.type,
                )

        elif was_enabled and not should_be_enabled:
            try:
                database.close()

            except Exception:
                logger.exception(
                    "Failed to close database %s (type=%s)",
                    database.__class__.__name__,
                    database.type,
                )

        logger.info(
            "Configured database %s (type=%s): enabled=%s",
            database.__class__.__name__,
            database.type,
            database.enabled,
        )
