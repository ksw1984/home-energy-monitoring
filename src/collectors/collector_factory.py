from src.collectors.base_collector import BaseCollector
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


def create_collectors(config):
    collectors: list[BaseCollector] = []

    for collector_config in config.collectors:
        if not collector_config.enabled:
            continue

        attributes = collector_config.attributes
        timezone = config.collection.timezone

        if collector_config.type == "fronius":
            collectors.append(FroniusSymoInverterCollector(timezone=timezone, **attributes))

        elif collector_config.type == "iec":
            collectors.append(IecCollector(timezone=timezone, **attributes))

        elif collector_config.type == "environment":
            collectors.append(RademacherEnvironmentSensorCollector(timezone=timezone, **attributes))

        elif collector_config.type == "weather":
            collectors.append(OpenMeteoWeatherCollector(timezone=timezone, **attributes))

        else:
            raise ValueError(f"Unknown collector type: {collector_config.type}")

    return collectors
