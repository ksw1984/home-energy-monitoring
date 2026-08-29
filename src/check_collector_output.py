import logging

from src.collectors.collector_factory import create_collectors
from src.config import config_obj
from src.databases.database_factory import create_databases
from src.logger.logging_config import setup_logging

logger = logging.getLogger(__name__)


def log_measurements(name, measurements):
    logger.info("")
    logger.info("=" * 70)
    logger.info(name)
    logger.info("=" * 70)

    if not measurements:
        logger.info("No measurements returned.")
        return

    for measurement in measurements:
        logger.info(
            f"{measurement.timestamp.isoformat()} "
            f"{measurement.source:10} "
            f"{measurement.metric:30} "
            f"{measurement.value:<12.3f} "
            f"{measurement.unit:8} "
            f"{measurement.measurement_type}"
        )


def check_collectors():
    logger.info("App.run()")

    collectors = create_collectors(config_obj)

    _databases = create_databases(config_obj)

    try:
        for collector in collectors:
            logger.info(f"\nCollecting from {collector.__class__.__name__}...")

            measurements = collector.collect()

            log_measurements(
                collector.__class__.__name__,
                measurements,
            )

            assert isinstance(measurements, list)

    finally:

        for collector in collectors:

            if hasattr(collector, "disconnect"):
                logger.info(f"\nDisconnecting " f"{collector.__class__.__name__}...")

                collector.disconnect()


if __name__ == "__main__":
    setup_logging()
    check_collectors()
