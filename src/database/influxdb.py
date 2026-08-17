import asyncio

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from src.collectors.definitions.measurement import Measurement


class InfluxDatabase:
    def __init__(
        self,
        url: str,
        token: str,
        org: str,
        bucket: str,
    ):
        self.bucket = bucket
        self.org = org

        self.client = InfluxDBClient(
            url=url,
            token=token,
            org=org,
        )

        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)

    async def store(self, measurements: list[Measurement]) -> None:
        points = [
            Point(measurement.metric)
            .tag("source", measurement.source)
            .field("value", measurement.value)
            .time(measurement.timestamp)
            for measurement in measurements
        ]
        print("points", points)
        print("write to db")
        await asyncio.to_thread(
            self.write_api.write,
            bucket=self.bucket,
            org=self.org,
            record=points,
        )

    def close(self):
        self.client.close()
