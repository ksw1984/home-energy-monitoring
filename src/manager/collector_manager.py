import asyncio
import logging
from datetime import datetime

from src.collectors.base_collector import BaseCollector
from src.collectors.definitions.measurement import Measurement
from src.databases.base_database import BaseDatabase

logger = logging.getLogger(__name__)

METER_DAILY_METRICS = {
    "grid_import_energy_total",
    "grid_export_energy_total",
}
METER_CURRENT_METRICS = {
    "grid_import_power",
    "grid_export_power",
}


class CollectorManager:
    """Run all collectors and store their measurements.

    All collectors are executed every ``interval`` seconds.

    Current meter power values are stored on every collection cycle.

    Daily meter energy values are stored only once during hour 00:00,
    after the required values from both meter sources are available.

    All other measurements are stored on every collection cycle.
    """

    def __init__(
        self,
        collectors: list[BaseCollector],
        databases: list[BaseDatabase] | None = None,
        interval: int = 300,
    ):
        self.collectors = collectors
        self.databases = databases
        self.interval = interval

        # True after the daily meter snapshot has been stored during
        # the current midnight hour.
        self._daily_values_stored = False

    async def run(self):

        logger.info("CollectorManager.run()")
        await self.connect()

        try:
            while True:
                measurements = await self.collect_all()

                self.output(measurements)

                measurements_to_store = self._filter_measurements(
                    measurements,
                )

                if self.databases is not None and measurements_to_store:
                    logger.info("Write to dbs")
                    for db in self.databases:
                        try:
                            await db.store(measurements_to_store)
                        except Exception:
                            logger.exception(
                                "Failed to store measurements in %s",
                                db.__class__.__name__,
                            )

                await asyncio.sleep(self.interval)

        finally:
            await self.disconnect()

    async def connect(self):
        await asyncio.gather(*[asyncio.to_thread(collector.connect) for collector in self.collectors])

    async def disconnect(self):
        await asyncio.gather(*[asyncio.to_thread(collector.disconnect) for collector in self.collectors])

    async def collect_all(self) -> list[Measurement]:

        logger.info("CollectorManager.collect_all()")
        tasks = [asyncio.to_thread(collector.collect) for collector in self.collectors]

        results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        measurements: list[Measurement] = []

        for collector, result in zip(self.collectors, results, strict=True):
            if isinstance(result, BaseException):
                logger.error(f"Collector {collector.__class__.__name__} failed: {result}")
                continue

            if not result:
                logger.warning(f"Collector {collector.__class__.__name__} unavailable: {result}")
                continue

            measurements.extend(result)

        return measurements

    def _filter_measurements(
        self,
        measurements: list[Measurement],
    ) -> list[Measurement]:
        """Return measurements that should be written to the databases.

        Current meter power measurements are always stored.

        Daily meter energy measurements are stored only once during
        hour 00:00, after all required values from both meter sources
        are available.

        All other measurements are always stored.
        """
        now = datetime.now().astimezone()

        #
        # Reset the daily flag once we leave midnight.
        #
        if now.hour != 0:
            self._daily_values_stored = False

        #
        # Current measurements are always stored.
        #
        measurements_to_store = [
            measurement for measurement in measurements if measurement.metric in METER_CURRENT_METRICS
        ]

        #
        # Daily measurements are stored only once at midnight.
        #
        if now.hour == 0 and not self._daily_values_stored:
            daily_measurements = [
                measurement for measurement in measurements if measurement.metric in METER_DAILY_METRICS
            ]

            daily_metrics_received = {measurement.metric for measurement in daily_measurements}

            if METER_DAILY_METRICS.issubset(daily_metrics_received):
                measurements_to_store.extend(daily_measurements)
                self._daily_values_stored = True

        #
        # All other measurements are stored every cycle.
        #
        measurements_to_store.extend(
            measurement
            for measurement in measurements
            if measurement.metric not in METER_CURRENT_METRICS and measurement.metric not in METER_DAILY_METRICS
        )

        return measurements_to_store

    @staticmethod
    def output(measurements: list[Measurement]):
        for measurement in measurements:
            logger.info(
                f"{measurement.timestamp.isoformat()} "
                f"{measurement.source:10} "
                f"{measurement.metric:20} "
                f"{measurement.value:<10.3f} "
                f"{measurement.unit}"
                f" ",
            )
