"""Local CSV and self-contained HTML reports for historical simulations."""

import csv
import html
import json
import shutil
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby, pairwise
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from src.collectors.definitions.common.measurement import Measurement
from src.config.config import BemsTariffConfig

import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

TIMESERIES_FIELDS = (
    "timestamp",
    "grid_reference_w",
    "meter_grid_without_battery_w",
    "household_w",
    "meter_household_without_battery_w",
    "heat_pump_w",
    "pv_w",
    "requested_power_w",
    "actual_power_w",
    "soc_percent",
    "energy_stored_wh",
    "grid_with_battery_w",
    "grid_import_saved_kwh_interval",
    "household_import_saved_kwh_interval",
    "heat_pump_import_saved_kwh_interval",
    "savings_cents_interval",
    "household_cost_saved_cents_interval",
    "heat_pump_cost_saved_cents_interval",
    "feed_in_cost_impact_cents_interval",
    "household_reference_cost_cents_interval",
    "heat_pump_reference_cost_cents_interval",
    "feed_in_reference_revenue_cents_interval",
    "savings_total_cents",
)


@dataclass(frozen=True)
class PreviousPoint:
    """Fields from the preceding sample needed for interval integration."""

    timestamp: datetime
    grid_reference_w: float
    household_reference_w: float
    heat_pump_reference_w: float
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
    capacity_wh: float,
    tariffs: BemsTariffConfig,
    start_soc_percent: float,
    configured_max_power_w: float,
    effective_max_power_w: float,
) -> tuple[Path, Path, Path, Path]:
    """Create local CSV summaries and an interactive report for one device."""
    points_path = report_dir / f"timeseries_{device_id}.csv"
    daily_path = report_dir / f"daily_{device_id}.csv"
    weekly_path = report_dir / f"weekly_{device_id}.csv"
    zone = ZoneInfo(timezone)
    daily: dict[str, dict[str, float]] = defaultdict(_empty_summary)
    weekly: dict[str, dict[str, float]] = defaultdict(_empty_summary)
    chart: list[dict[str, float | str]] = []
    previous: PreviousPoint | None = None

    with measurement_path.open(encoding="utf-8") as source, points_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=TIMESERIES_FIELDS)
        writer.writeheader()
        records = (json.loads(line) for line in source)
        for timestamp_text, group in groupby(records, key=lambda row: row["timestamp"]):
            values = {item["metric"]: float(item["value"]) for item in group}
            timestamp = datetime.fromisoformat(timestamp_text)
            row = _timeseries_row(timestamp, values, previous, max_interval_seconds, tariffs)
            writer.writerow(row)
            if previous is not None:
                duration = (timestamp - previous.timestamp).total_seconds()
                if 0 < duration <= max_interval_seconds:
                    local_day = previous.timestamp.astimezone(zone).date().isoformat()
                    iso = previous.timestamp.astimezone(zone).isocalendar()
                    week_key = f"{iso.year}-W{iso.week:02d}"
                    saved = cast("float", row["grid_import_saved_kwh_interval"])
                    saved_cents = cast("float", row["savings_cents_interval"])
                    household_saved = cast("float", row["household_import_saved_kwh_interval"])
                    hp_saved = cast("float", row["heat_pump_import_saved_kwh_interval"])
                    household_cents = cast("float", row["household_cost_saved_cents_interval"])
                    hp_cents = cast("float", row["heat_pump_cost_saved_cents_interval"])
                    feed_in_impact = cast("float", row["feed_in_cost_impact_cents_interval"])
                    household_reference_cents = cast("float", row["household_reference_cost_cents_interval"])
                    hp_reference_cents = cast("float", row["heat_pump_reference_cost_cents_interval"])
                    feed_in_reference_cents = cast("float", row["feed_in_reference_revenue_cents_interval"])
                    baseline_kwh = max(0.0, previous.grid_reference_w) * duration / 3_600_000
                    with_battery_kwh = baseline_kwh - saved
                    summary_delta = {
                        "reference_kwh": baseline_kwh,
                        "battery_kwh": with_battery_kwh,
                        "saved_kwh": saved,
                        "saved_cents": saved_cents,
                        "household_saved_kwh": household_saved,
                        "heat_pump_saved_kwh": hp_saved,
                        "household_saved_cents": household_cents,
                        "heat_pump_saved_cents": hp_cents,
                        "feed_in_cost_impact_cents": feed_in_impact,
                        "household_reference_cents": household_reference_cents,
                        "heat_pump_reference_cents": hp_reference_cents,
                        "feed_in_reference_cents": feed_in_reference_cents,
                    }
                    _add_summary(daily[local_day], summary_delta)
                    _add_summary(weekly[week_key], summary_delta)
            # Keep every aligned sample in the requested range. A local static
            # report cannot request more points when the reader zooms in.
            if chart_start <= timestamp < chart_stop:
                chart.append(_chart_row(timestamp, values))
            previous = _previous_state(timestamp, values)

    _write_summary(daily_path, daily, "date")
    _write_summary(weekly_path, weekly, "week")
    plotly_bundle = report_dir / "plotly.min.js"
    if not plotly_bundle.exists():
        package_bundle = Path(go.__file__).parent.parent / "package_data" / "plotly.min.js"
        shutil.copyfile(package_bundle, plotly_bundle)
    html_path = report_dir / f"report_{device_id}.html"
    html_path.write_text(
        _render_html(
            device_id,
            chart,
            daily,
            weekly,
            tariffs,
            start_soc_percent,
            configured_max_power_w,
            effective_max_power_w,
            timezone,
            capacity_wh,
        ),
        encoding="utf-8",
    )
    return points_path, daily_path, weekly_path, html_path


def _timeseries_row(
    timestamp: datetime,
    values: dict[str, float],
    previous: PreviousPoint | None,
    max_interval_seconds: float,
    tariffs: BemsTariffConfig,
) -> dict[str, object]:
    """Map normalized simulation metrics into a single timestamp row."""
    saved_kwh = 0.0
    savings_cents = 0.0
    household_saved = hp_saved = household_cents = hp_cents = 0.0
    feed_in_impact = 0.0
    household_reference_cents = hp_reference_cents = feed_in_reference_cents = 0.0
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
            household_saved, hp_saved, household_cents, hp_cents = _branch_savings(previous, values, duration, saved_kwh, tariffs)
            feed_in_impact = savings_cents - household_cents - hp_cents
            household_reference_cents, hp_reference_cents, feed_in_reference_cents = _reference_cost_parts(previous, duration, tariffs)
    return {
        "timestamp": timestamp.isoformat(),
        "grid_reference_w": values.get("grid_power_reference", 0.0),
        "meter_grid_without_battery_w": values.get("grid_power_reference", 0.0),
        "household_w": values.get("household_power_reference", 0.0),
        "meter_household_without_battery_w": values.get("household_power_reference", 0.0),
        "heat_pump_w": values.get("heat_pump_power_reference", 0.0),
        "pv_w": values.get("pv_power_reference", 0.0),
        "requested_power_w": values.get("requested_power", 0.0),
        "actual_power_w": values.get("actual_power", 0.0),
        "soc_percent": values.get("soc", 0.0),
        "energy_stored_wh": values.get("energy_stored", 0.0),
        "grid_with_battery_w": values.get("grid_power_with_battery", 0.0),
        "grid_import_saved_kwh_interval": saved_kwh,
        "household_import_saved_kwh_interval": household_saved,
        "heat_pump_import_saved_kwh_interval": hp_saved,
        "savings_cents_interval": savings_cents,
        "household_cost_saved_cents_interval": household_cents,
        "heat_pump_cost_saved_cents_interval": hp_cents,
        "feed_in_cost_impact_cents_interval": feed_in_impact,
        "household_reference_cost_cents_interval": household_reference_cents,
        "heat_pump_reference_cost_cents_interval": hp_reference_cents,
        "feed_in_reference_revenue_cents_interval": feed_in_reference_cents,
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


def _branch_savings(
    previous: PreviousPoint,
    values: dict[str, float],
    duration_s: float,
    grid_saved_kwh: float,
    tariffs: BemsTariffConfig,
) -> tuple[float, float, float, float]:
    """Split measured grid import reduction into household and heat-pump parts."""
    start_power = previous.actual_power_w
    end_power = values.get("actual_power", 0.0)
    delta = end_power - start_power
    household = previous.household_reference_w
    fractions = [0.0, 1.0]
    if delta:
        for threshold in (-household,):
            fraction = (threshold - start_power) / delta
            if 0 < fraction < 1:
                fractions.append(fraction)
    fractions.sort()
    household_with_wh = 0.0
    for left, right in pairwise(fractions):
        p_left = start_power + delta * left
        p_right = start_power + delta * right
        span_seconds = duration_s * (right - left)
        hh_left, hh_right = max(0.0, household + p_left), max(0.0, household + p_right)
        household_with_wh += (hh_left + hh_right) * span_seconds / 7200
    hh_reference_wh = max(0.0, household) * duration_s / 3600
    hh_saved = (hh_reference_wh - household_with_wh) / 1000
    # The cascade meters define the heat-pump branch as grid minus household.
    # Assign the remaining measured grid import reduction to that branch so
    # the two branch totals always reconcile to the grid meter.
    hp_saved = grid_saved_kwh - hh_saved
    return hh_saved, hp_saved, hh_saved * tariffs.household_import_ct_per_kwh, hp_saved * tariffs.heat_pump_import_ct_per_kwh


def _reference_cost_parts(previous: PreviousPoint, duration_s: float, tariffs: BemsTariffConfig) -> tuple[float, float, float]:
    """Calculate no-battery household cost, heat-pump cost and feed-in credit."""
    household_import_w = max(0.0, previous.household_reference_w)
    heat_pump_w = max(0.0, previous.heat_pump_reference_w)
    household_export_w = max(0.0, -previous.household_reference_w)
    heat_pump_import_w = max(0.0, heat_pump_w - min(household_export_w, heat_pump_w))
    grid_export_w = max(0.0, -previous.grid_reference_w)
    interval_kwh = duration_s / 3_600_000
    return (
        household_import_w * interval_kwh * tariffs.household_import_ct_per_kwh,
        heat_pump_import_w * interval_kwh * tariffs.heat_pump_import_ct_per_kwh,
        grid_export_w * interval_kwh * tariffs.feed_in_ct_per_kwh,
    )


def _previous_state(timestamp: datetime, values: dict[str, float]) -> PreviousPoint:
    return PreviousPoint(
        timestamp=timestamp,
        grid_reference_w=values.get("grid_power_reference", 0.0),
        household_reference_w=values.get("household_power_reference", 0.0),
        heat_pump_reference_w=values.get("heat_pump_power_reference", 0.0),
        actual_power_w=values.get("actual_power", 0.0),
        savings_total_cents=values.get("savings_total", 0.0),
    )


def _chart_row(timestamp: datetime, values: dict[str, float]) -> dict[str, float | str]:
    """Map normalized controller signs to the physical signs shown in reports."""
    return {
        "timestamp": timestamp.isoformat(),
        "meter_grid": values.get("grid_power_reference", 0.0),
        "grid_after": values.get("grid_power_with_battery", 0.0),
        "meter_household": values.get("household_power_reference", 0.0),
        "heat_pump": values.get("heat_pump_power_reference", 0.0),
        "pv": values.get("pv_power_reference", 0.0),
        # Match the BEMS convention: positive AC power charges the battery;
        # negative AC power discharges it.
        "battery": values.get("actual_power", 0.0),
        "soc": values.get("soc", 0.0),
        "energy_kwh": values.get("energy_stored", 0.0) / 1000.0,
    }


def _empty_summary() -> dict[str, float]:
    return {
        "reference_kwh": 0.0,
        "battery_kwh": 0.0,
        "saved_kwh": 0.0,
        "saved_cents": 0.0,
        "household_saved_kwh": 0.0,
        "heat_pump_saved_kwh": 0.0,
        "household_saved_cents": 0.0,
        "heat_pump_saved_cents": 0.0,
        "feed_in_cost_impact_cents": 0.0,
        "household_reference_cents": 0.0,
        "heat_pump_reference_cents": 0.0,
        "feed_in_reference_cents": 0.0,
    }


def _add_summary(summary: dict[str, float], delta: dict[str, float]) -> None:
    for field, value in delta.items():
        summary[field] += value


def _write_summary(path: Path, summary: dict[str, dict[str, float]], period_name: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                period_name,
                "grid_reference_kwh",
                "grid_with_battery_kwh",
                "grid_import_saved_kwh",
                "household_import_saved_kwh",
                "heat_pump_import_saved_kwh",
                "household_reference_cost_eur",
                "heat_pump_reference_cost_eur",
                "feed_in_reference_revenue_eur",
                "cost_saved_eur",
                "household_cost_saved_eur",
                "heat_pump_cost_saved_eur",
                "feed_in_cost_impact_eur",
            ],
        )
        writer.writeheader()
        for period, values in sorted(summary.items()):
            writer.writerow(
                {
                    period_name: period,
                    "grid_reference_kwh": f"{values['reference_kwh']:.6f}",
                    "grid_with_battery_kwh": f"{values['battery_kwh']:.6f}",
                    "grid_import_saved_kwh": f"{values['saved_kwh']:.6f}",
                    "household_import_saved_kwh": f"{values['household_saved_kwh']:.6f}",
                    "heat_pump_import_saved_kwh": f"{values['heat_pump_saved_kwh']:.6f}",
                    "household_reference_cost_eur": f"{values['household_reference_cents'] / 100:.4f}",
                    "heat_pump_reference_cost_eur": f"{values['heat_pump_reference_cents'] / 100:.4f}",
                    "feed_in_reference_revenue_eur": f"{values['feed_in_reference_cents'] / 100:.4f}",
                    "cost_saved_eur": f"{values['saved_cents'] / 100:.4f}",
                    "household_cost_saved_eur": f"{values['household_saved_cents'] / 100:.4f}",
                    "heat_pump_cost_saved_eur": f"{values['heat_pump_saved_cents'] / 100:.4f}",
                    "feed_in_cost_impact_eur": f"{values['feed_in_cost_impact_cents'] / 100:.4f}",
                }
            )


def _render_html(  # noqa: PLR0913, PLR0917 - explicit report metadata keeps rendering independent of config loading.
    device_id: str,
    chart: list[dict[str, float | str]],
    daily: dict[str, dict[str, float]],
    weekly: dict[str, dict[str, float]],
    tariffs: BemsTariffConfig,
    start_soc_percent: float,
    configured_max_power_w: float,
    effective_max_power_w: float,
    timezone: str,
    capacity_wh: float,
) -> str:
    daily_items = sorted(daily.items())
    weekly_items = sorted(weekly.items())
    daily_rows = (
        "".join(
            f"<tr><td>{day}</td><td>{values['reference_kwh']:.2f}</td><td>{values['battery_kwh']:.2f}</td>"
            f"<td>{values['saved_kwh']:.3f}</td><td>{values['household_saved_kwh']:.3f}</td><td>{values['heat_pump_saved_kwh']:.3f}</td>"
            f"<td>{values['saved_cents'] / 100:.2f}</td>"
            f"<td>{values['household_saved_cents'] / 100:.2f}</td>"
            f"<td>{values['heat_pump_saved_cents'] / 100:.2f}</td>"
            f"<td>{values['feed_in_cost_impact_cents'] / 100:.2f}</td></tr>"
            for day, values in daily_items
        )
        or '<tr><td colspan="10">No complete measurement intervals found</td></tr>'
    )
    weekly_rows = (
        "".join(
            f"<tr><td>{week}</td><td>{values['reference_kwh']:.2f}</td><td>{values['battery_kwh']:.2f}</td>"
            f"<td>{values['saved_kwh']:.3f}</td><td>{values['household_saved_kwh']:.3f}</td><td>{values['heat_pump_saved_kwh']:.3f}</td>"
            f"<td>{values['saved_cents'] / 100:.2f}</td>"
            f"<td>{values['household_saved_cents'] / 100:.2f}</td>"
            f"<td>{values['heat_pump_saved_cents'] / 100:.2f}</td>"
            f"<td>{values['feed_in_cost_impact_cents'] / 100:.2f}</td></tr>"
            for week, values in weekly_items
        )
        or '<tr><td colspan="10">No complete measurement intervals found</td></tr>'
    )

    zone = ZoneInfo(timezone)
    times = [datetime.fromisoformat(str(row["timestamp"])).astimezone(zone).replace(tzinfo=None) for row in chart]
    power_fig = go.Figure()
    power_series = [
        ("meter_grid", "Meter grid without battery (reference)", "#475569"),
        ("grid_after", "Meter grid with battery", "#dc2626"),
        ("meter_household", "Meter household without battery (reference)", "#2563eb"),
        ("heat_pump", "Heat pump", "#9333ea"),
        ("pv", "PV generation", "#16a34a"),
        ("battery", "Battery AC (charge + / discharge -)", "#ea580c"),
    ]
    for key, label, color in power_series:
        power_fig.add_trace(
            go.Scattergl(
                x=times,
                y=[float(row[key]) for row in chart],
                name=label,
                mode="lines",
                line={"color": color, "width": 1.5},
                hovertemplate=f"%{{x|%Y-%m-%d %H:%M:%S}}<br>{label}: %{{y:,.0f}} W<extra></extra>",
            )
        )
    power_fig.update_layout(
        template="plotly_white",
        height=500,
        margin={"l": 65, "r": 25, "t": 25, "b": 50},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.12, "x": 0},
        xaxis={"title": "Time", "type": "date", "rangeslider": {"visible": True}},
        yaxis={
            "title": "Power (W); meters: import +, export -; battery: charge +, discharge -",
            "zeroline": True,
            "zerolinecolor": "#64748b",
            "gridcolor": "#e2e8f0",
        },
        uirevision="power-chart",
    )
    soc_fig = make_subplots(specs=[[{"secondary_y": True}]])
    soc_fig.add_trace(
        go.Scattergl(
            x=times,
            y=[float(row["energy_kwh"]) for row in chart],
            customdata=[float(row["soc"]) for row in chart],
            name="Battery charge",
            mode="lines",
            line={"color": "#0891b2", "width": 2},
            fill="tozeroy",
            fillcolor="rgba(8,145,178,0.12)",
            hovertemplate="%{x|%Y-%m-%d %H:%M:%S}<br>SOC: %{customdata:.1f}%<br>Stored energy: %{y:.2f} kWh<extra></extra>",
        ),
        secondary_y=True,
    )
    soc_fig.update_layout(
        template="plotly_white",
        height=380,
        margin={"l": 65, "r": 25, "t": 20, "b": 50},
        hovermode="x unified",
        showlegend=False,
        xaxis={"title": "Time", "type": "date", "rangeslider": {"visible": True}},
        uirevision="soc-chart",
    )
    soc_fig.update_yaxes(
        title_text="State of charge (%)",
        range=[0, 100],
        dtick=10,
        ticksuffix="%",
        gridcolor="#e2e8f0",
        secondary_y=False,
    )
    soc_fig.update_yaxes(
        title_text="Stored energy (kWh)",
        range=[0, capacity_wh / 1000],
        dtick=capacity_wh / 5000,
        ticksuffix=" kWh",
        gridcolor="#ffedd5",
        secondary_y=True,
    )
    savings_fig = go.Figure(
        go.Bar(
            x=[day for day, _ in daily_items],
            y=[values["saved_kwh"] for _, values in daily_items],
            marker_color=["#16a34a" if values["saved_kwh"] >= 0 else "#dc2626" for _, values in daily_items],
            text=[f"{values['saved_kwh']:.2f}" for _, values in daily_items],
            textposition="outside",
            cliponaxis=False,
            name="Grid import saved",
            customdata=[[values["saved_cents"] / 100] for _, values in daily_items],
            hovertemplate="%{x}<br>Grid import saved: %{y:.3f} kWh<br>Cost saved: €%{customdata[0]:.2f}<extra></extra>",
        )
    )
    savings_fig.update_layout(
        template="plotly_white",
        height=380,
        margin={"l": 65, "r": 25, "t": 35, "b": 70},
        showlegend=False,
        hovermode="x unified",
        xaxis={"title": "Day", "type": "date", "tickangle": -35},
        yaxis={"title": "Grid import saved (kWh)", "zeroline": True, "gridcolor": "#e2e8f0"},
        uirevision="daily-savings-chart",
    )
    plot_config = {"scrollZoom": True, "displaylogo": False, "responsive": True}
    savings_plot = (
        pio.to_html(savings_fig, full_html=False, include_plotlyjs="plotly.min.js", config=plot_config)
        if daily_items
        else "<p>No daily intervals available.</p>"
    )
    power_plot = (
        pio.to_html(power_fig, full_html=False, include_plotlyjs="plotly.min.js", config=plot_config)
        if chart
        else "<p>No time-series points available.</p>"
    )
    soc_plot = pio.to_html(soc_fig, full_html=False, include_plotlyjs=False, config=plot_config) if chart else ""
    total_saved = sum(values["saved_kwh"] for _, values in daily_items)
    total_saved_eur = sum(values["saved_cents"] for _, values in daily_items) / 100
    household_saved = sum(values["household_saved_kwh"] for _, values in daily_items)
    heat_pump_saved = sum(values["heat_pump_saved_kwh"] for _, values in daily_items)
    household_saved_eur = sum(values["household_saved_cents"] for _, values in daily_items) / 100
    heat_pump_saved_eur = sum(values["heat_pump_saved_cents"] for _, values in daily_items) / 100
    feed_in_impact_eur = sum(values["feed_in_cost_impact_cents"] for _, values in daily_items) / 100
    household_reference_eur = sum(values["household_reference_cents"] for _, values in daily_items) / 100
    heat_pump_reference_eur = sum(values["heat_pump_reference_cents"] for _, values in daily_items) / 100
    feed_in_reference_eur = sum(values["feed_in_reference_cents"] for _, values in daily_items) / 100
    reference_cost_eur = household_reference_eur + heat_pump_reference_eur - feed_in_reference_eur
    escaped_device_id = html.escape(device_id)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>BEMS report: {escaped_device_id}</title><style>
:root{{--ink:#172033;--muted:#64748b;--line:#e2e8f0;--panel:#fff;--bg:#f8fafc}}
*{{box-sizing:border-box}}
body{{font:15px/1.5 system-ui,sans-serif;max-width:1380px;margin:0 auto;padding:1.5rem;
color:var(--ink);background:var(--bg)}}
h1{{margin:.2rem 0}}h2{{margin:2rem 0 .4rem}}.subtitle{{margin:0;color:var(--muted)}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:1rem;margin:1rem 0;box-shadow:0 1px 2px #0f172a0a}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:.8rem;margin:1.2rem 0}}
.kpi{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:1rem}}
.kpi span{{display:block;color:var(--muted);font-size:.85rem}}
.kpi strong{{font-size:1.55rem}}
table{{border-collapse:collapse;width:100%;background:var(--panel)}}
th,td{{padding:.65rem .75rem;text-align:right;border-bottom:1px solid var(--line);
font-variant-numeric:tabular-nums}}
th{{position:sticky;top:0;background:#f1f5f9;color:#334155}}th:first-child,td:first-child{{text-align:left}}
@media(max-width:640px){{body{{padding:.8rem}}th,td{{padding:.45rem;font-size:.85rem}}}}
</style>
<body><header><h1>BEMS historical simulation: {escaped_device_id}</h1>
<p class="subtitle">Historical replay · Times shown in {html.escape(timezone)} ·
Start-SOC {start_soc_percent:.1f}% · configured max output {configured_max_power_w:.0f} W</p>
<p class="subtitle">Effective power limit: {effective_max_power_w:.0f} W</p>
<p class="subtitle">Tariffs: household import {tariffs.household_import_ct_per_kwh:.2f} ct/kWh ·
heat-pump import {tariffs.heat_pump_import_ct_per_kwh:.2f} ct/kWh ·
feed-in {tariffs.feed_in_ct_per_kwh:.2f} ct/kWh · {len(chart):,} plotted points · results stored locally only</p></header>
<section class="kpis"><div class="kpi"><span>Total grid import saved</span><strong>{total_saved:.2f} kWh</strong>
<small>Household {household_saved:.2f} kWh · Heat pump {heat_pump_saved:.2f} kWh</small></div>
<div class="kpi"><span>Estimated cost saved</span><strong>€{total_saved_eur:.2f}</strong>
<small>Household €{household_saved_eur:.2f} · Heat pump €{heat_pump_saved_eur:.2f} · Feed-in impact €{feed_in_impact_eur:.2f}</small></div>
<div class="kpi"><span>Net supplier cost without battery</span><strong>€{reference_cost_eur:.2f}</strong>
<small>Household €{household_reference_eur:.2f} + Heat pump €{heat_pump_reference_eur:.2f} - Feed-in credit €{feed_in_reference_eur:.2f}</small></div>
<div class="kpi"><span>Days with results</span><strong>{len(daily_items)}</strong></div></section>
<section class="card"><h2>Daily grid import savings</h2>{savings_plot}</section>
<section class="card"><h2>Power timeline</h2>
<p class="subtitle">Click legend entries to show or hide traces. Drag to zoom; use the range slider or mode bar to navigate.</p>
{power_plot}</section>
<section class="card"><h2>Battery state of charge and stored energy</h2>
<p class="subtitle">SOC percent uses the left axis; modelled stored energy uses the right axis.</p>{soc_plot}</section>
<section class="card"><h2>Daily summary</h2>
<table><thead><tr><th>Date</th><th>Grid before (kWh)</th><th>Grid with battery (kWh)</th>
<th>Grid import saved (kWh)</th><th>Household saved (kWh)</th>
<th>Heat pump saved (kWh)</th><th>Cost saved (€)</th>
<th>Household saved (€)</th><th>Heat pump saved (€)</th>
<th>Feed-in impact (€)</th></tr></thead>
<tbody>{daily_rows}</tbody></table>
<p class="subtitle">Without-battery supplier import cost by tariff:
household €{household_reference_eur:.2f}; heat pump €{heat_pump_reference_eur:.2f};
feed-in credit €{feed_in_reference_eur:.2f}.</p></section>
<section class="card"><h2>Weekly summary</h2>
<table><thead><tr><th>ISO week</th><th>Grid before (kWh)</th><th>Grid with battery (kWh)</th>
<th>Grid import saved (kWh)</th><th>Household saved (kWh)</th>
<th>Heat pump saved (kWh)</th><th>Cost saved (€)</th>
<th>Household saved (€)</th><th>Heat pump saved (€)</th>
<th>Feed-in impact (€)</th></tr></thead>
<tbody>{weekly_rows}</tbody></table>
<p class="subtitle">Without-battery supplier import costs are also included by branch in the daily and weekly CSV files.</p></section>
<p class="subtitle">Detailed rows: <code>timeseries_{escaped_device_id}.csv</code>.
Daily and weekly CSV summaries are saved beside this report.</p></body></html>"""
