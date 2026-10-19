import asyncio
import json
import logging
from datetime import datetime

from src.collectors.definitions.common.measurement import Measurement
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
        self.query_api = self.client.query_api()

    def connect(self) -> None:
        logger.info("Connecting to InfluxDB")

        if not self.client.ping():
            raise ConnectionError("InfluxDB ping failed")

        logger.info("Connected to InfluxDB")

    async def store(self, measurements: list[Measurement]) -> None:
        if not self.enabled:
            return

        points = [
            (
                Point(measurement.metric)
                .tag("source", measurement.source)
                .field("value", measurement.value)
                .field("unit", measurement.unit)
                .field("measurement_type", measurement.measurement_type)
                .time(measurement.timestamp)
            )
            for measurement in measurements
        ]

        await asyncio.to_thread(
            self.write_api.write,
            bucket=self.bucket,
            org=self.org,
            record=points,
        )

    async def query_measurements(  # noqa: PLR0913 - mirrors the shared time-series query contract.
        self,
        *,
        source: str,
        metrics: list[str],
        start: datetime | None = None,
        stop: datetime | None = None,
        after: datetime | None = None,
        limit: int = 1000,
    ) -> list[Measurement]:
        if not self.enabled:
            raise RuntimeError("Cannot query a disabled InfluxDB database")
        if not metrics:
            return []
        if start is None and after is None:
            raise ValueError("start or after must be provided")
        if limit <= 0:
            raise ValueError("limit must be positive")

        flux = self._build_measurement_query(
            source=source,
            metrics=metrics,
            start=start,
            stop=stop,
            after=after,
            limit=limit,
        )
        tables = await asyncio.to_thread(self.query_api.query, flux, org=self.org)
        measurements: list[Measurement] = []
        for table in tables:
            for record in table.records:
                values = record.values
                timestamp = values["_time"]
                if isinstance(timestamp, str):
                    timestamp = datetime.fromisoformat(timestamp)
                measurements.append(
                    Measurement(
                        timestamp=timestamp,
                        source=str(values["source"]),
                        metric=str(values["_measurement"]),
                        value=float(values["value"]),
                        unit=str(values.get("unit") or ""),
                        measurement_type=str(values.get("measurement_type") or "current"),
                    )
                )

        measurements.sort(key=lambda item: item.timestamp)
        return measurements[:limit]

    def _build_measurement_query(  # noqa: PLR0913 - maps the shared query contract to Flux.
        self,
        *,
        source: str,
        metrics: list[str],
        start: datetime | None,
        stop: datetime | None,
        after: datetime | None,
        limit: int,
    ) -> str:
        start_time = start or after
        if start_time is None:
            raise ValueError("start or after must be provided")
        start_literal = json.dumps(start_time.isoformat())
        # Flux parses RFC3339 timestamps with time(); datetime() is not a
        # built-in Flux function and makes the query fail at runtime.
        stop_range = f"time(v: {json.dumps(stop.isoformat())})" if stop is not None else "now()"
        metric_set = ", ".join(json.dumps(metric) for metric in metrics)
        source_literal = json.dumps(source)
        query = (
            f"from(bucket: {json.dumps(self.bucket)})\n"
            f"  |> range(start: time(v: {start_literal}), stop: {stop_range})\n"
            f"  |> filter(fn: (r) => r.source == {source_literal})\n"
            f"  |> filter(fn: (r) => contains(value: r._measurement, set: [{metric_set}]))\n"
            '  |> filter(fn: (r) => r._field == "value" or r._field == "unit" or r._field == "measurement_type")\n'
        )
        if after is not None:
            query += f"  |> filter(fn: (r) => r._time > time(v: {json.dumps(after.isoformat())}))\n"
        if stop is not None:
            query += f"  |> filter(fn: (r) => r._time < time(v: {json.dumps(stop.isoformat())}))\n"
        query += (
            '  |> pivot(rowKey: ["_time", "_measurement", "source"], columnKey: ["_field"], valueColumn: "_value")\n'
            '  |> sort(columns: ["_time"])\n'
            f"  |> limit(n: {limit})"
        )
        return query

    def close(self) -> None:
        self.client.close()
