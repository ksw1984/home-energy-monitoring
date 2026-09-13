from src.collectors.definitions.measurement import Measurement
from src.config.config import StorageConfig


class StorageFilter:
    """Filter measurements according to the storage configuration."""

    def __init__(self, config: StorageConfig) -> None:
        self._enabled = config.enabled
        self._measurements = {
            (
                measurement.source,
                measurement.metric,
                measurement.measurement_type,
            )
            for measurement in config.measurements
        }

    def filter(
        self,
        measurements: list[Measurement],
    ) -> list[Measurement]:
        """Return measurements enabled for persistent storage.

        Args:
            measurements: Measurements collected by a collector.

        Returns:
            Measurements allowed by the storage configuration.
        """
        if not self._enabled:
            return []

        return [measurement for measurement in measurements if measurement.storage_key in self._measurements]

    def is_selected(
        self,
        measurement: Measurement,
    ) -> bool:
        """Return whether a measurement is selected for persistent storage."""
        return self._enabled and measurement.storage_key in self._measurements

    def update(self, config: StorageConfig) -> None:
        self._enabled = config.enabled
        self._measurements = {measurement.storage_key for measurement in config.measurements}
