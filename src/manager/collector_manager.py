import asyncio


from src.collectors.definitions.measurement import Measurement


class CollectorManager:
    def __init__(
        self,
        collectors,
        database=None,
        interval=10,
    ):
        self.collectors = collectors
        self.database = database
        self.interval = interval

    async def run(self):

        print("CollectorManager.run()")
        await self.connect()

        try:
            while True:
                measurements = await self.collect_all()

                self.output(measurements)

                if self.database is not None:
                    await self.database.store(measurements)

                await asyncio.sleep(self.interval)

        finally:
            await self.disconnect()

    async def collect_all(self) -> list[Measurement]:

        print("CollectorManager.collect_all()")
        tasks = [asyncio.to_thread(collector.collect) for collector in self.collectors]

        results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        measurements = []

        for collector, result in zip(self.collectors, results):
            if isinstance(result, Exception):
                print(f"Collector {collector.__class__.__name__} failed: " f"{result}")
                continue

            measurements.extend(result)

        return measurements

    async def connect(self):
        await asyncio.gather(
            *[asyncio.to_thread(collector.connect) for collector in self.collectors]
        )

    async def disconnect(self):
        await asyncio.gather(
            *[asyncio.to_thread(collector.disconnect) for collector in self.collectors]
        )

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
