from datetime import datetime, timedelta, UTC

from src.collectors.definitions.measurement import Measurement
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


def test_history_size():
    estimator = KalmanEstimator(
        source="test",
        metric="power",
        latency=timedelta(seconds=2),
        history_size=3,
    )

    estimator.estimate(
        [
            measurement(0, 0),
            measurement(10, 10),
            measurement(20, 20),
            measurement(30, 30),
        ]
    )

    assert len(estimator._history) == 3
    assert estimator._history[0].timestamp == datetime.fromtimestamp(
        10,
        tz=UTC,
    )


def test_estimator_uses_actual_timestamp_interval():
    estimator = KalmanEstimator(
        source="test",
        metric="power",
        latency=timedelta(seconds=2),
        history_size=10,
    )

    result = estimator.estimate(
        [
            measurement(0, 0),
            measurement(10, 10),
            measurement(30, 30),
        ]
    )

    assert len(result) == 1
    assert result[0].timestamp == datetime.fromtimestamp(
        28,
        tz=UTC,
    )


def test_estimator_waits_until_latency_is_available():
    estimator = KalmanEstimator(
        source="test",
        metric="power",
        latency=timedelta(seconds=2),
        history_size=10,
    )

    result = estimator.estimate([measurement(0, 100)])

    assert result == []


def test_fixed_lag_smoothing_uses_future_measurements():
    estimator = KalmanEstimator(
        source="test",
        metric="power",
        latency=timedelta(seconds=2),
        history_size=10,
        process_variance=1.0,
        measurement_variance=0.01,
    )

    estimator.estimate(
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

    estimator.estimate(
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
