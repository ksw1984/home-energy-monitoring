"""Decouple collector sampling from database persistence.

A collector may run faster than the database write cadence. This scheduler
counts *successful* collections per collector and answers whether the current
batch should be persisted. Failed or empty collections do not advance the
counter, so a temporary outage does not move the persistence boundary.

The scheduler is intentionally stateful and single-consumer: one instance is
owned by one collector loop. Configuration changes call :meth:`update`, which
recomputes ``n`` and resets the counter so the next successful batch is stored.
"""

import logging

logger = logging.getLogger(__name__)

# Warn when the integer number of cycles cannot represent the configured
# storage period closely enough. A one-second cadence with a five-second
# storage target maps exactly; a 1.1 s cadence with a 5 s target would map to
# 5.5 s and is worth surfacing.
STORAGE_DEVIATION_WARNING_RATIO = 0.05


class PersistenceScheduler:
    """Decide which successful collection batches reach the database.

    The effective database period is ``n * interval``, where ``n`` is the
    rounded ratio between the requested storage interval and the collection
    interval. The measurement bus and downstream consumers are not affected by
    this decision; they always see every successful collection.

    Attributes:
        interval: Collection interval in seconds.
        storage_interval: Requested database interval in seconds, or ``None``
            to persist every successful collection.
        cycles: Number of successful collections between database writes.
    """

    def __init__(
        self,
        *,
        interval: float,
        storage_interval: int | None,
    ) -> None:
        """Create a scheduler for one collector.

        Args:
            interval: Positive collection interval in seconds.
            storage_interval: Positive whole number of seconds between
                database writes, or ``None`` to store every collection.

        Raises:
            ValueError: If the interval is not positive or the storage
                interval is not a positive integer.
        """
        if interval <= 0:
            raise ValueError(f"interval must be positive, got {interval!r}")

        if storage_interval is not None and (isinstance(storage_interval, bool) or not isinstance(storage_interval, int) or storage_interval <= 0):
            raise ValueError(f"storage_interval must be a positive integer or None, got {storage_interval!r}")

        self.interval = float(interval)
        self.storage_interval = storage_interval
        self.cycles = self._compute_cycles()
        self._successful_collects = 0

    @property
    def effective_storage_interval_s(self) -> float:
        """Return the database period that the integer cycle count can provide."""
        return self.cycles * self.interval

    @property
    def cadence_label(self) -> str:
        """Return a human-readable persistence cadence for logs."""
        if self.storage_interval is None:
            return "every collection"

        return f"every {self.cycles} collections ({self.effective_storage_interval_s:g}s)"

    def update(
        self,
        *,
        interval: float,
        storage_interval: int | None,
    ) -> bool:
        """Apply a runtime configuration change.

        The counter resets only when the cadence actually changes. This keeps a
        reload that leaves the collector untouched from shifting the next
        database write.

        Args:
            interval: Current collector interval in seconds.
            storage_interval: Current configured storage interval.

        Returns:
            ``True`` when the cadence changed and the counter was reset.

        Raises:
            ValueError: If the new interval or storage interval is invalid.
        """
        interval = float(interval)

        if interval == self.interval and storage_interval == self.storage_interval:
            return False

        if interval <= 0:
            raise ValueError(f"interval must be positive, got {interval!r}")

        if storage_interval is not None and (isinstance(storage_interval, bool) or not isinstance(storage_interval, int) or storage_interval <= 0):
            raise ValueError(f"storage_interval must be a positive integer or None, got {storage_interval!r}")

        self.interval = interval
        self.storage_interval = storage_interval
        self.cycles = self._compute_cycles()
        self._successful_collects = 0
        return True

    def record_success(self, *, elapsed_s: float | None = None) -> bool:
        """Record one successful collection and decide whether to persist it.

        The first successful collection after creation or :meth:`update` is
        always persisted. Subsequent writes happen after every ``cycles``
        successful collections.

        Args:
            elapsed_s: Wall-clock duration of the collection cycle, when
                available. Used to warn when a read overruns the configured
                interval and the real database spacing becomes larger than
                ``n * interval``.

        Returns:
            ``True`` when the caller should write this batch to the database.
        """
        if elapsed_s is not None and elapsed_s > self.interval:
            logger.warning(
                "Collector cycle took %.3fs, longer than interval=%.3fs; database spacing may exceed the configured %.3fs",
                elapsed_s,
                self.interval,
                self.effective_storage_interval_s,
            )

        should_persist = self._successful_collects % self.cycles == 0
        self._successful_collects += 1
        return should_persist

    def reset(self) -> None:
        """Make the next successful collection persist."""
        self._successful_collects = 0

    def _compute_cycles(self) -> int:
        """Return the integer cycle count and warn when it deviates too far.

        ``storage_interval / interval`` is used instead of a raw "every nth
        flag" so a fractional collector interval still produces a database
        cadence close to the configured number of seconds.
        """
        if self.storage_interval is None:
            return 1

        cycles = max(1, round(self.storage_interval / self.interval))
        effective = cycles * self.interval
        deviation = abs(effective - self.storage_interval) / self.storage_interval

        if deviation > STORAGE_DEVIATION_WARNING_RATIO:
            logger.warning(
                "storage_interval=%ss at interval=%ss resolves to n=%d (%.3fs, %.1f%% from requested)",
                self.storage_interval,
                self.interval,
                cycles,
                effective,
                deviation * 100.0,
            )

        return cycles
