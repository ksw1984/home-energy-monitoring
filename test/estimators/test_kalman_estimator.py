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
