from abc import ABC, abstractmethod

from src.collectors.definitions.measurement import Measurement


class BaseCollector(ABC):

    @abstractmethod
    def collect(self) -> list[Measurement]:
        raise NotImplementedError
