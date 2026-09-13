import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from src.calculators.base_calculator import BaseCalculator
from src.collectors.base_collector import BaseCollector
from src.collectors.definitions.measurement import Measurement
from src.config.config import CONFIG_FILE, load_config, StorageConfig
from src.config.storage_filter import StorageFilter
from src.databases.base_database import BaseDatabase
from src.estimators.base_estimator import BaseEstimator
from src.manager.connection_lifecycle import (
    connect_collectors,
    connect_databases,
    disconnect_collectors,
    disconnect_databases,
)
from src.manager.runtime_config import (
    configure_collectors,
    configure_databases,
)

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


class MeasurementManager:
    """Run all collectors independently and store their measurements.

    Each collector is executed at its own configured interval.

    Collectors are independent of each other. A failure to connect to
    or collect from one collector does not prevent the remaining
    collectors from operating.

    Collectors that are temporarily unavailable may retry their
    connection during subsequent collection cycles.

    Measurements are collected independently of storage configuration.
    The storage filter determines which measurements are persisted.

    Daily meter energy values are stored at most once during hour 00:00,
    after the required values are available.

    All other configured measurements are stored immediately after
    successful collection.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        collectors: list[BaseCollector],
        databases: list[BaseDatabase],
        estimators: list[BaseEstimator],
        calculators: list[BaseCalculator],
        timezone: str,
        storage_config: StorageConfig,
    ):
        """Initialize the collector manager.

        Args:
            collectors: Collectors to execute independently.
            databases: Databases receiving the collected measurements.
            timezone: Timezone used for daily measurement handling.
        """
        self.collectors = collectors
        self.databases = databases
        self.estimators = estimators
        self.calculators = calculators

        self.timezone = ZoneInfo(timezone)
        self.storage_filter = StorageFilter(storage_config)

        self._config_mod_time = CONFIG_FILE.stat().st_mtime
        self._config_reload_lock = asyncio.Lock()

        # True after the daily meter snapshot has been stored during
        # the current midnight hour.
        self._daily_values_stored: set[str] = set()

        # Protects the daily measurement state when multiple collectors
        # finish at approximately the same time.
        self._filter_lock = asyncio.Lock()

    async def _reload_config_if_changed(self) -> None:
        async with self._config_reload_lock:
            try:
                mtime = CONFIG_FILE.stat().st_mtime
            except OSError:
                logger.exception("Failed to stat configuration file")
                return

            if mtime == self._config_mod_time:
                return

            logger.info("Configuration file changed, reloading")

            try:
                config = load_config()
            except Exception:
                logger.exception("Failed to reload configuration")
                return

            async with self._filter_lock:
                self.storage_filter.update(config.storage)

            await configure_collectors(
                self.collectors,
                config,
            )

            await configure_databases(
                self.databases,
                config,
            )

            self._config_mod_time = mtime

            logger.info("Configuration reloaded")

    async def run(self) -> None:
        """Run all collectors until the task is canceled.

        All configured collectors and enabled databases are initially connected before the
        collection loops start. A connection failure of an individual
        collector does not stop the other collectors from starting.

        Each collector is then executed independently at its configured
        interval. Measurements are written to the configured databases
        immediately after each successful collection.

        Cleanup is performed for all collectors when the collection
        manager exits.
        """
        logger.info("MeasurementManager.run()")

        try:
            await self.connect()

            tasks = [
                asyncio.create_task(
                    self._run_collector(collector),
                    name=f"collector-{collector.source}",
                )
                for collector in self.collectors
            ]

            await asyncio.gather(*tasks)

        finally:
            await self.disconnect()

    async def _run_collector(
        self,
        collector: BaseCollector,
    ) -> None:
        """Run one collector continuously at its configured interval.

        The collector is executed in a worker thread so that blocking
        collector implementations do not block the asyncio event loop.

        Measurements are filtered and written to the databases immediately
        after a successful collection. A failure of this collector does
        not affect any other collector.

        Args:
            collector: Collector to execute.
        """

        logger.info(
            "Starting collector %s with interval=%ss enabled=%s",
            collector.__class__.__name__,
            collector.interval,
            collector.enabled,
        )

        while True:
            started = asyncio.get_running_loop().time()

            try:
                await self._reload_config_if_changed()

                if not collector.enabled:
                    await asyncio.sleep(1)
                    continue

                measurements = await asyncio.to_thread(
                    collector.collect,
                )

                if measurements:
                    await self._process_measurements(measurements)

                else:
                    logger.warning(
                        "Collector %s unavailable: %s",
                        collector.__class__.__name__,
                        measurements,
                    )

            except serial.SerialException as exc:
                logger.warning(
                    "Collector %s unavailable: %s",
                    collector.__class__.__name__,
                    exc,
                )

            except Exception:
                logger.exception(
                    "Collector %s failed",
                    collector.__class__.__name__,
                )

            elapsed = asyncio.get_running_loop().time() - started
            delay = max(0.0, collector.interval - elapsed)

            await asyncio.sleep(delay)

    async def _process_measurements(
        self,
        measurements: list[Measurement],
    ) -> None:
        """Process collected measurements through estimators and calculators."""
        estimated: list[Measurement] = []

        for estimator in self.estimators:
            estimated.extend(
                estimator.estimate(measurements),
            )

        measurements = [
            *measurements,
            *estimated,
        ]

        calculated: list[Measurement] = []

        for calculator in self.calculators:
            calculated.extend(
                calculator.calculate(measurements),
            )

        measurements = [
            *measurements,
            *calculated,
        ]

        await self._store_measurements(measurements)

    async def _store_measurements(
        self,
        measurements: list[Measurement],
    ) -> None:
        """Filter and store measurements in all configured databases.

        Measurements are written immediately after the collector has
        successfully returned them.

        Args:
            measurements: Measurements returned by a collector.
        """
        self.output(measurements)

        async with self._filter_lock:
            measurements_to_store = self._filter_measurements(
                measurements,
            )

        if not measurements_to_store:
            return

        if not self.databases:
            return

        logger.info("Write to dbs")

        for database in self.databases:
            if not database.enabled:
                continue

            try:
                await database.store(measurements_to_store)

            except Exception:
                logger.exception(
                    "Failed to store measurements in %s",
                    database.__class__.__name__,
                )

    async def connect(self) -> None:
        """Attempt to connect all configured collectors and databases."""
        logger.info("MeasurementManager.connect()")

        await connect_collectors(self.collectors)
        await connect_databases(self.databases)

    async def disconnect(self) -> None:
        """Disconnect all configured collectors and databases."""
        logger.info("MeasurementManager.disconnect()")

        await disconnect_collectors(self.collectors)
        await disconnect_databases(self.databases)

    async def collect_all(self) -> list[Measurement]:
        """Collect measurements from all configured collectors.

        Collectors are executed concurrently. A failure in one collector
        does not prevent measurements from the remaining collectors from
        being returned.

        Expected serial connection failures are ignored here because the
        collector is responsible for retrying the connection during a
        subsequent collection cycle.

        This method is retained as a convenience method for collecting
        all collectors once. The main :meth:`run` loop uses independent
        collection loops for each collector.

        Returns:
            All measurements successfully returned by the collectors.
        """
        logger.info("MeasurementManager.collect_all()")

        tasks = [asyncio.to_thread(collector.collect) for collector in self.collectors]

        results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        measurements: list[Measurement] = []

        for collector, result in zip(
            self.collectors,
            results,
            strict=True,
        ):
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

    def _filter_measurements(
        self,
        measurements: list[Measurement],
    ) -> list[Measurement]:
        """Return measurements that should be written to the databases.

        Storage configuration determines which measurements are eligible for
        persistence. Daily meter measurements are stored once per source
        during the midnight hour. All other configured measurements are stored
        on every collection cycle.

            Args:
                measurements: Measurements collected during the current cycle.

            Returns:
                Measurements that should be written to the databases.
        """
        # 1. What is configured to be stored?
        measurements = self.storage_filter.filter(measurements)

        if not measurements:
            return []

        now = datetime.now(self.timezone)

        # Reset the daily state once we leave midnight.
        if now.hour != 0:
            self._daily_values_stored.clear()

        #
        # Current measurements are stored on every collection cycle.
        #
        measurements_to_store = [measurement for measurement in measurements if measurement.metric in METER_CURRENT_METRICS]

        #
        # Daily measurements are stored once per source during midnight.
        #
        if now.hour == 0:
            daily_measurements_by_source: dict[str, list[Measurement]] = {}

            for measurement in measurements:
                if measurement.metric in METER_DAILY_METRICS:
                    daily_measurements_by_source.setdefault(
                        measurement.source,
                        [],
                    ).append(measurement)

            for source, daily_measurements in daily_measurements_by_source.items():
                if source in self._daily_values_stored:
                    continue

                daily_metrics_received = {measurement.metric for measurement in daily_measurements}

                if METER_DAILY_METRICS.issubset(daily_metrics_received):
                    measurements_to_store.extend(daily_measurements)
                    self._daily_values_stored.add(source)

        #
        # All other measurements are stored on every collection cycle.
        #
        measurements_to_store.extend(
            measurement
            for measurement in measurements
            if (measurement.metric not in METER_CURRENT_METRICS and measurement.metric not in METER_DAILY_METRICS)
        )

        return measurements_to_store

    def output(
        self,
        measurements: list[Measurement],
    ) -> None:
        """Log all collected measurements.

        Args:
            measurements: Measurements collected during the current cycle.
        """
        for measurement in measurements:
            selected = "X" if self.storage_filter.is_selected(measurement) else ""

            calculated = "C" if measurement.source == "calculated" else ""

            estimated = "E" if measurement.source == "estimated" else ""

            status = f"{selected}{calculated}{estimated}"

            logger.info(
                "%-25s %-25s %-25s %14.3f %-5s %-4s",
                measurement.timestamp.isoformat(),
                measurement.source,
                measurement.metric,
                measurement.value,
                measurement.unit or "",
                status,
            )
