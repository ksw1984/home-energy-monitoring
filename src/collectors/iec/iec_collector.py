import logging
import re

from src.collectors.base_collector import BaseCollector
from src.collectors.definitions.measurement import Measurement
from src.collectors.definitions.obis import CURRENT_OBIS, get_obis_definition

from .iec_protocol import IecProtocol

logger = logging.getLogger(__name__)


class IecCollector(BaseCollector):
    """Collect current electrical measurements from an IEC 62056-21 meter.

    The collector communicates with the meter through an
    :class:`IecProtocol` instance and converts supported current OBIS
    values into the common :class:`Measurement` representation.

    Only OBIS codes defined in ``CURRENT_OBIS`` are collected. Historical
    or profile data may be present in the meter telegram and may have
    definitions in ``obis.py``, but is intentionally ignored by this
    collector.

    The serial connection is established lazily when ``collect()`` is
    called if it has not already been established through ``connect()``.
    """

    def __init__(
        self,
        timezone: str = "UTC",
        *,
        port="/dev/ttyUSB0",
        source="iec",
    ) -> None:
        """Initialize the IEC meter collector.

        Args:
            port: Serial device used to communicate with the IEC meter.
        """
        super().__init__(timezone)

        self.protocol = IecProtocol(port)
        self.source = source
        self.connected = False

    def connect(self) -> None:
        """Establish the IEC meter connection."""
        self.protocol.connect()
        self.connected = True

    def disconnect(self) -> None:
        """Close the IEC meter connection."""
        self.protocol.disconnect()
        self.connected = False

    def collect(self) -> list[Measurement]:
        """Read and return the current measurements from the meter.

        If the collector is not connected, the IEC connection is
        established automatically before reading the meter telegram.

        Returns:
            A list of measurements for the supported current OBIS codes.

        Raises:
            RuntimeError: If the IEC protocol cannot read because the
                connection is not available.
        """
        if not self.connected:
            self.connect()

        text = self.protocol.read()

        return self._parse(text)

    def _parse(self, text: str) -> list[Measurement]:
        """Parse supported current values from an IEC meter telegram.

        Example values include::

            1-1:1.5.0(00.000*kW)
            1-1:2.5.0(07.417*kW)
            1-1:32.7.0(243.1*V)

        Only OBIS codes listed in ``CURRENT_OBIS`` are converted into
        measurements. Historical and unsupported OBIS values are ignored.

        Args:
            text: Decoded IEC meter telegram.

        Returns:
            A list of measurements for recognized current OBIS values.
        """

        timestamp = self.now()

        measurements: list[Measurement] = []

        pattern = re.compile(r"([0-9]+-[0-9]+:)?" r"([0-9]+\.[0-9]+\.[0-9]+)" r"\(([^)]*)\)")

        for match in pattern.finditer(text):
            obis = match.group(2)
            raw_value = match.group(3)

            # Ignore historical values and all other OBIS codes.
            if obis not in CURRENT_OBIS:
                continue

            definition = get_obis_definition(obis)

            if definition is None:
                continue

            value, _ = self._parse_value(raw_value)

            measurements.append(
                Measurement(
                    timestamp=timestamp,
                    source=self.source,
                    metric=definition.metric,
                    value=value,
                    unit=definition.unit,
                )
            )

        return measurements

    @staticmethod
    def _parse_value(raw_value: str) -> tuple[float, str]:
        """Parse a numeric IEC value and its optional unit.

        Examples::

            07.417*kW
            243.1*V
            -8.77*kW
            +0.59*kvar

        Args:
            raw_value: Raw value including the optional unit.

        Returns:
            A tuple containing the numeric value and unit string.

        Raises:
            ValueError: If the numeric portion cannot be converted to
                a floating-point value.
        """

        if "*" in raw_value:
            value_string, unit = raw_value.split("*", 1)
        else:
            value_string = raw_value
            unit = ""

        return float(value_string), unit
