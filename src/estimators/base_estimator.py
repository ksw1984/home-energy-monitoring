from abc import ABC, abstractmethod
from datetime import datetime

from src.collectors.definitions.measurement import Measurement


class BaseEstimator(ABC):
    """Base interface for measurement estimators."""

    source: str
    metric: str

    @abstractmethod
    def add_measurements(
        self,
        measurements: list[Measurement],
    ) -> None:
        """Add measurements to the estimator history."""
        raise NotImplementedError

    @abstractmethod
    def target_timestamp(self) -> datetime | None:
        """Return the timestamp selected by the configured lookback."""
        raise NotImplementedError

    @abstractmethod
    def estimate_at(
        self,
        target_timestamp: datetime,
    ) -> Measurement | None:
        """Estimate a measurement at the requested timestamp."""
        raise NotImplementedError
