import logging
from abc import ABC, abstractmethod
from datetime import datetime
from zoneinfo import ZoneInfo

from src.collectors.definitions.measurement import Measurement

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    def __init__(
        self,
        *,
        timezone: str = "UTC",
        interval: int = 300,
        enabled: bool = True,
        source: str,
    ) -> None:
        self.timezone: ZoneInfo = ZoneInfo(timezone)
        self.interval: int = interval
        self.enabled: bool = enabled
        self.source: str = source

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
        interval: int,
        enabled: bool,
    ) -> None:
        if interval <= 0:
            logger.warning(
                "Invalid interval=%s for collector %s; keeping current interval=%ss",
                interval,
                self.source,
                self.interval,
            )
        else:
            self.interval = interval

        self.enabled = enabled
