from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from src.collectors.base_collector import BaseCollector
from src.collectors.definitions.collectors.obis import (
    FAST_OBIS,
    get_obis_definition,
    SLOW_OBIS,
)
from src.collectors.definitions.common.measurement import Measurement

import serial
from .dlms_protocol import DlmsProtocol

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)


class DlmsCollector(BaseCollector):
    """Collect measurements from a DLMS/COSEM meter.

    The collector keeps one persistent DLMS connection and splits the register
    set into two schedules:

    * **fast** registers (``FAST_OBIS``, currently only ``16.7.0``) are read on
      every collection cycle, so the interval can be sub-second;
    * **slow** registers (``SLOW_OBIS``, the energy totals) are read on the
      first cycle and then once per ``slow_interval_s`` inside the same
      connection.

    A slow-read failure never discards the fast sample and never stops the
    fast loop. A fast-read failure keeps the reconnect-and-raise behavior so
    the manager can log the unavailable collector.

    Args:
        enabled: Whether the collector is enabled.
        timezone: Timezone used for measurement timestamps.
        interval: Fast collection interval in seconds. May be fractional.
        source: Source identifier stored with generated measurements.
        port: Serial device used for the DLMS connection.
        obis_codes: Fast OBIS codes. Defaults to ``FAST_OBIS``. An explicit set
            without ``slow_obis_codes`` means "read exactly this set" and
            disables automatic slow reads (used by the poll-interval probe).
        slow_obis_codes: Slow OBIS codes. Defaults to ``SLOW_OBIS`` when
            ``obis_codes`` is not supplied; otherwise empty.
        slow_interval_s: Seconds between successful slow-register reads.
        slow_retry_interval_s: Seconds before retrying a failed slow read.
        read_timeout: Maximum seconds to wait for each register reply.
            ``None`` keeps the protocol default of 30 seconds.
    """

    def __init__(  # noqa: PLR0913 - one keyword per collector attribute keeps config mapping explicit.
        self,
        *,
        enabled: bool = True,
        timezone: str = "UTC",
        interval: float = 1.0,
        source: str = "landys_gyr_zmd310_meter_dlms",
        port: str = "/dev/ttyUSB0",
        obis_codes: set[str] | None = None,
        slow_obis_codes: set[str] | None = None,
        slow_interval_s: float = 3600.0,
        slow_retry_interval_s: float = 60.0,
        read_timeout: float | None = None,
    ) -> None:
        """Initialize the DLMS collector.

        Args:
            enabled: Whether the collector is enabled.
            timezone: Timezone used for measurement timestamps.
            interval: Fast collection interval in seconds.
            source: Measurement source identifier.
            port: Serial device used by the meter.
            obis_codes: Fast OBIS codes. ``None`` uses ``FAST_OBIS``.
            slow_obis_codes: Slow OBIS codes. ``None`` uses ``SLOW_OBIS`` for
                production defaults, or no slow reads when ``obis_codes`` was
                explicitly supplied.
            slow_interval_s: Seconds between successful slow reads.
            slow_retry_interval_s: Seconds before retrying a failed slow read.
            read_timeout: Per-reply timeout in seconds. ``None`` uses the
                protocol default.

        Raises:
            ValueError: If a scheduling interval is not positive or a register
                appears in both the fast and slow sets.
        """
        super().__init__(
            enabled=enabled,
            timezone=timezone,
            interval=interval,
            source=source,
        )

        self.port = port
        self.fast_obis_codes = set(obis_codes) if obis_codes is not None else set(FAST_OBIS)
        if slow_obis_codes is not None:
            self.slow_obis_codes = set(slow_obis_codes)
        elif obis_codes is not None:
            # Custom/probe mode: the caller asked for an exact register set.
            self.slow_obis_codes = set()
        else:
            self.slow_obis_codes = set(SLOW_OBIS)

        if slow_interval_s <= 0:
            raise ValueError("slow_interval_s must be positive")
        if slow_retry_interval_s <= 0:
            raise ValueError("slow_retry_interval_s must be positive")

        overlap = self.fast_obis_codes & self.slow_obis_codes
        if overlap:
            raise ValueError(f"OBIS codes cannot be both fast and slow: {', '.join(sorted(overlap))}")

        self.slow_interval_s = slow_interval_s
        self.slow_retry_interval_s = slow_retry_interval_s
        # Monotonic deadline. Zero means "read on the next cycle".
        self._next_slow_read_at = 0.0
        self.read_timeout = read_timeout
        self.protocol = DlmsProtocol(port)
        self.connected = False

    def connect(self) -> None:
        """Establish the persistent DLMS connection.

        Disabled collectors do not open their serial port. The slow-read
        deadline survives reconnects so a failed slow read is not retried on
        every fast cycle.

        Raises:
            Exception: If the DLMS connection cannot be established.
        """
        if not self.enabled:
            logger.debug(
                "DLMS collector '%s' disabled; skipping connection on %s",
                self.source,
                self.port,
            )
            return

        if self.connected:
            return

        try:
            self.protocol.connect()
        except Exception:
            self.connected = False
            raise

        self.connected = True

        logger.info(
            "DLMS collector '%s' connected on %s",
            self.source,
            self.port,
        )

    def disconnect(self) -> None:
        """Close the persistent DLMS connection."""
        self.protocol.disconnect()
        self.connected = False

    def collect(self) -> list[Measurement]:
        """Read the fast registers and, when due, the slow energy totals.

        Returns:
            Measurements for every value read in this cycle. Slow values are
            included only on cycles where the slow schedule is due.

        Raises:
            serial.SerialException: If the fast read fails.
            TimeoutError: If the meter does not answer a fast read in time.
            RuntimeError: If the DLMS protocol reports an error on a fast read.
        """
        if not self.enabled:
            return []

        if not self.connected:
            self.connect()

        try:
            values = self._read(self.fast_obis_codes)
        except (
            serial.SerialException,
            TimeoutError,
            RuntimeError,
        ):
            logger.exception(
                "DLMS fast read failed",
            )

            self.connected = False
            self.protocol.disconnect()

            raise

        if self.slow_obis_codes and time.monotonic() >= self._next_slow_read_at:
            try:
                values.update(self._read(self.slow_obis_codes))
            except (
                serial.SerialException,
                TimeoutError,
                RuntimeError,
            ):
                # A broken slow read can leave the HDLC stream out of sync.
                # Keep the fast values from this cycle, close the connection,
                # and retry the slow set after a bounded backoff.
                logger.exception(
                    "DLMS slow read failed; fast polling continues",
                )
                self._next_slow_read_at = time.monotonic() + self.slow_retry_interval_s
                self.connected = False
                self.protocol.disconnect()
            except (TypeError, ValueError):
                # A malformed value is a data/config problem, not a broken
                # connection; keep the connection and retry later.
                logger.exception(
                    "DLMS slow read returned invalid data; fast polling continues",
                )
                self._next_slow_read_at = time.monotonic() + self.slow_retry_interval_s
            else:
                self._next_slow_read_at = time.monotonic() + self.slow_interval_s

        timestamp = self.now()
        return self._to_measurements(values, timestamp)

    def _read(self, obis_codes: set[str]) -> dict[str, float]:
        """Read one register group with the optional per-reply timeout."""
        if not obis_codes:
            return {}

        # Only pass a timeout when one was configured so the protocol keeps its
        # own default and existing callers see an unchanged signature.
        read_kwargs = {} if self.read_timeout is None else {"timeout": self.read_timeout}

        return self.protocol.read(obis_codes, **read_kwargs)

    def _to_measurements(
        self,
        values: dict[str, float],
        timestamp: datetime,
    ) -> list[Measurement]:
        """Convert raw OBIS values into measurements using shared definitions."""
        measurements: list[Measurement] = []

        for obis, value in values.items():
            definition = get_obis_definition(obis)

            if definition is None:
                logger.warning(
                    "No OBIS definition for %s",
                    obis,
                )
                continue

            if not isinstance(value, (int, float)):
                raise TypeError(f"Expected numeric value, got {type(value).__name__}")

            measurements.append(
                Measurement(
                    timestamp=timestamp,
                    source=self.source,
                    metric=definition.metric,
                    value=float(value),
                    unit=definition.unit,
                )
            )

        return measurements
