import re
from datetime import datetime

from src.collector.definitions.measurement import Measurement
from src.collector.definitions.obis import CURRENT_OBIS, get_obis_definition

from .iec_protocol import IecProtocol


class IecCollector:
    """
    Collector for IEC 62056-21 meter data.

    The collector currently collects only current measurements.
    Historical/profile values are defined in obis.py but ignored
    during collection.
    """

    SOURCE = "iec"

    def __init__(
        self,
        port="/dev/ttyUSB0",
    ):
        self.protocol = IecProtocol(port)
        self.connected = False

    def connect(self) -> None:
        self.protocol.connect()
        self.connected = True

    def disconnect(self) -> None:
        self.protocol.disconnect()
        self.connected = False

    def collect(self) -> list[Measurement]:
        """
        Read the current IEC values and return them as Measurements.
        """
        if not self.connected:
            self.connect()

        text = self.protocol.read()

        return self._parse(text)

    @classmethod
    def _parse(cls, text: str) -> list[Measurement]:
        """
        Parse IEC values from the meter response.

        Example:

            1-1:1.5.0(00.000*kW)
            1-1:2.5.0(07.417*kW)
            1-1:32.7.0(243.1*V)

        Only OBIS codes listed in CURRENT_OBIS are returned.
        """

        timestamp = datetime.now().astimezone()

        measurements = []

        pattern = re.compile(
            r"([0-9]+-[0-9]+:)?" r"([0-9]+\.[0-9]+\.[0-9]+)" r"\(([^)]*)\)"
        )

        for match in pattern.finditer(text):
            obis = match.group(2)
            raw_value = match.group(3)

            # Ignore historical values and all other OBIS codes.
            if obis not in CURRENT_OBIS:
                continue

            definition = get_obis_definition(obis)

            if definition is None:
                continue

            value, unit = cls._parse_value(raw_value)

            # Prefer the unit supplied by the OBIS definition.
            if definition.unit:
                unit = definition.unit

            measurements.append(
                Measurement(
                    timestamp=timestamp,
                    source=cls.SOURCE,
                    metric=definition.metric,
                    value=value,
                    unit=unit,
                    obis=obis,
                )
            )

        return measurements

    @staticmethod
    def _parse_value(raw_value: str) -> tuple[float, str]:
        """
        Parse values such as:

            07.417*kW
            243.1*V
            -8.77*kW
            +0.59*kvar
        """

        if "*" in raw_value:
            value_string, unit = raw_value.split("*", 1)
        else:
            value_string = raw_value
            unit = ""

        return float(value_string), unit
