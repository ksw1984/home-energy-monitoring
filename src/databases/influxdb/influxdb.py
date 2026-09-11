import asyncio
import logging

from src.collectors.definitions.measurement import Measurement
from src.databases.base_database import BaseDatabase

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

logger = logging.getLogger(__name__)


class InfluxDatabase(BaseDatabase):
    def __init__(
        self,
        *,
        url: str,
        token: str,
        org: str,
        bucket: str,
        enabled: bool = True,
    ):
        super().__init__(
            type="influxdb",
            enabled=enabled,
        )

        self.bucket = bucket
        self.org = org

        self.client = InfluxDBClient(
            url=url,
            token=token,
            org=org,
        )

        self.write_api = self.client.write_api(
            write_options=SYNCHRONOUS,
        )

    def connect(self) -> None:
        logger.info("Connecting to InfluxDB")

        if not self.client.ping():
            raise ConnectionError("InfluxDB ping failed")

        logger.info("Connected to InfluxDB")

    async def store(self, measurements: list[Measurement]) -> None:
        if not self.enabled:
            return

        points = [
            Point(measurement.metric).tag("source", measurement.source).field("value", measurement.value).time(measurement.timestamp)
            for measurement in measurements
        ]

        await asyncio.to_thread(
            self.write_api.write,
            bucket=self.bucket,
            org=self.org,
            record=points,
        )

    def close(self) -> None:
        self.client.close()
