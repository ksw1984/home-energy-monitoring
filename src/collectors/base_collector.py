from abc import ABC, abstractmethod

from src.collectors.definitions.measurement import Measurement


class BaseCollector(ABC):

    @abstractmethod
    def collect(self) -> list[Measurement]:
        raise NotImplementedError

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass
