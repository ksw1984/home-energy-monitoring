import asyncio
import logging
from datetime import datetime

from src.collectors.base_collector import BaseCollector
from src.collectors.definitions.measurement import Measurement
from src.databases.base_database import BaseDatabase

import serial

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

    Collectors are independent of each other. A failure to connect to
    or collect from one collector does not prevent the remaining
    collectors from operating.

    Collectors that are temporarily unavailable may retry their
    connection during subsequent collection cycles.

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
        """Initialize the collector manager.

        Args:
            collectors: Collectors to execute during each collection cycle.
            databases: Databases receiving the collected measurements.
            interval: Delay in seconds between collection cycles.
        """
        self.collectors = collectors
        self.databases = databases
        self.interval = interval

        # True after the daily meter snapshot has been stored during
        # the current midnight hour.
        self._daily_values_stored = False

    async def run(self) -> None:
        """Run the collection loop until the task is cancelled.

        All configured collectors are initially connected before the
        collection loop starts. A connection failure of an individual
        collector does not stop the other collectors from starting.

        The collectors are then executed repeatedly at the configured
        interval. Cleanup is performed for all collectors when the
        collection loop exits.
        """
        logger.info("CollectorManager.run()")

        try:
            await self.connect()

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

    async def connect(self) -> None:
        """Attempt to connect all configured collectors.

        Collector connections are established concurrently. A connection
        failure in one collector is captured and logged without preventing
        the remaining collectors from connecting.

        Individual collectors may retry their connection during subsequent
        collection cycles if they remain unavailable.
        """
        logger.info("CollectorManager.connect()")

        results = await asyncio.gather(
            *[asyncio.to_thread(collector.connect) for collector in self.collectors],
            return_exceptions=True,
        )

        for collector, result in zip(self.collectors, results, strict=True):
            if isinstance(result, BaseException):
                self._log_collector_error(collector, result)

    async def disconnect(self) -> None:
        """Disconnect all configured collectors.

        Collector disconnections are performed concurrently. A failure
        while disconnecting one collector does not prevent the remaining
        collectors from being disconnected.
        """
        logger.info("CollectorManager.disconnect()")

        results = await asyncio.gather(
            *[asyncio.to_thread(collector.disconnect) for collector in self.collectors],
            return_exceptions=True,
        )

        for collector, result in zip(self.collectors, results, strict=True):
            if isinstance(result, BaseException):
                logger.error(
                    "Failed to disconnect collector %s: %s",
                    collector.__class__.__name__,
                    result,
                    exc_info=(type(result), result, result.__traceback__),
                )

    async def collect_all(self) -> list[Measurement]:
        """Collect measurements from all configured collectors.

        Collectors are executed concurrently. A failure in one collector
        does not prevent measurements from the remaining collectors from
        being returned.

        Expected serial connection failures are ignored here because the
        collector is responsible for retrying the connection during a
        subsequent collection cycle.

        Returns:
            All measurements successfully returned by the collectors.
        """
        logger.info("CollectorManager.collect_all()")

        tasks = [asyncio.to_thread(collector.collect) for collector in self.collectors]

        results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        measurements: list[Measurement] = []

        for collector, result in zip(self.collectors, results, strict=True):
            if isinstance(result, BaseException):
                if isinstance(result, serial.SerialException):
                    continue

                logger.error(
                    "Collector %s failed: %s",
                    collector.__class__.__name__,
                    result,
                    exc_info=(
                        type(result),
                        result,
                        result.__traceback__,
                    ),
                )
                continue

            if not result:
                logger.warning(
                    "Collector %s unavailable: %s",
                    collector.__class__.__name__,
                    result,
                )
                continue

            measurements.extend(result)

        return measurements

    @staticmethod
    def _log_collector_error(
        collector: BaseCollector,
        result: BaseException,
    ) -> None:
        """Log a collector connection failure.

        Expected serial connection failures are logged as warnings without
        a traceback. Other failures are logged as errors with their
        original traceback.

        Args:
            collector: Collector whose connection attempt failed.
            result: Exception raised by the connection attempt.
        """
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

    def _filter_measurements(
        self,
        measurements: list[Measurement],
    ) -> list[Measurement]:
        """Return measurements that should be written to the databases.

        Current meter power measurements are always stored.

        Daily meter energy measurements are stored only once during
        hour 00:00, after the required values from both meter sources
        are available.

        All other measurements are stored every collection cycle.

        Args:
            measurements: Measurements collected during the current cycle.

        Returns:
            Measurements that should be written to the databases.
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
        measurements_to_store = [measurement for measurement in measurements if measurement.metric in METER_CURRENT_METRICS]

        #
        # Daily measurements are stored only once at midnight.
        #
        if now.hour == 0 and not self._daily_values_stored:
            daily_measurements = [measurement for measurement in measurements if measurement.metric in METER_DAILY_METRICS]

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
    def output(measurements: list[Measurement]) -> None:
        """Log all collected measurements.

        Args:
            measurements: Measurements collected during the current cycle.
        """
        for measurement in measurements:
            logger.info(
                f"{measurement.timestamp.isoformat()} {measurement.source:10} {measurement.metric:20} {measurement.value:<10.3f} {measurement.unit} ",
            )
