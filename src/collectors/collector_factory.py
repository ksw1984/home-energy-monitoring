from typing import TYPE_CHECKING

from src.collectors.fronius_inverter.fronius_symo_inverter_collector import (
    FroniusSymoInverterCollector,
)
from src.collectors.landys_gyr_zmd310_meter.dlms.dlms_collector import DlmsCollector
from src.collectors.landys_gyr_zmd310_meter.iec.iec_collector import IecCollector
from src.collectors.rademacher.umweltsensor_9475_collector import (
    RademacherEnvironmentSensorCollector,
)
from src.collectors.shelly.shelly_gen3_collector.shelly_gen3_pm_collector import ShellyPmCollector
from src.collectors.shelly.shelly_gen3_collector.shelly_gen3_switch_collector import ShellySwitchCollector
from src.collectors.weather_forecast.open_meteo_weather_collector import (
    OpenMeteoWeatherCollector,
)

if TYPE_CHECKING:
    from src.collectors.base_collector import BaseCollector


def create_collectors(config):
    collectors: list[BaseCollector] = []

    for collector_config in config.collectors:
        attributes = collector_config.attributes
        timezone = config.collection.timezone

        interval = collector_config.interval if collector_config.interval is not None else config.collection.interval

        common_kwargs = {
            "timezone": timezone,
            "interval": interval,
            "enabled": collector_config.enabled,
            **attributes,
        }

        if collector_config.type == "fronius":
            collector = FroniusSymoInverterCollector(**common_kwargs)

        elif collector_config.type == "landys_gyr_zmd310_meter_iec":
            collector = IecCollector(**common_kwargs)

        elif collector_config.type == "landys_gyr_zmd310_meter_dlms":
            collector = DlmsCollector(**common_kwargs)

        elif collector_config.type == "environment":
            collector = RademacherEnvironmentSensorCollector(**common_kwargs)

        elif collector_config.type == "weather":
            collector = OpenMeteoWeatherCollector(**common_kwargs)

        elif collector_config.type == "shelly_pm":
            collector = ShellyPmCollector(**common_kwargs)

        elif collector_config.type == "shelly_switch":
            collector = ShellySwitchCollector(**common_kwargs)

        else:
            raise ValueError(f"Unknown collector type: {collector_config.type}")

        # Storage cadence is a manager concern, so it is attached after
        # construction instead of being threaded through every collector's
        # keyword-only signature.
        storage_interval = getattr(collector_config, "storage_interval", None)
        if isinstance(storage_interval, int) and not isinstance(storage_interval, bool) and storage_interval > 0:
            collector.storage_interval = storage_interval

        collectors.append(collector)

    return collectors
