import asyncio
import logging

from src.collectors.base_collector import BaseCollector
from src.databases.base_database import BaseDatabase

import serial

logger = logging.getLogger(__name__)


# ###########
# Collectors
# ###########


async def connect_collectors(
    collectors: list[BaseCollector],
) -> None:
    """Attempt to connect all configured collectors.

    Collector connections are established concurrently. A connection
    failure in one collector is captured and logged without preventing
    the remaining collectors from connecting.

    Individual collectors may retry their connection during subsequent
    collection cycles if they remain unavailable.
    """
    results = await asyncio.gather(
        *[asyncio.to_thread(collector.connect) for collector in collectors],
        return_exceptions=True,
    )

    for collector, result in zip(
        collectors,
        results,
        strict=True,
    ):
        if isinstance(result, BaseException):
            log_collector_connection_error(
                collector,
                result,
            )


async def disconnect_collectors(
    collectors: list[BaseCollector],
) -> None:
    """Disconnect all configured collectors.

    Collector disconnections are performed concurrently. A failure
    while disconnecting one collector does not prevent the remaining
    collectors from being disconnected.
    """
    results = await asyncio.gather(
        *[asyncio.to_thread(collector.disconnect) for collector in collectors],
        return_exceptions=True,
    )

    for collector, result in zip(
        collectors,
        results,
        strict=True,
    ):
        if isinstance(result, BaseException):
            log_collector_disconnect_error(
                collector,
                result,
            )


def log_collector_connection_error(
    collector: BaseCollector,
    result: BaseException,
) -> None:
    name = collector.__class__.__name__

    if isinstance(result, serial.SerialException):
        logger.warning(
            "Collector %s unavailable: %s",
            name,
            result,
        )
    else:
        logger.error(
            "Collector %s failed: %s",
            name,
            result,
            exc_info=(
                type(result),
                result,
                result.__traceback__,
            ),
        )


def log_collector_disconnect_error(
    collector: BaseCollector,
    result: BaseException,
) -> None:
    logger.error(
        "Failed to disconnect collector %s: %s",
        collector.__class__.__name__,
        result,
        exc_info=(
            type(result),
            result,
            result.__traceback__,
        ),
    )


# ###########
# Databases
# ###########


async def connect_databases(
    databases: list[BaseDatabase],
) -> None:
    enabled_databases = [database for database in databases if database.enabled]

    results = await asyncio.gather(
        *[asyncio.to_thread(database.connect) for database in enabled_databases],
        return_exceptions=True,
    )

    for database, result in zip(
        enabled_databases,
        results,
        strict=True,
    ):
        if isinstance(result, BaseException):
            log_database_connection_error(
                database,
                result,
            )


async def disconnect_databases(
    databases: list[BaseDatabase],
) -> None:
    enabled_databases = [database for database in databases if database.enabled]

    results = await asyncio.gather(
        *[asyncio.to_thread(database.close) for database in enabled_databases],
        return_exceptions=True,
    )

    for database, result in zip(
        enabled_databases,
        results,
        strict=True,
    ):
        if isinstance(result, BaseException):
            log_database_disconnect_error(
                database,
                result,
            )


def log_database_connection_error(
    database: BaseDatabase,
    result: BaseException,
) -> None:
    logger.error(
        "Failed to connect database %s (type=%s): %s",
        database.__class__.__name__,
        database.type,
        result,
        exc_info=(
            type(result),
            result,
            result.__traceback__,
        ),
    )


def log_database_disconnect_error(
    database: BaseDatabase,
    result: BaseException,
) -> None:
    logger.error(
        "Failed to disconnect database %s (type=%s): %s",
        database.__class__.__name__,
        database.type,
        result,
        exc_info=(
            type(result),
            result,
            result.__traceback__,
        ),
    )
