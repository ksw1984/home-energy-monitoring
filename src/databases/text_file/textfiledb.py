import asyncio
import json
import logging
from datetime import datetime, UTC
from pathlib import Path
from typing import TextIO, TYPE_CHECKING

from src.collectors.definitions.common.measurement import Measurement
from src.databases.base_database import BaseDatabase

if TYPE_CHECKING:
    from datetime import date

logger = logging.getLogger(__name__)


class TextFileDatabase(BaseDatabase):
    """Store measurements as JSON Lines files, one file per calendar day."""

    def __init__(
        self,
        *,
        directory: str,
        enabled: bool = True,
    ):
        """Initialize the text-file database.

        Args:
            directory: Directory in which the daily JSONL files are stored.
        """
        super().__init__(
            type="text_file",
            enabled=enabled,
        )
        self.directory = Path(directory)

    async def store(self, measurements: list[Measurement]) -> None:
        """Store measurements asynchronously.

        The file-writing operation is executed in a worker thread so that
        synchronous filesystem I/O does not block the asyncio event loop.

        Args:
            measurements: Measurements to persist.
        """
        if not self.enabled:
            return

        await asyncio.to_thread(
            self._store,
            measurements,
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
            raise RuntimeError("Cannot query a disabled text-file database")
        if limit <= 0:
            raise ValueError("limit must be positive")

        return await asyncio.to_thread(
            self._query_measurements,
            source,
            set(metrics),
            start,
            stop,
            after,
            limit,
        )

    def _query_measurements(  # noqa: C901, PLR0912, PLR0913, PLR0917 - JSONL filtering is a bounded file scan.
        self,
        source: str,
        metrics: set[str],
        start: datetime | None,
        stop: datetime | None,
        after: datetime | None,
        limit: int,
    ) -> list[Measurement]:
        if not metrics:
            return []
        if start is None:
            if after is None:
                raise ValueError("start or after must be provided")
            first_date = after.date()
        else:
            first_date = start.date()
        last_date = stop.date() if stop is not None else datetime.now(UTC).date()
        if last_date < first_date:
            return []

        measurements: list[Measurement] = []
        for day_index in range((last_date - first_date).days + 1):
            day = first_date.fromordinal(first_date.toordinal() + day_index)
            path = self.directory / f"{day.isoformat()}.jsonl"
            if not path.exists():
                continue

            with path.open(encoding="utf-8") as file:
                for line in file:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if record.get("source") != source or record.get("metric") not in metrics:
                        continue

                    timestamp = datetime.fromisoformat(record["timestamp"])
                    if after is not None and timestamp <= after:
                        continue
                    if start is not None and timestamp < start:
                        continue
                    if stop is not None and timestamp >= stop:
                        continue

                    measurements.append(
                        Measurement(
                            timestamp=timestamp,
                            source=str(record["source"]),
                            metric=str(record["metric"]),
                            value=float(record["value"]),
                            unit=str(record.get("unit", "")),
                            measurement_type=str(record.get("measurement_type", "current")),
                        )
                    )
                    if len(measurements) >= limit:
                        break

            if len(measurements) >= limit:
                break

        measurements.sort(key=lambda item: item.timestamp)
        return measurements[:limit]

    def _store(self, measurements: list[Measurement]) -> None:
        """Write measurements to daily JSON Lines files.

        Measurements are grouped by their timestamp date. Each measurement
        is serialized as one JSON object followed by a newline. Existing
        files are opened in append mode.

        Args:
            measurements: Measurements to write.
        """
        self.directory.mkdir(parents=True, exist_ok=True)

        logger.info(f"Write to jsonl in {self.directory.absolute()}")
        files: dict[date, TextIO] = {}

        try:
            for measurement in measurements:
                day = measurement.timestamp.date()
                file = files.get(day)

                if file is None:
                    path = self.directory / f"{day.isoformat()}.jsonl"
                    file = path.open("a", encoding="utf-8")
                    files[day] = file

                record = {
                    "timestamp": measurement.timestamp.isoformat(),
                    "source": measurement.source,
                    "metric": measurement.metric,
                    "value": measurement.value,
                    "unit": measurement.unit,
                    "measurement_type": measurement.measurement_type,
                }

                file.write(json.dumps(record, ensure_ascii=False) + "\n")

            for file in files.values():
                file.flush()

        finally:
            for file in files.values():
                file.close()

    def close(self) -> None:
        """Close the database.

        No persistent resources are held between calls to ``store()``, so
        there is nothing to close.
        """
        return
