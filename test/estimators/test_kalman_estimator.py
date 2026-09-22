from datetime import datetime, UTC

from src.collectors.definitions.common.measurement import Measurement
from src.estimators.kalman_estimator import KalmanEstimator


def measurement(
    timestamp: int,
    value: float,
) -> Measurement:
    return Measurement(
        timestamp=datetime.fromtimestamp(
            timestamp,
            tz=UTC,
        ),
        source="test",
        metric="power",
        value=value,
        unit="W",
    )


def test_lookback():
    estimator = KalmanEstimator(
        source="test",
        metric="power",
        lookback=3,
    )

    estimator.add_measurements(
        [
            measurement(0, 0),
            measurement(10, 10),
            measurement(20, 20),
            measurement(30, 30),
        ]
    )

    assert estimator.lookback == 3
    assert estimator.target_timestamp() == datetime.fromtimestamp(
        10,
        tz=UTC,
    )


def test_estimator_uses_actual_timestamp_interval():
    estimator = KalmanEstimator(
        source="test",
        metric="power",
        lookback=1,
    )

    estimator.add_measurements(
        [
            measurement(0, 0),
            measurement(10, 10),
            measurement(30, 30),
        ]
    )

    result = estimator.estimate_at(
        datetime.fromtimestamp(28, tz=UTC),
    )

    assert result is not None
    assert result.timestamp == datetime.fromtimestamp(
        28,
        tz=UTC,
    )


def test_estimator_waits_until_lookback_is_available():
    estimator = KalmanEstimator(
        source="test",
        metric="power",
        lookback=3,
    )

    measurements = [
        measurement(0, 100.0),
        measurement(10, 110.0),
    ]

    estimator.add_measurements(measurements)

    assert estimator.target_timestamp() is None

    estimator.add_measurements([measurement(20, 120.0)])

    assert estimator.target_timestamp() is not None


def test_fixed_lag_smoothing_uses_future_measurements():
    estimator = KalmanEstimator(
        source="test",
        metric="power",
        lookback=1,
        process_variance=1.0,
        measurement_variance=0.01,
    )

    estimator.add_measurements(
        [
            measurement(0, 0),
            measurement(4, 4),
            measurement(8, 8),
            measurement(10, 10),
        ]
    )

    before = estimator._estimate_at(
        datetime.fromtimestamp(
            8,
            tz=UTC,
        )
    )

    estimator.add_measurements(
        [
            measurement(12, 20),
        ]
    )

    after = estimator._estimate_at(
        datetime.fromtimestamp(
            8,
            tz=UTC,
        )
    )

    assert before is not None
    assert after is not None

    assert after.value != before.value
