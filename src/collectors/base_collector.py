from abc import ABC, abstractmethod
from datetime import datetime
from zoneinfo import ZoneInfo

from src.collectors.definitions.measurement import Measurement


class BaseCollector(ABC):
    def __init__(self, timezone: str = "UTC"):
        self.timezone = ZoneInfo(timezone)

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
