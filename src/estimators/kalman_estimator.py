from collections import deque
from datetime import datetime, timedelta

from src.collectors.definitions.measurement import Measurement
from src.estimators.kalman_filter import (
    KalmanFilter,
    KalmanHistoryEntry,
    KalmanState,
)


class KalmanEstimator:
    def __init__(  # noqa: PLR0913
        self,
        *,
        source: str,
        metric: str,
        latency: timedelta,
        history_size: int = 10,
        process_variance: float = 1.0,
        measurement_variance: float = 1.0,
    ) -> None:
        if history_size <= 0:
            raise ValueError(f"history_size must be positive, got {history_size}")

        if latency < timedelta(0):
            raise ValueError(f"latency must not be negative, got {latency}")

        self.source = source
        self.metric = metric
        self.latency = latency

        self._filter = KalmanFilter(
            process_variance=process_variance,
            measurement_variance=measurement_variance,
        )

        self._history: deque[KalmanHistoryEntry] = deque(
            maxlen=history_size,
        )

        self._last_timestamp: datetime | None = None
        self._last_emitted_timestamp: datetime | None = None

    def estimate(
        self,
        measurements: list[Measurement],
    ) -> list[Measurement]:
        relevant: list[Measurement] = [
            measurement for measurement in measurements if (measurement.source == self.source and measurement.metric == self.metric)
        ]

        for measurement in sorted(
            relevant,
            key=lambda item: item.timestamp,
        ):
            self._add_measurement(measurement)

        if not self._history:
            return []

        latest_timestamp: datetime = self._history[-1].timestamp
        target_timestamp: datetime = latest_timestamp - self.latency

        if self._last_emitted_timestamp is not None and target_timestamp <= self._last_emitted_timestamp:
            return []

        estimated = self._estimate_at(target_timestamp)

        if estimated is None:
            return []

        self._last_emitted_timestamp = target_timestamp

        return [estimated]

    def _add_measurement(
        self,
        measurement: Measurement,
    ) -> None:
        if self._last_timestamp is not None and measurement.timestamp <= self._last_timestamp:
            return

        dt: float = 1.0 if self._last_timestamp is None else (measurement.timestamp - self._last_timestamp).total_seconds()

        self._filter.update(
            value=measurement.value,
            dt=dt,
        )

        self._history.append(
            self._filter.history_entry(
                timestamp=measurement.timestamp,
                unit=measurement.unit,
            )
        )

        self._last_timestamp = measurement.timestamp

    def _estimate_at(
        self,
        target_timestamp: datetime,
    ) -> Measurement | None:
        if not self._history:
            return None

        previous: KalmanHistoryEntry | None = None

        for entry in self._history:
            if entry.timestamp <= target_timestamp:
                previous = entry
            else:
                break

        if previous is None:
            return None

        state: KalmanState = previous.state

        if previous.timestamp == target_timestamp:
            value: float = state.value
        else:
            dt: float = (target_timestamp - previous.timestamp).total_seconds()

            value = state.value + state.slope * dt

        return Measurement(
            timestamp=target_timestamp,
            source=self.source,
            metric=self.metric,
            value=value,
            unit=previous.unit,
        )
