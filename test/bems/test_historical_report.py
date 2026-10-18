import asyncio
import csv
import json
from dataclasses import replace
from datetime import datetime, timedelta, UTC

from src.bems.historical import simulate_historical
from src.collectors.definitions.common.measurement import Measurement
from src.config.config import config_obj
from src.databases.base_database import BaseDatabase


class HistoryMemoryDatabase(BaseDatabase):
    """Minimal history backend that records any attempted database writes."""

    def __init__(self, measurements: list[Measurement]) -> None:
        super().__init__(type="memory", enabled=True)
        self.measurements = measurements
        self.store_calls = 0

    async def store(self, measurements: list[Measurement]) -> None:
        self.store_calls += 1

    async def query_measurements(  # noqa: PLR0913 - follows the database query interface.
        self,
        *,
        source: str,
        metrics: list[str],
        start: datetime | None = None,
        stop: datetime | None = None,
        after: datetime | None = None,
        limit: int = 1000,
    ) -> list[Measurement]:
        points = [
            point
            for point in self.measurements
            if point.source == source
            and point.metric in metrics
            and (start is None or point.timestamp >= start)
            and (stop is None or point.timestamp < stop)
            and (after is None or point.timestamp > after)
        ]
        return sorted(points, key=lambda point: point.timestamp)[:limit]


def test_historical_simulation_writes_local_reports_but_not_to_database(tmp_path):
    bems = config_obj.bems
    assert bems is not None
    device = replace(bems.devices[0], enabled=True)
    settings = replace(bems, devices=[device])
    start = datetime(2026, 1, 15, 12, tzinfo=UTC)
    grid = settings.inputs.grid
    household = settings.inputs.household
    pv = settings.inputs.pv
    points = []
    for timestamp in (start, start + timedelta(seconds=10)):
        points.extend(
            [
                Measurement(timestamp, grid.source, grid.metric, -800, "W"),
                Measurement(timestamp, household.source, household.metric, -500, "W"),
                Measurement(timestamp, pv.source, pv.metric, 1000, "W"),
            ]
        )
    database = HistoryMemoryDatabase(points)

    run_id = asyncio.run(
        simulate_historical(
            start=start,
            stop=start + timedelta(seconds=20),
            config=settings,
            history_database=database,
            output_dir=tmp_path,
            device_ids={device.id},
            run_id="test-run",
        )
    )

    report_dir = tmp_path / run_id
    assert database.store_calls == 0
    assert (report_dir / "index.html").exists()
    assert (report_dir / f"report_{device.id}.html").exists()
    assert (report_dir / f"measurements_{device.id}.jsonl").exists()
    assert (report_dir / f"timeseries_{device.id}.csv").exists()
    assert (report_dir / f"weekly_{device.id}.csv").exists()
    with (report_dir / f"daily_{device.id}.csv").open(encoding="utf-8", newline="") as file:
        daily = list(csv.DictReader(file))
    assert daily[0]["date"] == "2026-01-15"
    assert float(daily[0]["grid_import_saved_kwh"]) > 0

    with (report_dir / f"measurements_{device.id}.jsonl").open(encoding="utf-8") as file:
        first_measurement = json.loads(next(file))
    assert first_measurement["source"].startswith("bems_sim_")
