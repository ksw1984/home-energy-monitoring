"""On-demand backfill and inspection for the measured household night baseline."""

import argparse
import asyncio
import logging
from datetime import date, datetime, timedelta, UTC
from zoneinfo import ZoneInfo

from src.bems.core.night_baseline import last_completed_night, night_window_for_evening
from src.bems.services.night_baseline_service import NightBaselineProvider
from src.config.config import config_obj
from src.databases.database_factory import create_databases
from src.logger.logging_config import setup_logging

logger = logging.getLogger(__name__)


def _parse_date(value: str) -> date:
    """Parse an ISO local date such as ``2026-11-18``."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO date: {value}") from exc


async def _run(args: argparse.Namespace) -> None:
    """Backfill completed nights and/or show the selected baseline."""
    bems = config_obj.bems
    if bems is None:
        raise RuntimeError("BEMS configuration was not loaded")
    if not bems.night.baseline_estimation.enabled:
        raise RuntimeError("BEMS night.baseline_estimation.enabled is false")

    databases = create_databases(config_obj)
    try:
        history_database = next(
            (database for database in databases if database.enabled and database.type == bems.history_database),
            None,
        )
        if history_database is None:
            raise RuntimeError(f"Configured BEMS history database '{bems.history_database}' is not enabled")
        history_database.connect()

        provider = NightBaselineProvider(bems, history_database)
        schedule = {
            "latitude": bems.night.latitude,
            "longitude": bems.night.longitude,
            "timezone": bems.night.timezone,
            "dusk_margin_minutes": bems.night.dusk_margin_minutes,
            "dawn_margin_minutes": bems.night.dawn_margin_minutes,
        }
        now = datetime.now(UTC)

        if args.backfill_days:
            local_today = now.astimezone(ZoneInfo(bems.night.timezone)).date()
            for days_back in range(args.backfill_days, 0, -1):
                evening = local_today - timedelta(days=days_back)
                window = night_window_for_evening(evening, **schedule)
                if window.end > now:
                    print(f"skip {evening}: night has not ended yet")
                    continue
                # Historical household references are gross. The configured
                # household_meter_includes_battery flag describes the live bus
                # meter and would make old nights invalid without live battery data.
                night = await provider.recalculate_night(evening, meter_includes_battery=False)
                await provider.store_night(night)
                print(
                    f"stored {evening}: {night.energy_wh:.1f} Wh / {night.duration_h:.2f} h "
                    f"= {night.avg_power_w:.1f} W (valid={night.valid}, battery_adjusted={night.battery_adjusted})"
                )

        if args.show:
            target = args.night_date or last_completed_night(now, **schedule)
            window = night_window_for_evening(target, **schedule)
            timestamp = window.start + timedelta(hours=1)
            selected_target, selected_nights, power_w = await provider.selection_for(timestamp)
            print(f"night {selected_target}: baseline={power_w if power_w is not None else 'fallback'}")
            for night in selected_nights:
                print(f"  {night.night_date} {night.weekday}: {night.avg_power_w:.1f} W over {night.duration_h:.2f} h")
    finally:
        for database in databases:
            database.close()


def main() -> None:
    """Parse CLI arguments and run one baseline maintenance process."""
    parser = argparse.ArgumentParser(description="Backfill or inspect the measured BEMS household night baseline")
    parser.add_argument("--backfill-days", type=int, default=0, metavar="N", help="recalculate the last N completed nights")
    parser.add_argument("--show", action="store_true", help="print the baseline selected for one night")
    parser.add_argument("--night-date", type=_parse_date, help="local evening date to inspect; defaults to the last completed night")
    args = parser.parse_args()
    if args.backfill_days < 0:
        parser.error("--backfill-days must not be negative")
    if args.night_date is not None:
        args.show = True
    if not args.backfill_days and not args.show:
        parser.error("use --backfill-days N or --show")

    setup_logging()
    asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover
    main()
