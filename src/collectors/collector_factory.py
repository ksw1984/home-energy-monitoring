from typing import TYPE_CHECKING

from src.collectors.fronius_inverter.fronius_symo_inverter_collector import (
    FroniusSymoInverterCollector,
)
from src.collectors.iec.iec_collector import IecCollector
from src.collectors.rademacher.umweltsensor_9475_collector import (
    RademacherEnvironmentSensorCollector,
)
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
            collectors.append(FroniusSymoInverterCollector(**common_kwargs))

        elif collector_config.type == "iec":
            collectors.append(IecCollector(**common_kwargs))

        elif collector_config.type == "environment":
            collectors.append(RademacherEnvironmentSensorCollector(**common_kwargs))

        elif collector_config.type == "weather":
            collectors.append(OpenMeteoWeatherCollector(**common_kwargs))

        else:
            raise ValueError(f"Unknown collector type: {collector_config.type}")

    return collectors
