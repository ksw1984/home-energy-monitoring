from src.config.config import (
    CalculationConfig,
    CalculatorInputConfig,
    EstimatorConfig,
)
from src.estimators.estimator_factory import create_estimators
from src.estimators.kalman_estimator import KalmanEstimator


def test_create_kalman_estimator():
    config = EstimatorConfig(
        type="kalman",
        enabled=True,
        lookback=7,
    )

    calculations = [
        CalculationConfig(
            source="calculated",
            metric="something",
            unit="W",
            formula="household + grid",
            inputs={
                "household": CalculatorInputConfig(
                    source="meter_household",
                    metric="grid_import_power",
                ),
                "grid": CalculatorInputConfig(
                    source="meter_grid",
                    metric="grid_import_power",
                ),
            },
        ),
    ]

    estimators = create_estimators(
        [config],
        calculations,
    )

    assert len(estimators) == 2
    assert all(isinstance(estimator, KalmanEstimator) for estimator in estimators)

    assert {(estimator.source, estimator.metric) for estimator in estimators} == {
        ("meter_household", "grid_import_power"),
        ("meter_grid", "grid_import_power"),
    }

    assert all(estimator.lookback == 7 for estimator in estimators)
