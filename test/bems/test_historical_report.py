import asyncio
import csv
import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta, UTC

from src.bems.reporting.report import _chart_row
from src.bems.services.historical import simulate_historical
from src.config.config import config_obj
from src.databases.base_database import BaseDatabase
from src.shared.measurement import Measurement


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


def test_report_chart_uses_meter_signs_and_charge_positive_battery_power():
    row = _chart_row(
        datetime(2026, 1, 1, tzinfo=UTC),
        {
            "grid_power_reference": 230.0,
            "grid_power_with_battery": 20.0,
            "household_power_reference": 210.0,
            "heat_pump_power_reference": 20.0,
            "pv_power_reference": 0.0,
            "battery_actual_power": -210.0,
            "battery_soc": 49.9,
            "battery_energy_stored": 3500.0,
        },
    )

    assert row["meter_grid"] == 230.0
    assert row["grid_after"] == 20.0
    assert row["meter_household"] == 210.0
    assert row["battery"] == -210.0
    assert row["soc"] == 49.9
    assert row["energy_kwh"] == 3.5


def _historical_points(*, start: datetime, grid_w: float, household_w: float, pv_w: float = 0.0):
    bems = config_obj.bems
    assert bems is not None
    grid = bems.inputs.grid
    household = bems.inputs.household
    pv = bems.inputs.pv
    points = []
    for offset in (0, 10, 20, 30):
        timestamp = start + timedelta(seconds=offset)
        points.extend(
            [
                Measurement(timestamp, grid.source, grid.metric, grid_w, "W"),
                Measurement(timestamp, household.source, household.metric, household_w, "W"),
                Measurement(timestamp, pv.source, pv.metric, pv_w, "W"),
            ]
        )
    return points


def _run_historical(tmp_path, *, device, settings, start: datetime, points, run_id: str):  # noqa: PLR0913 - small test helper keeps simulation setup explicit.
    database = HistoryMemoryDatabase(points)
    asyncio.run(
        simulate_historical(
            start=start,
            stop=start + timedelta(seconds=40),
            config=settings,
            history_database=database,
            output_dir=tmp_path,
            device_ids={device.id},
            run_id=run_id,
        )
    )
    timeseries_path = tmp_path / run_id / f"timeseries_{device.id}.csv"
    with timeseries_path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file)), database


def test_historical_simulation_writes_local_reports_but_not_to_database(  # noqa: PLR0915 - integration assertions cover generated artifacts and logging.
    tmp_path, caplog
):
    caplog.set_level(logging.INFO, logger="src.bems.services.historical")
    bems = config_obj.bems
    assert bems is not None
    device = replace(bems.devices[0], enabled=True, initial_soc_percent=50)
    settings = replace(
        bems,
        devices=[device],
        tariffs=replace(
            bems.tariffs,
            household_import_ct_per_kwh=30.0,
            heat_pump_import_ct_per_kwh=25.0,
            feed_in_ct_per_kwh=7.8,
        ),
    )
    start = datetime(2026, 1, 15, 0, tzinfo=UTC)
    points = _historical_points(start=start, grid_w=800, household_w=500)
    timeseries, database = _run_historical(tmp_path, device=device, settings=settings, start=start, points=points, run_id="test-run")

    report_dir = tmp_path / "test-run"
    assert "Reading grid, household and PV measurements" in caplog.text
    assert "Fetched raw measurements: grid=4" in caplog.text
    assert "raw +800.000..+800.000 W" in caplog.text
    assert "Alignment kept 4/4 grid points" in caplog.text
    assert "Aligned 4 measurement points" in caplog.text
    assert "Generating report and plots for" in caplog.text
    assert "Historical simulation complete: 4 points" in caplog.text
    assert database.store_calls == 0
    assert (report_dir / "index.html").exists()
    assert (report_dir / f"report_{device.id}.html").exists()
    report_html = (report_dir / f"report_{device.id}.html").read_text(encoding="utf-8")
    assert 'src="plotly.min.js"' in report_html
    assert (report_dir / "plotly.min.js").exists()
    assert "rangeslider" in report_html
    assert "Grid import saved" in report_html
    assert "Start-SOC 50.0%" in report_html
    assert "Times shown in Europe/Berlin" in report_html
    assert "Stored energy (kWh)" in report_html
    assert "Battery state of charge and stored energy" in report_html
    assert "Net supplier cost without battery" in report_html
    assert "Feed-in impact €" in report_html
    assert "household import 30.00 ct/kWh" in report_html
    assert "Meter grid without battery (reference)" in report_html
    assert (report_dir / f"measurements_{device.id}.jsonl").exists()

    assert float(timeseries[0]["actual_power_w"]) == 0
    # 800W grid import = 500W household + 300W heat pump. At 50% SOC the
    # shrinking night reserve leaves enough surplus for heat-pump sustain, so
    # both household and heat pump can be discharged.
    assert all(float(row["requested_power_w"]) == -780 for row in timeseries)
    assert all(float(row["actual_power_w"]) == -780 for row in timeseries[1:])
    assert float(timeseries[-1]["soc_percent"]) < 50
    assert (report_dir / f"weekly_{device.id}.csv").exists()
    with (report_dir / f"daily_{device.id}.csv").open(encoding="utf-8", newline="") as file:
        daily = list(csv.DictReader(file))
    assert daily[0]["date"] == "2026-01-15"
    assert float(daily[0]["grid_import_saved_kwh"]) > 0
    assert "household_import_saved_kwh" in daily[0]
    assert "heat_pump_import_saved_kwh" in daily[0]
    assert "household_reference_cost_eur" in daily[0]
    assert "feed_in_reference_revenue_eur" in daily[0]
    assert (
        abs(float(daily[0]["household_import_saved_kwh"]) + float(daily[0]["heat_pump_import_saved_kwh"]) - float(daily[0]["grid_import_saved_kwh"]))
        < 0.00001
    )
    assert (
        abs(
            float(daily[0]["household_cost_saved_eur"])
            + float(daily[0]["heat_pump_cost_saved_eur"])
            + float(daily[0]["feed_in_cost_impact_eur"])
            - float(daily[0]["cost_saved_eur"])
        )
        < 0.0001
    )

    with (report_dir / f"measurements_{device.id}.jsonl").open(encoding="utf-8") as file:
        first_measurement = json.loads(next(file))
    assert first_measurement["source"].startswith("bems_sim_")


def test_historical_simulation_discharge_only_household_when_soc_is_below_night_reserve(tmp_path):
    bems = config_obj.bems
    assert bems is not None
    device = replace(bems.devices[0], enabled=True, initial_soc_percent=35)
    settings = replace(bems, devices=[device])
    start = datetime(2026, 1, 15, 0, tzinfo=UTC)
    points = _historical_points(start=start, grid_w=800, household_w=500)

    timeseries, _database = _run_historical(tmp_path, device=device, settings=settings, start=start, points=points, run_id="low-soc")

    # 35% SOC is below the shrinking night reserve at 00:00 UTC, so there is no
    # surplus for heat-pump sustain; only household demand may be served.
    assert all(float(row["requested_power_w"]) == -500 for row in timeseries)
    assert all(float(row["actual_power_w"]) == -500 for row in timeseries[1:])


def test_historical_simulation_allows_heat_pump_when_household_load_is_low(tmp_path):
    bems = config_obj.bems
    assert bems is not None
    device = replace(bems.devices[0], enabled=True, initial_soc_percent=50)
    settings = replace(bems, devices=[device])
    start = datetime(2026, 1, 15, 0, tzinfo=UTC)
    points = _historical_points(start=start, grid_w=400, household_w=100)

    timeseries, _database = _run_historical(tmp_path, device=device, settings=settings, start=start, points=points, run_id="low-household")

    # Low household load leaves enough energy above the shrinking night reserve
    # for heat-pump sustain, so household + heat pump can be served.
    assert all(float(row["requested_power_w"]) == -400 for row in timeseries)
    assert all(float(row["actual_power_w"]) == -400 for row in timeseries[1:])


def test_historical_simulation_allows_heat_pump_when_soc_has_surplus(tmp_path):
    bems = config_obj.bems
    assert bems is not None
    device = replace(bems.devices[0], enabled=True, initial_soc_percent=80)
    settings = replace(bems, devices=[device])
    start = datetime(2026, 1, 15, 0, tzinfo=UTC)
    points = _historical_points(start=start, grid_w=800, household_w=500)

    timeseries, _database = _run_historical(tmp_path, device=device, settings=settings, start=start, points=points, run_id="high-soc")

    # With high SOC there is clear surplus above the night reserve, so the heat
    # pump may discharge in addition to household demand.
    assert all(float(row["requested_power_w"]) == -780 for row in timeseries)
    assert all(float(row["actual_power_w"]) == -780 for row in timeseries[1:])


def test_historical_simulation_keeps_190_w_discharge_continuous_for_two_minutes(tmp_path):
    bems = config_obj.bems
    assert bems is not None
    device = replace(bems.devices[0], enabled=True, initial_soc_percent=50, max_output_power_w=200)
    settings = replace(
        bems,
        devices=[device],
        control=replace(bems.control, deadband_w=10, max_output_power_w=200, simulation_ramp_seconds=10),
    )
    start = datetime(2026, 1, 15, 12, tzinfo=UTC)
    grid = settings.inputs.grid
    household = settings.inputs.household
    pv = settings.inputs.pv
    points = []
    for sample_number in range(13):
        timestamp = start + timedelta(seconds=sample_number * 10.01)
        points.extend(
            [
                Measurement(timestamp, grid.source, grid.metric, 200, "W"),
                Measurement(timestamp, household.source, household.metric, 200, "W"),
                Measurement(timestamp, pv.source, pv.metric, 0, "W"),
            ]
        )
    database = HistoryMemoryDatabase(points)

    run_id = asyncio.run(
        simulate_historical(
            start=start,
            stop=start + timedelta(seconds=130.2),
            config=settings,
            history_database=database,
            output_dir=tmp_path,
            device_ids={device.id},
            run_id="continuous-discharge",
        )
    )

    timeseries_path = tmp_path / run_id / f"timeseries_{device.id}.csv"
    with timeseries_path.open(encoding="utf-8", newline="") as file:
        timeseries = list(csv.DictReader(file))

    assert len(timeseries) == 13
    assert (datetime.fromisoformat(timeseries[-1]["timestamp"]) - datetime.fromisoformat(timeseries[0]["timestamp"])).total_seconds() >= 120
    assert all(float(row["requested_power_w"]) == -190 for row in timeseries)
    assert float(timeseries[0]["actual_power_w"]) == 0
    assert all(float(row["actual_power_w"]) == -190 for row in timeseries[1:])
    assert all(float(row["soc_percent"]) < 50 for row in timeseries[1:])
    assert database.store_calls == 0
