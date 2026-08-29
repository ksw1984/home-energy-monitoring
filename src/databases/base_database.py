from abc import ABC, abstractmethod

from src.collectors.definitions.measurement import Measurement


class BaseDatabase(ABC):

    @abstractmethod
    async def store(self, measurements: list[Measurement]) -> None:
        pass

    @abstractmethod
    def close(self) -> None:
        pass
