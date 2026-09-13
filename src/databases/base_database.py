import logging
from abc import ABC, abstractmethod

from src.collectors.definitions.measurement import Measurement

logger = logging.getLogger(__name__)


class BaseDatabase(ABC):
    def __init__(
        self,
        *,
        type: str,
        enabled: bool = True,
    ) -> None:
        self.type = type
        self.enabled = enabled

    def configure_runtime(
        self,
        *,
        enabled: bool,
    ) -> None:
        self.enabled = enabled

    def connect(self) -> None:
        """Open database resources.

        Databases that do not require a persistent connection can leave
        this method unchanged.
        """
        return

    def close(self) -> None:
        """Close database resources."""
        return

    @abstractmethod
    async def store(self, measurements: list[Measurement]) -> None:
        raise NotImplementedError
