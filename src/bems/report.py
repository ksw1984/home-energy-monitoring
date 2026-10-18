"""Local CSV and self-contained HTML reports for historical simulations."""

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from src.collectors.definitions.common.measurement import Measurement

TIMESERIES_FIELDS = (
    "timestamp",
    "grid_reference_w",
    "household_w",
    "heat_pump_w",
    "pv_w",
    "requested_power_w",
    "actual_power_w",
    "soc_percent",
    "energy_stored_wh",
    "grid_with_battery_w",
    "grid_import_saved_kwh_interval",
    "savings_cents_interval",
    "savings_total_cents",
)


@dataclass(frozen=True)
class PreviousPoint:
    """Fields from the preceding sample needed for interval integration."""

    timestamp: datetime
    grid_reference_w: float
    actual_power_w: float
    savings_total_cents: float


def append_measurements(path: Path, measurements: list[Measurement]) -> None:
    """Append a bounded simulation chunk to its local JSONL result file."""
    if not measurements:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        for item in measurements:
            file.write(
                json.dumps(
                    {
                        "timestamp": item.timestamp.isoformat(),
                        "source": item.source,
                        "metric": item.metric,
                        "value": item.value,
                        "unit": item.unit,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def build_device_report(  # noqa: PLR0913 - report generation needs explicit paths, identity, clock range and aggregation settings.
    *,
    measurement_path: Path,
    report_dir: Path,
    device_id: str,
    timezone: str,
    chart_start: datetime,
    chart_stop: datetime,
    max_interval_seconds: float,
) -> tuple[Path, Path, Path, Path]:
    """Create a detailed local CSV, daily/weekly summaries and an SVG HTML report."""
    points_path = report_dir / f"timeseries_{device_id}.csv"
    daily_path = report_dir / f"daily_{device_id}.csv"
    weekly_path = report_dir / f"weekly_{device_id}.csv"
    zone = ZoneInfo(timezone)
    daily: dict[str, dict[str, float]] = defaultdict(_empty_summary)
    weekly: dict[str, dict[str, float]] = defaultdict(_empty_summary)
    chart: list[dict[str, float | str]] = []
    chart_bucket_seconds = max(300.0, (chart_stop - chart_start).total_seconds() / 1000)
    next_chart_at = chart_start.timestamp()
    previous: PreviousPoint | None = None

    with measurement_path.open(encoding="utf-8") as source, points_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=TIMESERIES_FIELDS)
        writer.writeheader()
        records = (json.loads(line) for line in source)
        for timestamp_text, group in groupby(records, key=lambda row: row["timestamp"]):
            values = {item["metric"]: float(item["value"]) for item in group}
            timestamp = datetime.fromisoformat(timestamp_text)
            row = _timeseries_row(timestamp, values, previous, max_interval_seconds)
            writer.writerow(row)
            if previous is not None:
                duration = (timestamp - previous.timestamp).total_seconds()
                if 0 < duration <= max_interval_seconds:
                    local_day = previous.timestamp.astimezone(zone).date().isoformat()
                    iso = previous.timestamp.astimezone(zone).isocalendar()
                    week_key = f"{iso.year}-W{iso.week:02d}"
                    saved = cast("float", row["grid_import_saved_kwh_interval"])
                    saved_cents = cast("float", row["savings_cents_interval"])
                    baseline_kwh = max(0.0, previous.grid_reference_w) * duration / 3_600_000
                    with_battery_kwh = baseline_kwh - saved
                    _add_summary(daily[local_day], baseline_kwh, with_battery_kwh, saved, saved_cents)
                    _add_summary(weekly[week_key], baseline_kwh, with_battery_kwh, saved, saved_cents)
            if timestamp.timestamp() >= next_chart_at:
                chart.append(_chart_row(timestamp, values))
                next_chart_at = timestamp.timestamp() + chart_bucket_seconds
            previous = _previous_state(timestamp, values)

    _write_summary(daily_path, daily, "date")
    _write_summary(weekly_path, weekly, "week")
    html_path = report_dir / f"report_{device_id}.html"
    html_path.write_text(_render_html(device_id, chart, daily, weekly), encoding="utf-8")
    return points_path, daily_path, weekly_path, html_path


def _timeseries_row(
    timestamp: datetime,
    values: dict[str, float],
    previous: PreviousPoint | None,
    max_interval_seconds: float,
) -> dict[str, object]:
    """Map normalized simulation metrics into a single timestamp row."""
    saved_kwh = 0.0
    savings_cents = 0.0
    if previous is not None:
        duration = (timestamp - previous.timestamp).total_seconds()
        if 0 < duration <= max_interval_seconds:
            saved_kwh = _grid_import_saved_kwh(
                previous.grid_reference_w,
                previous.actual_power_w,
                values["actual_power"],
                duration,
            )
            savings_cents = values["savings_total"] - previous.savings_total_cents
    return {
        "timestamp": timestamp.isoformat(),
        "grid_reference_w": values.get("grid_power_reference", 0.0),
        "household_w": values.get("household_power_reference", 0.0),
        "heat_pump_w": values.get("heat_pump_power_reference", 0.0),
        "pv_w": values.get("pv_power_reference", 0.0),
        "requested_power_w": values.get("requested_power", 0.0),
        "actual_power_w": values.get("actual_power", 0.0),
        "soc_percent": values.get("soc", 0.0),
        "energy_stored_wh": values.get("energy_stored", 0.0),
        "grid_with_battery_w": values.get("grid_power_with_battery", 0.0),
        "grid_import_saved_kwh_interval": saved_kwh,
        "savings_cents_interval": savings_cents,
        "savings_total_cents": values.get("savings_total", 0.0),
    }


def _grid_import_saved_kwh(grid_w: float, power_start_w: float, power_end_w: float, duration_s: float) -> float:
    """Integrate the change in grid import for a linearly changing battery output."""
    baseline_wh = max(0.0, grid_w) * duration_s / 3600
    start = grid_w + power_start_w
    end = grid_w + power_end_w
    if start >= 0 and end >= 0:
        battery_grid_wh = (start + end) / 2 * duration_s / 3600
    elif start <= 0 and end <= 0:
        battery_grid_wh = 0.0
    elif start > 0:
        fraction = start / (start - end)
        battery_grid_wh = start * duration_s * fraction / 7200
    else:
        fraction = end / (end - start)
        battery_grid_wh = end * duration_s * fraction / 7200
    return (baseline_wh - battery_grid_wh) / 1000


def _previous_state(timestamp: datetime, values: dict[str, float]) -> PreviousPoint:
    return PreviousPoint(
        timestamp=timestamp,
        grid_reference_w=values.get("grid_power_reference", 0.0),
        actual_power_w=values.get("actual_power", 0.0),
        savings_total_cents=values.get("savings_total", 0.0),
    )


def _chart_row(timestamp: datetime, values: dict[str, float]) -> dict[str, float | str]:
    return {
        "timestamp": timestamp.isoformat(),
        "grid": values.get("grid_power_reference", 0.0),
        "grid_after": values.get("grid_power_with_battery", 0.0),
        "household": values.get("household_power_reference", 0.0),
        "heat_pump": values.get("heat_pump_power_reference", 0.0),
        "pv": values.get("pv_power_reference", 0.0),
        "battery": values.get("actual_power", 0.0),
        "soc": values.get("soc", 0.0),
    }


def _empty_summary() -> dict[str, float]:
    return {"reference_kwh": 0.0, "battery_kwh": 0.0, "saved_kwh": 0.0, "saved_cents": 0.0}


def _add_summary(summary: dict[str, float], baseline: float, with_battery: float, saved: float, cents: float) -> None:
    summary["reference_kwh"] += baseline
    summary["battery_kwh"] += with_battery
    summary["saved_kwh"] += saved
    summary["saved_cents"] += cents


def _write_summary(path: Path, summary: dict[str, dict[str, float]], period_name: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[period_name, "grid_reference_kwh", "grid_with_battery_kwh", "grid_import_saved_kwh", "cost_saved_eur"],
        )
        writer.writeheader()
        for period, values in sorted(summary.items()):
            writer.writerow(
                {
                    period_name: period,
                    "grid_reference_kwh": f"{values['reference_kwh']:.6f}",
                    "grid_with_battery_kwh": f"{values['battery_kwh']:.6f}",
                    "grid_import_saved_kwh": f"{values['saved_kwh']:.6f}",
                    "cost_saved_eur": f"{values['saved_cents'] / 100:.4f}",
                }
            )


def _render_html(
    device_id: str,
    chart: list[dict[str, float | str]],
    daily: dict[str, dict[str, float]],
    weekly: dict[str, dict[str, float]],
) -> str:
    daily_rows = "".join(
        f"<tr><td>{day}</td><td>{values['saved_kwh']:.3f}</td><td>{values['saved_cents'] / 100:.2f}</td></tr>"
        for day, values in sorted(daily.items())
    )
    power_plot = _line_chart(
        chart,
        title="Measured inputs and simulated grid/battery power (W)",
        series=[
            ("grid", "Grid reference", "#374151"),
            ("grid_after", "Grid with battery", "#dc2626"),
            ("household", "Household", "#2563eb"),
            ("heat_pump", "Heat pump", "#9333ea"),
            ("pv", "PV", "#16a34a"),
            ("battery", "Battery AC power", "#ea580c"),
        ],
    )
    soc_plot = _line_chart(chart, title="Simulated battery state of charge (%)", series=[("soc", "SOC", "#0891b2")])
    daily_plot = _bar_chart(daily)
    weekly_rows = "".join(
        f"<tr><td>{week}</td><td>{values['saved_kwh']:.3f}</td><td>{values['saved_cents'] / 100:.2f}</td></tr>"
        for week, values in sorted(weekly.items())
    )
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>BEMS report: {device_id}</title><style>
body{{font:15px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#172033}}
svg{{width:100%;height:auto;border:1px solid #ddd}}
table{{border-collapse:collapse;width:100%}}
th,td{{padding:.45rem;text-align:right;border-bottom:1px solid #ddd}}
th:first-child,td:first-child{{text-align:left}}
.legend span{{margin-right:1rem}}
</style>
<h1>BEMS historical simulation: {device_id}</h1>
<p>Input measurements are read from the configured database.</p>
<p>Simulation output is stored locally in CSV/JSONL files. No simulation measurements are written to a database.</p>
<h2>Daily grid import savings (kWh)</h2>{daily_plot}
<h2>Power timeline</h2>{power_plot}
<h2>Battery SOC</h2>{soc_plot}
<h2>Daily summary</h2>
<table><thead><tr><th>Date</th><th>Grid import saved (kWh)</th><th>Cost saved (€)</th></tr></thead>
<tbody>{daily_rows}</tbody></table>
<h2>Weekly summary</h2>
<table><thead><tr><th>ISO week</th><th>Grid import saved (kWh)</th><th>Cost saved (€)</th></tr></thead>
<tbody>{weekly_rows}</tbody></table>
<p>Detailed rows: <code>timeseries_{device_id}.csv</code>. Daily and weekly CSV summaries are saved beside this report.</p></html>"""


def _line_chart(data: list[dict[str, float | str]], *, title: str, series: list[tuple[str, str, str]]) -> str:
    """Render a compact inline SVG line chart without external libraries."""
    width, height, margin = 1000, 300, 45
    all_values = [float(row[key]) for row in data for key, _, _ in series]
    if not data or not all_values:
        return "<p>No points available.</p>"
    minimum, maximum = min(all_values), max(all_values)
    span = maximum - minimum or 1.0
    lines = []
    for key, label, color in series:
        coords = []
        for index, row in enumerate(data):
            x = margin + index * (width - 2 * margin) / max(1, len(data) - 1)
            y = height - margin - (float(row[key]) - minimum) / span * (height - 2 * margin)
            coords.append(f"{x:.1f},{y:.1f}")
        lines.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{" ".join(coords)}"/>'
            f'<text x="{margin + len(lines) * 150}" y="18" fill="{color}">{label}</text>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{title}">'
        f'<text x="{margin}" y="18">{title}</text>'
        f'<line x1="{margin}" y1="{height - margin}" x2="{width - margin}" y2="{height - margin}" stroke="#999"/>'
        f"{''.join(lines)}</svg>"
    )


def _bar_chart(daily: dict[str, dict[str, float]]) -> str:
    """Render daily kWh savings as an inline SVG bar chart."""
    if not daily:
        return "<p>No daily intervals available.</p>"
    data = sorted(daily.items())
    width, height, margin = 1000, 240, 50
    max_value = max((abs(row["saved_kwh"]) for _, row in data), default=0.0) or 1.0
    bar_width = max(2.0, (width - 2 * margin) / len(data) * 0.7)
    bars = []
    has_negative = any(row["saved_kwh"] < 0 for _, row in data)
    available_height = (height - 2 * margin) / 2 if has_negative else height - 2 * margin
    zero_y = margin + available_height if has_negative else height - margin
    for index, (day, row) in enumerate(data):
        x = margin + index * (width - 2 * margin) / len(data)
        value = row["saved_kwh"]
        bar_height = abs(value) / max_value * available_height
        y = zero_y - bar_height if value >= 0 else zero_y
        color = "#16a34a" if value >= 0 else "#dc2626"
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="{color}">'
            f"<title>{day}: {value:.3f} kWh</title></rect>"
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Daily grid import saved in kWh">'
        f'<line x1="{margin}" y1="{zero_y}" x2="{width - margin}" y2="{zero_y}" stroke="#999"/>'
        f"{''.join(bars)}</svg>"
    )
