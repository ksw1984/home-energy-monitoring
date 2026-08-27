import asyncio

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
from src.config import config_obj
from src.database.influxdb import InfluxDatabase
from src.manager.collector_manager import CollectorManager

#
#  poetry run python -m app
#


async def run():
    print("App.run()")
    _meter_grid_iec = IecCollector(
        port=config_obj.power_meter_grid_ir_device0,
    )

    _meter_household_iec = IecCollector(
        port=config_obj.power_meter_household_ir_device1,
    )

    fronius_inverter = FroniusSymoInverterCollector(
        inverter_ip=config_obj.fronius_ip,
        pv_latitude=config_obj.latitude,
        pv_longitude=config_obj.longitude,
    )

    env_sensor = RademacherEnvironmentSensorCollector(
        smart_home_box_ip=config_obj.smart_home_box_ip,
        device_id=config_obj.umweltsensor_9475_device_id,
    )

    weather = OpenMeteoWeatherCollector(
        latitude=config_obj.latitude,
        longitude=config_obj.longitude,
    )

    database = InfluxDatabase(
        url=config_obj.influxdb_url,
        token=config_obj.influxdb_token,
        org=config_obj.influxdb_org,
        bucket=config_obj.influxdb_bucket,
    )

    manager = CollectorManager(
        collectors=[
            # _meter_grid_iec,
            # _meter_household_iec,
            fronius_inverter,
            env_sensor,
            weather,
        ],
        database=database,
        interval=config_obj.collection_interval,
    )

    try:
        await manager.run()

    finally:
        database.close()


def main():
    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover
    main()
