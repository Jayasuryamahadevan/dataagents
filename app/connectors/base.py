from abc import ABC, abstractmethod
from typing import Any


class Connector(ABC):
    @abstractmethod
    def fetch(self) -> list[dict[str, Any]]:
        raise NotImplementedError
