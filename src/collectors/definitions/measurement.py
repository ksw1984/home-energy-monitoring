from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Measurement:
    """A single normalized measurement produced by a collector.

    Attributes:
        timestamp:
            The point in time the measurement applies to.
            Examples:
                - Current sensor reading: ``2026-08-26 20:00+02:00``
                - Forecast value: ``2026-08-27 14:00+02:00``

        source:
            Identifier of the system or collector that produced the
            measurement.
            Examples:
                - ``"rademacher"``
                - ``"fronius"``
                - ``"iec"``
                - ``"open_meteo"``

        metric:
            Stable internal name describing what was measured.
            Examples:
                - ``"temperature"``
                - ``"pv_power"``
                - ``"grid_import_power"``
                - ``"precipitation_probability"``

        value:
            Numeric value of the measurement.
            Examples:
                - ``21.5`` for 21.5 °C
                - ``1250.0`` for 1250 W
                - ``75.0`` for 75 % cloud cover

            Boolean values should be normalized to numeric values:
                - ``0.0`` = false / not detected
                - ``1.0`` = true / detected

        unit:
            Unit in which ``value`` is expressed.
            Examples:
                - ``"°C"``
                - ``"W"``
                - ``"kWh"``
                - ``"%"``
                - ``"m/s"``
                - ``""`` for dimensionless values such as weather codes

        scope:
            Describes whether the measurement represents an actual
            observation or a forecast.
            Defaults to ``"current"``.
            Examples:
                - ``"current"`` for current sensor/API measurements
                - ``"forecast"`` for predicted future values

        obis:
            Optional OBIS identifier for measurements originating from
            an electricity meter.
            Examples:
                - ``"1.8.0"``
                - ``"2.8.0"``
                - ``None`` for measurements without an OBIS code
    """

    timestamp: datetime
    source: str
    metric: str
    value: float
    unit: str
    obis: str | None = None
    measurement_type: str = "current"
