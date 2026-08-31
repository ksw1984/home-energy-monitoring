import asyncio
import json
from datetime import date
from pathlib import Path
from typing import TextIO

from src.collectors.definitions.measurement import Measurement
from src.databases.base_database import BaseDatabase


class TextFileDatabase(BaseDatabase):
    """Store measurements as JSON Lines files, one file per calendar day."""

    def __init__(self, directory: str):
        """Initialize the text-file database.

        Args:
            directory: Directory in which the daily JSONL files are stored.
        """
        self.directory = Path(directory)

    async def store(self, measurements: list[Measurement]) -> None:
        """Store measurements asynchronously.

        The file-writing operation is executed in a worker thread so that
        synchronous filesystem I/O does not block the asyncio event loop.

        Args:
            measurements: Measurements to persist.
        """
        await asyncio.to_thread(self._store, measurements)

    def _store(self, measurements: list[Measurement]) -> None:
        """Write measurements to daily JSON Lines files.

        Measurements are grouped by their timestamp date. Each measurement
        is serialized as one JSON object followed by a newline. Existing
        files are opened in append mode.

        Args:
            measurements: Measurements to write.
        """
        self.directory.mkdir(parents=True, exist_ok=True)

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
