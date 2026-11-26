import logging

from src.config.config import CalculationConfig
from src.hems.calculators.base_calculator import BaseCalculator
from src.shared.measurement import Measurement

from simpleeval import simple_eval

logger = logging.getLogger(__name__)


class FormulaCalculator(BaseCalculator):
    """Calculate measurements from configured formulas."""

    def __init__(
        self,
        calculations: list[CalculationConfig],
    ) -> None:
        self.calculations = calculations

    def calculate(
        self,
        measurements: list[Measurement],
    ) -> list[Measurement]:
        calculated: list[Measurement] = []

        for calculation in self.calculations:
            values = self._get_input_values(
                calculation,
                measurements,
            )

            if values is None:
                continue

            try:
                value = simple_eval(
                    calculation.formula,
                    names=values,
                )
            except ZeroDivisionError:
                logger.debug(
                    "Skipping calculation because of division by zero: source=%s metric=%s formula=%s values=%s",
                    calculation.source,
                    calculation.metric,
                    calculation.formula,
                    values,
                )
                continue

            timestamp = max(
                measurement.timestamp
                for measurement in measurements
                if any(
                    measurement.source == input_config.source and measurement.metric == input_config.metric
                    for input_config in calculation.inputs.values()
                )
            )

            calculated_measurement = Measurement(
                timestamp=timestamp,
                source=calculation.source,
                metric=calculation.metric,
                value=float(value),
                unit=calculation.unit,
            )

            logger.debug(
                "Calculated measurement: source=%s metric=%s timestamp=%s value=%.3f %s C",
                calculated_measurement.source,
                calculated_measurement.metric,
                calculated_measurement.timestamp.isoformat(),
                calculated_measurement.value,
                calculated_measurement.unit or "",
            )

            calculated.append(calculated_measurement)

        return calculated

    def _get_input_values(
        self,
        calculation: CalculationConfig,
        measurements: list[Measurement],
    ) -> dict[str, float] | None:
        """Resolve formula inputs from the current batch or configured defaults.

        Defaults are intended for optional inputs such as PV power when an
        inverter is offline. A default is used in-memory for the formula only;
        it is never stored as a measurement.
        """
        values: dict[str, float] = {}

        for name, input_config in calculation.inputs.items():
            matching = [
                measurement
                for measurement in measurements
                if (measurement.source == input_config.source and measurement.metric == input_config.metric)
            ]

            if not matching:
                if input_config.default is None:
                    return None
                values[name] = input_config.default
                continue

            latest = max(
                matching,
                key=lambda measurement: measurement.timestamp,
            )

            values[name] = latest.value

        return values
