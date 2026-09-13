from abc import ABC, abstractmethod

from src.collectors.definitions.measurement import Measurement


class BaseCalculator(ABC):
    """Base interface for measurement calculators."""

    @abstractmethod
    def calculate(
        self,
        measurements: list[Measurement],
    ) -> list[Measurement]:
        """Calculate derived measurements."""
        raise NotImplementedError
