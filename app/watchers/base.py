from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class SourcePost:
    source: str
    text: str
    url: str | None = None
    created_at: datetime | None = None


class BaseWatcher(ABC):
    name: str = "base"

    @abstractmethod
    async def fetch(self) -> list[SourcePost]:
        raise NotImplementedError
