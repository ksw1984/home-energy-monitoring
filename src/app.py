import asyncio


from src.database.influxdb import InfluxDatabase
from src.collectors.iec.iec_collector import IecCollector
from src.collectors.fronius_inverter.fronius_symo_inverter_collector import (
    FroniusSymoInverterCollector,
)
from src.collectors.rademacher.umweltsensor_9475_collector import (
    RademacherEnvironmentSensorCollector,
)
from src.manager.collector_manager import CollectorManager
from src.config import config_obj

#
#  poetry run python -m app
#


async def run():
    print("App.run()")
    _meter_iec = IecCollector(
        port=config_obj.ir_device0,
    )

    fronius_inverter = FroniusSymoInverterCollector(
        inverter_ip=config_obj.fronius_ip,
    )

    env_sensor = RademacherEnvironmentSensorCollector(
        smart_home_box_ip=config_obj.smart_home_box_ip,
        device_id=config_obj.umweltsensor_9475_device_id,
    )

    database = InfluxDatabase(
        url=config_obj.influxdb_url,
        token=config_obj.influxdb_token,
        org=config_obj.influxdb_org,
        bucket=config_obj.influxdb_bucket,
    )

    manager = CollectorManager(
        collectors=[
            # meter_iec,
            fronius_inverter,
            env_sensor,
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


if __name__ == "__main__":
    main()
