import logging
from abc import ABC, abstractmethod
from datetime import datetime
from zoneinfo import ZoneInfo

from src.collectors.definitions.common.measurement import Measurement

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    def __init__(
        self,
        *,
        timezone: str = "UTC",
        interval: float = 300,
        enabled: bool = True,
        source: str,
        storage_interval: int | None = None,
    ) -> None:
        """Initialize common collector state.

        Args:
            timezone: Timezone used for measurement timestamps.
            interval: Collection interval in seconds.
            enabled: Whether the collector loop runs.
            source: Measurement source identifier.
            storage_interval: Optional positive number of seconds between
                database writes. ``None`` stores every successful collection.
        """
        self.timezone: ZoneInfo = ZoneInfo(timezone)
        self.interval: float = interval
        self.enabled: bool = enabled
        self.source: str = source
        self.storage_interval: int | None = storage_interval

    def now(self) -> datetime:
        return datetime.now(self.timezone)

    def localize_timestamp(self, timestamp: datetime) -> datetime:
        return timestamp.astimezone(self.timezone)

    @abstractmethod
    def collect(self) -> list[Measurement]:
        """Collect measurements."""
        raise NotImplementedError

    def connect(self) -> None:
        """Open collector resources.

        Most collectors do not require a persistent connection.
        Collectors that do can override this method.
        """
        return

    def disconnect(self) -> None:
        """Close collector resources.

        Most collectors do not require a persistent connection.
        Collectors that do can override this method.
        """
        return

    def configure_runtime(
        self,
        *,
        interval: float,
        enabled: bool,
        storage_interval: int | None = None,
    ) -> None:
        """Apply reloaded runtime settings to this collector.

        Invalid values are logged and ignored so a bad live edit cannot stop a
        running collector. The persistence scheduler notices the resulting
        attribute change on its next loop and resets its counter.

        Args:
            interval: New collection interval in seconds.
            enabled: Whether the collector loop should run.
            storage_interval: New database cadence in seconds, or ``None`` to
                persist every successful collection.
        """
        if interval <= 0:
            logger.warning(
                "Invalid interval=%s for collector %s; keeping current interval=%ss",
                interval,
                self.source,
                self.interval,
            )
        else:
            self.interval = interval

        if storage_interval is not None and (isinstance(storage_interval, bool) or not isinstance(storage_interval, int) or storage_interval <= 0):
            logger.warning(
                "Invalid storage_interval=%r for collector %s; keeping current storage_interval=%r",
                storage_interval,
                self.source,
                self.storage_interval,
            )
        else:
            self.storage_interval = storage_interval

        self.enabled = enabled
