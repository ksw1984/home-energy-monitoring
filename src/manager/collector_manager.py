import asyncio
from datetime import datetime

from src.collectors.definitions.measurement import Measurement

DAILY_METRICS = {
    "grid_import_energy_total",
    "grid_export_energy_total",
}


class CollectorManager:
    """Run all collectors and store their measurements.

    Daily meter energy values are handled specially. The manager stores
    ``grid_import_energy_total`` and ``grid_export_energy_total`` only once
    during hour 00:00. Both values must be available before the daily
    snapshot is written.

    All other measurements are stored on every collection cycle.
    """

    def __init__(
        self,
        collectors,
        database=None,
        interval=10,
    ):
        self.collectors = collectors
        self.database = database
        self.interval = interval

        # Start as if we are already outside the 00:00 hour.
        #
        # This means that if the application starts at 00:05, the first
        # collection is still allowed to create the daily snapshot.
        self._daily_energy_recorded = False

    async def run(self):

        print("CollectorManager.run()")
        await self.connect()

        try:
            while True:
                measurements = await self.collect_all()

                self.output(measurements)

                measurements_to_store = self._filter_measurements(
                    measurements,
                )

                if self.database is not None and measurements_to_store:
                    print("write to influx db")
                    await self.database.store(measurements_to_store)

                await asyncio.sleep(self.interval)

        finally:
            await self.disconnect()

    async def connect(self):
        await asyncio.gather(*[asyncio.to_thread(collector.connect) for collector in self.collectors])

    async def disconnect(self):
        await asyncio.gather(*[asyncio.to_thread(collector.disconnect) for collector in self.collectors])

    async def collect_all(self) -> list[Measurement]:

        print("CollectorManager.collect_all()")
        tasks = [asyncio.to_thread(collector.collect) for collector in self.collectors]

        results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        measurements: list[Measurement] = []

        for collector, result in zip(self.collectors, results, strict=True):
            if isinstance(result, BaseException):
                print(f"Collector {collector.__class__.__name__} failed: {result}")
                continue

            if not result:
                print(f"Collector {collector.__class__.__name__} unavailable: {result}")
                continue

            measurements.extend(result)

        return measurements

    def _filter_measurements(
        self,
        measurements: list[Measurement],
    ) -> list[Measurement]:
        """Return measurements that should be written to the database.

        Normal measurements are always returned.

        Daily meter energy values are only returned during hour 00 and only
        after both required values have been collected.
        """
        now = datetime.now().astimezone()

        # We have entered hour 01 or later. This resets the daily snapshot
        # flag so that the next midnight can store a new snapshot.
        if now.hour != 0:
            self._daily_values_stored = False

        # Outside midnight: discard daily energy measurements.
        if now.hour != 0:
            return [measurement for measurement in measurements if measurement.metric not in DAILY_METRICS]

        # Already stored this midnight.
        if self._daily_values_stored:
            return [measurement for measurement in measurements if measurement.metric not in DAILY_METRICS]

        daily_measurements = [measurement for measurement in measurements if measurement.metric in DAILY_METRICS]

        daily_metrics_received = {measurement.metric for measurement in daily_measurements}

        # We need both meter values before storing the daily snapshot.
        if not DAILY_METRICS.issubset(daily_metrics_received):
            return [measurement for measurement in measurements if measurement.metric not in DAILY_METRICS]

        # Both daily meter values are available. Store them now.
        self._daily_values_stored = True

        return measurements

    @staticmethod
    def output(measurements: list[Measurement]):
        for measurement in measurements:
            print(
                f"{measurement.timestamp.isoformat()} "
                f"{measurement.source:10} "
                f"{measurement.metric:20} "
                f"{measurement.value:<10.3f} "
                f"{measurement.unit}"
                f" ",
            )
