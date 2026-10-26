from __future__ import annotations

import logging

from src.collectors.base_collector import BaseCollector
from src.collectors.definitions.collectors.obis import (
    CURRENT_OBIS,
    get_obis_definition,
)
from src.collectors.definitions.common.measurement import Measurement

import serial
from .dlms_protocol import DlmsProtocol

logger = logging.getLogger(__name__)


class DlmsCollector(BaseCollector):
    """Collect current measurements from a DLMS/COSEM meter.

    The collector maintains a persistent DLMS connection and polls the
    configured current OBIS values on each collection cycle.

    The actual collection interval is controlled by the collector
    manager through the ``interval`` value passed to ``BaseCollector``.

    Only OBIS codes listed in ``CURRENT_OBIS`` are requested. Their
    metric names and units are resolved through the shared
    :mod:`obis` definitions.

    Args:
        enabled: Whether the collector is enabled.
        timezone: Timezone used for measurement timestamps.
        interval: Collection interval in seconds.
        source: Source identifier stored with generated measurements.
        port: Serial device used for the DLMS connection.
        obis_codes: OBIS codes read on every collection. Defaults to
            ``CURRENT_OBIS``. Use a single register such as ``{"16.7.0"}``
            for latency probes or fast power-only polling.
        read_timeout: Maximum seconds to wait for each register reply.
            ``None`` keeps the protocol default of 30 seconds.
    """

    def __init__(  # noqa: PLR0913 - one keyword per collector attribute keeps config mapping explicit.
        self,
        *,
        enabled: bool = True,
        timezone: str = "UTC",
        interval: int = 1,
        source: str = "landys_gyr_zmd310_meter_dlms",
        port: str = "/dev/ttyUSB0",
        obis_codes: set[str] | None = None,
        read_timeout: float | None = None,
    ) -> None:
        """Initialize the DLMS collector.

        Args:
            enabled: Whether the collector is enabled.
            timezone: Timezone used for measurement timestamps.
            interval: Collection interval in seconds.
            source: Measurement source identifier.
            port: Serial device used by the meter.
            obis_codes: OBIS codes to read. ``None`` means ``CURRENT_OBIS``.
            read_timeout: Per-reply timeout in seconds. ``None`` uses the
                protocol default.
        """
        super().__init__(
            enabled=enabled,
            timezone=timezone,
            interval=interval,
            source=source,
        )

        self.port = port
        # ``None`` keeps the module-level CURRENT_OBIS lookup dynamic so the
        # default set can still be patched in tests and future callers.
        self.obis_codes = set(obis_codes) if obis_codes is not None else None
        self.read_timeout = read_timeout
        self.protocol = DlmsProtocol(port)
        self.connected = False

    def connect(self) -> None:
        """Establish the persistent DLMS connection.

        Disabled collectors do not open their serial port.

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
        """Read the configured current OBIS values.

        Returns:
            Measurements created from the values returned by the DLMS
            protocol. An empty list is returned when the collector is
            disabled.

        Raises:
            serial.SerialException: If communication with the meter
                fails.
            TimeoutError: If the meter does not respond in time.
            RuntimeError: If the DLMS protocol reports an error.
        """
        if not self.enabled:
            return []

        if not self.connected:
            self.connect()

        try:
            obis_codes = self.obis_codes if self.obis_codes is not None else CURRENT_OBIS

            # Only pass a timeout when one was configured so the protocol keeps
            # its own default and existing callers see an unchanged signature.
            read_kwargs = {} if self.read_timeout is None else {"timeout": self.read_timeout}

            values = self.protocol.read(obis_codes, **read_kwargs)

        except (
            serial.SerialException,
            TimeoutError,
            RuntimeError,
        ):
            logger.exception(
                "DLMS communication failed",
            )

            self.connected = False
            self.protocol.disconnect()

            raise

        timestamp = self.now()
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
