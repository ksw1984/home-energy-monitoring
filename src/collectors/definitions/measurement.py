from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Measurement:
    """A single normalized measurement produced by a collector.

    Attributes:
        timestamp:
            The point in time the measurement applies to.
            Examples:
                - Current reading: ``2026-08-26 20:00+02:00``
                - Forecast value: ``2026-08-27 14:00+02:00``

        source:
            Identifier of the system that produced the measurement.
            Examples:
                - ``"iec"``
                - ``"open_meteo"``
                - ``"rademacher"``
                - ``"fronius"``

        metric:
            Stable internal name describing what was measured.
            Examples:
                - ``"temperature"``
                - ``"grid_import_power"``
                - ``"grid_import_energy_total"``
                - ``"precipitation_probability"``

        value:
            Numeric value of the measurement.

        unit:
            Unit in which ``value`` is expressed.
            Examples:
                - ``"°C"``
                - ``"W"``
                - ``"kWh"``
                - ``"%"``

        measurement_type:
            Describes whether the measurement is an actual observation
            or a forecast.
            Defaults to ``"current"``.

            Examples:
                - ``"current"``
                - ``"forecast"``
    """

    timestamp: datetime
    source: str
    metric: str
    value: float
    unit: str
    measurement_type: str = "current"
