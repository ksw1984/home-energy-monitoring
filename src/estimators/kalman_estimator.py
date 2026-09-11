from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.collectors.definitions.measurement import Measurement
from src.estimators.base_estimator import BaseEstimator
from src.estimators.kalman_filter import (
    KalmanCovariance,
    KalmanFilter,
    KalmanHistoryEntry,
    KalmanState,
)

SMOOTHER_DETERMINANT_EPSILON = 1e-12


@dataclass(frozen=True)
class KalmanSmootherGain:
    """RTS smoother gain matrix."""

    p00: float
    p01: float
    p10: float
    p11: float


class KalmanEstimator(BaseEstimator):
    """Estimate delayed measurements with a fixed-lag Kalman smoother."""

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
        """Add measurements and return the latest delayed estimate."""
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
        """Update the filter and append its state to history."""
        if self._last_timestamp is not None and measurement.timestamp <= self._last_timestamp:
            return

        dt: float = 1.0 if self._last_timestamp is None else (measurement.timestamp - self._last_timestamp).total_seconds()

        self._filter.update(
            value=measurement.value,
            dt=dt,
        )

        prediction = self._filter.last_prediction

        if prediction is None:
            raise RuntimeError("Kalman filter did not produce a prediction")

        self._history.append(
            self._filter.history_entry(
                timestamp=measurement.timestamp,
                unit=measurement.unit,
                prediction=prediction,
            )
        )

        self._last_timestamp = measurement.timestamp

    def _estimate_at(
        self,
        target_timestamp: datetime,
    ) -> Measurement | None:
        """Estimate the signal at the requested timestamp."""
        if not self._history:
            return None

        entries = list(self._history)
        target_index = self._find_target_index(
            entries,
            target_timestamp,
        )

        if target_index is None:
            return None

        smoothed_state = self._smooth_history(
            entries,
            target_index,
        )

        return self._create_estimate(
            entries[target_index],
            target_timestamp,
            smoothed_state,
        )

    def _find_target_index(
        self,
        entries: list[KalmanHistoryEntry],
        target_timestamp: datetime,
    ) -> int | None:
        """Find the latest history entry at or before the target."""
        target_index: int | None = None

        for index, entry in enumerate(entries):
            if entry.timestamp <= target_timestamp:
                target_index = index
            else:
                break

        return target_index

    def _smooth_history(
        self,
        entries: list[KalmanHistoryEntry],
        target_index: int,
    ) -> KalmanState:
        """Run the RTS smoother backwards to the target entry."""
        smoothed_state = KalmanState(
            value=entries[-1].state.value,
            slope=entries[-1].state.slope,
        )

        smoothed_covariance = entries[-1].covariance

        for index in range(
            len(entries) - 2,
            target_index - 1,
            -1,
        ):
            current = entries[index]
            next_entry = entries[index + 1]

            result = self._smooth_step(
                current=current,
                next_entry=next_entry,
                smoothed_state=smoothed_state,
                smoothed_covariance=smoothed_covariance,
            )

            if result is None:
                continue

            smoothed_state, smoothed_covariance = result

        return smoothed_state

    def _smooth_step(
        self,
        *,
        current: KalmanHistoryEntry,
        next_entry: KalmanHistoryEntry,
        smoothed_state: KalmanState,
        smoothed_covariance: KalmanCovariance,
    ) -> tuple[KalmanState, KalmanCovariance] | None:
        """Apply one backwards RTS smoothing step."""
        dt = (next_entry.timestamp - current.timestamp).total_seconds()

        if dt <= 0:
            return None

        current_covariance = current.covariance
        predicted_covariance = next_entry.prediction.covariance

        c = self._smoother_gain(
            covariance=current_covariance,
            predicted_covariance=predicted_covariance,
            dt=dt,
        )

        if c is None:
            return None

        predicted_state = next_entry.prediction.state

        value_difference = smoothed_state.value - predicted_state.value
        slope_difference = smoothed_state.slope - predicted_state.slope

        state = KalmanState(
            value=(current.state.value + c.p00 * value_difference + c.p01 * slope_difference),
            slope=(current.state.slope + c.p10 * value_difference + c.p11 * slope_difference),
        )

        covariance = self._smooth_covariance(
            current=current_covariance,
            predicted=predicted_covariance,
            smoothed=smoothed_covariance,
            gain=c,
        )

        return state, covariance

    def _smoother_gain(
        self,
        *,
        covariance: KalmanCovariance,
        predicted_covariance: KalmanCovariance,
        dt: float,
    ) -> KalmanSmootherGain | None:
        """Calculate the RTS smoother gain matrix."""
        fp00 = covariance.p00 + dt * covariance.p01
        fp01 = covariance.p01 + dt * covariance.p11
        fp10 = covariance.p10 + dt * covariance.p11
        fp11 = covariance.p11

        det = predicted_covariance.p00 * predicted_covariance.p11 - predicted_covariance.p01 * predicted_covariance.p10

        if abs(det) < SMOOTHER_DETERMINANT_EPSILON:
            return None

        inv00 = predicted_covariance.p11 / det
        inv01 = -predicted_covariance.p01 / det
        inv10 = -predicted_covariance.p10 / det
        inv11 = predicted_covariance.p00 / det

        return KalmanSmootherGain(
            p00=fp00 * inv00 + fp01 * inv10,
            p01=fp00 * inv01 + fp01 * inv11,
            p10=fp10 * inv00 + fp11 * inv10,
            p11=fp10 * inv01 + fp11 * inv11,
        )

    def _smooth_covariance(
        self,
        *,
        current: KalmanCovariance,
        predicted: KalmanCovariance,
        smoothed: KalmanCovariance,
        gain: KalmanSmootherGain,
    ) -> KalmanCovariance:
        """Update covariance during RTS smoothing."""
        p_diff00 = smoothed.p00 - predicted.p00
        p_diff01 = smoothed.p01 - predicted.p01
        p_diff10 = smoothed.p10 - predicted.p10
        p_diff11 = smoothed.p11 - predicted.p11

        return KalmanCovariance(
            p00=(current.p00 + gain.p00 * p_diff00 + gain.p01 * p_diff10),
            p01=(current.p01 + gain.p00 * p_diff01 + gain.p01 * p_diff11),
            p10=(current.p10 + gain.p10 * p_diff00 + gain.p11 * p_diff10),
            p11=(current.p11 + gain.p10 * p_diff01 + gain.p11 * p_diff11),
        )

    def _create_estimate(
        self,
        entry: KalmanHistoryEntry,
        target_timestamp: datetime,
        state: KalmanState,
    ) -> Measurement:
        """Create a measurement from the smoothed state."""
        dt = (target_timestamp - entry.timestamp).total_seconds()

        value = state.value + state.slope * dt

        return Measurement(
            timestamp=target_timestamp,
            source=self.source,
            metric=self.metric,
            value=value,
            unit=entry.unit,
        )
