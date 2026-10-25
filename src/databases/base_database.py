import logging
from abc import ABC, abstractmethod
from datetime import datetime

from src.collectors.definitions.common.measurement import Measurement

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

    async def query_measurements(  # noqa: PLR0913 - this is the shared time-series query contract.
        self,
        *,
        source: str,
        metrics: list[str],
        start: datetime | None = None,
        stop: datetime | None = None,
        after: datetime | None = None,
        limit: int = 1000,
    ) -> list[Measurement]:
        """Read measurements in timestamp order.

        ``start`` is inclusive, ``stop`` is exclusive, and ``after`` is a
        strict cursor for incremental consumers. Backends may override this
        method; write-only databases remain usable by existing HEMS code.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support measurement queries")
