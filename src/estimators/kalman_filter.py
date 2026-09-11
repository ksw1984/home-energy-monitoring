from dataclasses import dataclass
from datetime import datetime


@dataclass
class KalmanState:
    value: float
    slope: float


@dataclass
class KalmanHistoryEntry:
    timestamp: datetime
    state: KalmanState
    p00: float
    p01: float
    p10: float
    p11: float
    unit: str


class KalmanFilter:
    """Two-state Kalman filter for value and slope."""

    def __init__(
        self,
        *,
        process_variance: float = 1.0,
        measurement_variance: float = 1.0,
    ) -> None:
        self.process_variance = process_variance
        self.measurement_variance = measurement_variance

        self.state: KalmanState | None = None

        # Covariance matrix P.
        self._p00 = 1.0
        self._p01 = 0.0
        self._p10 = 0.0
        self._p11 = 1.0

    def update(
        self,
        *,
        value: float,
        dt: float,
    ) -> KalmanState:
        if dt <= 0:
            raise ValueError(f"dt must be positive, got {dt}")

        if self.state is None:
            self.state = KalmanState(
                value=value,
                slope=0.0,
            )
            return self.state

        x: float = self.state.value
        v: float = self.state.slope

        # -------------------------------------------------
        # Predict
        # -------------------------------------------------

        predicted_value: float = x + v * dt
        predicted_slope: float = v

        # F = [[1, dt],
        #      [0,  1]]

        p00: float = self._p00 + dt * (self._p10 + self._p01) + dt * dt * self._p11
        p01: float = self._p01 + dt * self._p11
        p10: float = self._p10 + dt * self._p11
        p11: float = self._p11

        # Process noise.
        q: float = self.process_variance

        p00 += q * dt**4 / 4
        p01 += q * dt**3 / 2
        p10 += q * dt**3 / 2
        p11 += q * dt**2

        # -------------------------------------------------
        # Measurement update
        # -------------------------------------------------

        # We only measure value:
        #
        # z = [1, 0] x

        innovation: float = value - predicted_value

        innovation_variance: float = p00 + self.measurement_variance

        k0: float = p00 / innovation_variance
        k1: float = p10 / innovation_variance

        updated_value: float = predicted_value + k0 * innovation

        updated_slope: float = predicted_slope + k1 * innovation

        # Joseph form isn't necessary here because H=[1,0].
        updated_p00: float = (1 - k0) * p00
        updated_p01: float = (1 - k0) * p01
        updated_p10: float = p10 - k1 * p00
        updated_p11: float = p11 - k1 * p01

        self.state = KalmanState(
            value=updated_value,
            slope=updated_slope,
        )

        self._p00 = updated_p00
        self._p01 = updated_p01
        self._p10 = updated_p10
        self._p11 = updated_p11

        return self.state

    def history_entry(
        self,
        *,
        timestamp: datetime,
        unit: str,
    ) -> KalmanHistoryEntry:
        if self.state is None:
            raise RuntimeError("Cannot create history entry before first update")

        return KalmanHistoryEntry(
            timestamp=timestamp,
            state=KalmanState(
                value=self.state.value,
                slope=self.state.slope,
            ),
            p00=self._p00,
            p01=self._p01,
            p10=self._p10,
            p11=self._p11,
            unit=unit,
        )
