"""In-process fan-out of the newest measurement per ``(source, metric)``.

The bus is the single source of truth for "latest value" inside the HEMS
process. :class:`~src.manager.measurement_manager.MeasurementManager`
publishes every collected batch synchronously on the event-loop thread;
consumers such as BEMS live control read the latest value and await the next
generation without touching a database.

Timing model:

* :meth:`MeasurementBus.publish` is non-blocking and wakes every waiter.
* :meth:`MeasurementBus.latest` never moves backwards in time: a batch with an
  older timestamp for an existing key is ignored.
* :meth:`MeasurementBus.wait_for_next` returns when a strictly newer value for
  the watched key is published, or when its own timeout expires. A publish for
  an unrelated key wakes the waiter but does not count as a new sample and does
  not extend the timeout.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from src.collectors.definitions.common.measurement import Measurement


class MeasurementBus:
    """Hold the latest measurement per source/metric and wake waiting consumers."""

    def __init__(self) -> None:
        """Create an empty bus with a loop-agnostic wake-up event."""
        self._latest: dict[tuple[str, str], Measurement] = {}
        self._wake_up = asyncio.Event()

    def publish(self, measurements: Iterable[Measurement]) -> None:
        """Store a collected batch and wake every waiter.

        Called by the measurement manager on the event-loop thread right after
        a successful collect. Persistence decisions must not gate this call:
        samples that are not written to a database still have to reach live
        consumers.

        Args:
            measurements: Raw collector measurements from one successful cycle.
        """
        changed = False

        for measurement in measurements:
            key = (measurement.source, measurement.metric)
            current = self._latest.get(key)

            # Ignore out-of-order batches so ``latest`` is monotonic per key.
            if current is not None and measurement.timestamp <= current.timestamp:
                continue

            self._latest[key] = measurement
            changed = True

        if changed:
            self._wake_up.set()

    def latest(self, source: str, metric: str) -> Measurement | None:
        """Return the newest published measurement for one series.

        Args:
            source: Measurement source identifier.
            metric: Measurement metric name.

        Returns:
            The newest measurement, or ``None`` when the series was never
            published.
        """
        return self._latest.get((source, metric))

    async def wait_for_next(
        self,
        *,
        source: str,
        metric: str,
        after_timestamp: datetime | None,
        timeout: float,
    ) -> bool:
        """Wait until a newer value for one series is published.

        The method is level-triggered: it checks the current latest value
        before waiting, so a sample published before the call is not missed.
        Wake-ups for other series do not reset ``timeout``.

        Args:
            source: Measurement source to watch.
            metric: Measurement metric to watch.
            after_timestamp: Timestamp already handled by the caller. Pass
                ``None`` to accept any available value. Must be a timezone-aware
                ``datetime`` when provided.
            timeout: Maximum seconds to wait.

        Returns:
            ``True`` when a strictly newer value exists, ``False`` when the
            timeout expired first.

        Raises:
            ValueError: If ``timeout`` is not positive.
        """
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while True:
            if self._is_newer(source, metric, after_timestamp):
                return True

            remaining = deadline - loop.time()

            if remaining <= 0:
                return False

            # Clear before re-checking: a publish between the check above and
            # the clear would otherwise be lost because the event was set
            # without a waiter present.
            self._wake_up.clear()

            if self._is_newer(source, metric, after_timestamp):
                return True

            try:
                await asyncio.wait_for(
                    self._wake_up.wait(),
                    timeout=remaining,
                )
            except TimeoutError:
                return False

    def _is_newer(
        self,
        source: str,
        metric: str,
        after_timestamp: datetime | None,
    ) -> bool:
        """Return whether the latest value for a series is newer than a cursor."""
        current = self._latest.get((source, metric))

        if current is None:
            return False

        if after_timestamp is None:
            return True

        return current.timestamp > after_timestamp
