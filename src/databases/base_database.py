from abc import ABC, abstractmethod

from src.collectors.definitions.measurement import Measurement


class BaseDatabase(ABC):

    @abstractmethod
    async def store(self, measurements: list[Measurement]) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError
