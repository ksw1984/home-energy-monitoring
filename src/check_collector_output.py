import logging

from src.collectors.fronius_inverter.fronius_symo_inverter_collector import (
    FroniusSymoInverterCollector,
)
from src.collectors.rademacher.umweltsensor_9475_collector import (
    RademacherEnvironmentSensorCollector,
)
from src.collectors.weather_forecast.open_meteo_weather_collector import (
    OpenMeteoWeatherCollector,
)
from src.config import config_obj

logger = logging.getLogger(__name__)


def log_measurements(name, measurements):
    logger.info("")
    logger.info("=" * 70)
    logger.info(name)
    logger.info("=" * 70)

    if not measurements:
        logger.info("No measurements returned.")
        return

    for measurement in measurements:
        logger.info(
            f"{measurement.timestamp.isoformat()} "
            f"{measurement.source:10} "
            f"{measurement.metric:30} "
            f"{measurement.value:<12.3f} "
            f"{measurement.unit:8} "
            f"{measurement.measurement_type}"
        )


def check_collectors():
    fronius = FroniusSymoInverterCollector(
        inverter_ip=config_obj.fronius_ip,
        pv_latitude=config_obj.latitude,
        pv_longitude=config_obj.longitude,
    )

    rademacher = RademacherEnvironmentSensorCollector(
        smart_home_box_ip=config_obj.smart_home_box_ip,
        device_id=config_obj.umweltsensor_9475_device_id,
    )

    weather = OpenMeteoWeatherCollector(
        latitude=config_obj.latitude,
        longitude=config_obj.longitude,
    )

    collectors = [
        fronius,
        rademacher,
        weather,
    ]

    try:
        for collector in collectors:
            logger.info(f"\nCollecting from {collector.__class__.__name__}...")

            measurements = collector.collect()

            log_measurements(
                collector.__class__.__name__,
                measurements,
            )

            assert isinstance(measurements, list)

    finally:

        for collector in collectors:

            if hasattr(collector, "disconnect"):
                logger.info(f"\nDisconnecting " f"{collector.__class__.__name__}...")

                collector.disconnect()


if __name__ == "__main__":
    check_collectors()
