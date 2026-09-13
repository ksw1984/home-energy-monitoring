from datetime import timedelta

from src.config.config import (
    EstimatorConfig,
    EstimatorMeasurementConfig,
)
from src.estimators.estimator_factory import create_estimators
from src.estimators.kalman_estimator import KalmanEstimator


def test_create_kalman_estimator():
    config = EstimatorConfig(
        type="kalman",
        enabled=True,
        latency=timedelta(seconds=2),
        history_size=7,
        measurements=[
            EstimatorMeasurementConfig(
                source="meter_household",
                metric="grid_import_power",
            ),
        ],
    )

    estimators = create_estimators([config])

    assert len(estimators) == 1
    assert isinstance(estimators[0], KalmanEstimator)
    assert estimators[0].source == "meter_household"
    assert estimators[0].metric == "grid_import_power"
    assert estimators[0].latency == timedelta(seconds=2)
    assert estimators[0]._history.maxlen == 7
