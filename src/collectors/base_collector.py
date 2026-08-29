from abc import ABC, abstractmethod

from src.collectors.definitions.measurement import Measurement


class BaseCollector(ABC):

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
