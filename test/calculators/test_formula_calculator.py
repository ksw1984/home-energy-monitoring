from datetime import datetime, UTC

from src.calculators.formula_calculator import FormulaCalculator
from src.collectors.definitions.measurement import Measurement
from src.config.config import (
    CalculationConfig,
    CalculatorInputConfig,
)


def measurement(
    source,
    metric,
    value,
    timestamp=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
):
    return Measurement(
        timestamp=timestamp,
        source=source,
        metric=metric,
        value=value,
        unit="W",
    )


def test_formula_calculator_calculates_value():
    calculator = FormulaCalculator(
        calculations=[
            CalculationConfig(
                source="calculated",
                metric="heat_pump_power",
                unit="W",
                formula="grid - household",
                inputs={
                    "grid": CalculatorInputConfig(
                        source="meter_grid",
                        metric="grid_import_power",
                    ),
                    "household": CalculatorInputConfig(
                        source="meter_household",
                        metric="grid_import_power",
                    ),
                },
            ),
        ],
    )

    measurements = [
        measurement(
            "meter_grid",
            "grid_import_power",
            1000.0,
        ),
        measurement(
            "meter_household",
            "grid_import_power",
            700.0,
        ),
    ]

    result = calculator.calculate(measurements)

    assert len(result) == 1
    assert result[0].source == "calculated"
    assert result[0].metric == "heat_pump_power"
    assert result[0].value == 300.0
    assert result[0].unit == "W"


def test_formula_calculator_uses_matching_timestamp():
    calculator = FormulaCalculator(
        calculations=[
            CalculationConfig(
                source="calculated",
                metric="heat_pump_power",
                unit="W",
                formula="grid - household",
                inputs={
                    "grid": CalculatorInputConfig(
                        source="meter_grid",
                        metric="grid_import_power",
                    ),
                    "household": CalculatorInputConfig(
                        source="meter_household",
                        metric="grid_import_power",
                    ),
                },
            ),
        ],
    )

    timestamp = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)

    measurements = [
        measurement(
            "meter_grid",
            "grid_import_power",
            1000.0,
            timestamp,
        ),
        measurement(
            "meter_household",
            "grid_import_power",
            700.0,
            timestamp,
        ),
    ]

    result = calculator.calculate(measurements)

    assert len(result) == 1
    assert result[0].timestamp == timestamp
    assert result[0].value == 300.0


def test_formula_calculator_uses_estimated_measurement():
    calculator = FormulaCalculator(
        calculations=[
            CalculationConfig(
                source="calculated",
                metric="heat_pump_power",
                unit="W",
                formula="grid - household",
                inputs={
                    "grid": CalculatorInputConfig(
                        source="meter_grid",
                        metric="grid_import_power",
                    ),
                    "household": CalculatorInputConfig(
                        source="estimated",
                        metric="grid_import_power",
                    ),
                },
            ),
        ],
    )

    timestamp = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)

    measurements = [
        measurement(
            "meter_grid",
            "grid_import_power",
            1000.0,
            timestamp,
        ),
        measurement(
            "estimated",
            "grid_import_power",
            700.0,
            timestamp,
        ),
    ]

    result = calculator.calculate(measurements)

    assert len(result) == 1
    assert result[0].timestamp == timestamp
    assert result[0].value == 300.0
    assert result[0].unit == "W"
