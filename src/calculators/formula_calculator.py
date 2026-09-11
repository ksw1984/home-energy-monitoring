from src.calculators.base_calculator import BaseCalculator
from src.collectors.definitions.measurement import Measurement
from src.config.config import CalculationConfig

from simpleeval import simple_eval


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

            value = simple_eval(
                calculation.formula,
                names=values,
            )

            timestamp = max(
                measurement.timestamp
                for measurement in measurements
                if any(
                    measurement.source == input_config.source and measurement.metric == input_config.metric
                    for input_config in calculation.inputs.values()
                )
            )

            calculated.append(
                Measurement(
                    timestamp=timestamp,
                    source=calculation.source,
                    metric=calculation.metric,
                    value=float(value),
                    unit=calculation.unit,
                )
            )

        return calculated

    def _get_input_values(
        self,
        calculation: CalculationConfig,
        measurements: list[Measurement],
    ) -> dict[str, float] | None:
        values: dict[str, float] = {}

        for name, input_config in calculation.inputs.items():
            matching = [
                measurement
                for measurement in measurements
                if (measurement.source == input_config.source and measurement.metric == input_config.metric)
            ]

            if not matching:
                return None

            latest = max(
                matching,
                key=lambda measurement: measurement.timestamp,
            )

            values[name] = latest.value

        return values
