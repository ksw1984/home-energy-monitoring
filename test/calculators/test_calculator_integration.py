from datetime import datetime, UTC

from src.calculators.formula_calculator import FormulaCalculator
from src.collectors.definitions.common.measurement import Measurement
from src.config.config import (
    CalculationConfig,
    CalculatorInputConfig,
)
from src.estimators.kalman_estimator import KalmanEstimator


def measurement(
    source: str,
    metric: str,
    timestamp: int,
    value: float,
) -> Measurement:
    return Measurement(
        timestamp=datetime.fromtimestamp(
            timestamp,
            tz=UTC,
        ),
        source=source,
        metric=metric,
        value=value,
        unit="W",
    )


def test_estimator_output_can_be_used_by_calculator():
    estimator = KalmanEstimator(
        source="meter_household",
        metric="grid_import_power",
        lookback=2,
    )

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

    grid = measurement(
        "meter_grid",
        "grid_import_power",
        10,
        1000.0,
    )

    household = [
        measurement(
            "meter_household",
            "grid_import_power",
            0,
            500.0,
        ),
        measurement(
            "meter_household",
            "grid_import_power",
            5,
            600.0,
        ),
        measurement(
            "meter_household",
            "grid_import_power",
            12,
            700.0,
        ),
    ]

    estimator.add_measurements(household)
    target_timestamp = estimator.target_timestamp()
    assert target_timestamp is not None
    estimated_measurement = estimator.estimate_at(target_timestamp)

    assert estimated_measurement is not None

    result = calculator.calculate(
        [
            grid,
            estimated_measurement,
        ],
    )

    assert len(result) == 1
    assert result[0].source == "calculated"
    assert result[0].metric == "heat_pump_power"
    assert result[0].unit == "W"
