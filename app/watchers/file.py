import asyncio
from pathlib import Path

from app.watchers.base import BaseWatcher, SourcePost


class FileWatcher(BaseWatcher):
    """Simple local watcher used to test the live pipeline end-to-end."""

    name = "file"

    def __init__(self, path: str, interval_seconds: int = 2):
        self.path = Path(path)
        self.interval_seconds = max(1, interval_seconds)
        self._last_text = ""

    async def fetch(self) -> list[SourcePost]:
        if not self.path.exists():
            self.path.touch()

        text = self.path.read_text(encoding="utf-8", errors="ignore")
        if text == self._last_text:
            return []

        self._last_text = text
        return [
            SourcePost(
                source=f"file:{self.path.name}",
                text=text,
                url=str(self.path.resolve()),
            )
        ]

    async def stream(self):
        while True:
            for post in await self.fetch():
                yield post
            await asyncio.sleep(self.interval_seconds)
