from abc import ABC, abstractmethod

from src.collectors.definitions.measurement import Measurement


class BaseEstimator(ABC):
    """Base interface for measurement estimators."""

    @abstractmethod
    def estimate(
        self,
        measurements: list[Measurement],
    ) -> list[Measurement]:
        """Estimate measurements from collected data."""
        raise NotImplementedError
