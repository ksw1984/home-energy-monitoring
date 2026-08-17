import asyncio

from src.collectors.fronius_inverter.fronius_symo_inverter_collector import (
    FoniusSymoInverterCollector,
)

from src.database.influxdb import InfluxDatabase
from src.collectors.iec.iec_collector import IecCollector
from src.manager.collector_manager import CollectorManager
from src.config import config_obj

#
#  poetry run python -m app
#


async def run():
    print("App.run()")
    iec = IecCollector(
        port=config_obj.ir_device0,
    )

    fronius = FoniusSymoInverterCollector(
        inverter_ip=config_obj.fronius_ip,
    )

    database = InfluxDatabase(
        url=config_obj.influxdb_url,
        token=config_obj.influxdb_token,
        org=config_obj.influxdb_org,
        bucket=config_obj.influxdb_bucket,
    )

    manager = CollectorManager(
        collectors=[
            # iec,
            fronius,
        ],
        database=database,
        interval=config_obj.collection_interval,
    )

    try:

        # await manager.run()

        # debug
        await manager.connect()
        measurements = await manager.collect_all()

        for measurement in measurements:
            print(measurement)

        await database.store(measurements)

    finally:

        await manager.disconnect()  # debug
        database.close()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
