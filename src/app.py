import asyncio

from src.collectors.fronius_inverter.fronius_symo_inverter_collector import (
    FoniusSymoInverterCollector,
)
from src.collectors.iec.iec_collector import IecCollector
from src.manager.collector_manager import CollectorManager

#
#  poetry run python -m app
#


async def run():
    print("App.run()")
    iec = IecCollector(
        port="/dev/ttyUSB0",
    )

    fronius = FoniusSymoInverterCollector(
        inverter_ip="192.168.178.25",
    )

    manager = CollectorManager(
        collectors=[
            # iec,
            fronius,
        ],
        interval=10,
    )

    await manager.run()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
