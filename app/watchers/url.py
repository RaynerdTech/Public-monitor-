import asyncio

import httpx

from app.watchers.base import BaseWatcher, SourcePost


class UrlWatcher(BaseWatcher):
    """Poll a public web page and emit it only when its content changes."""

    name = "url"

    def __init__(self, url: str, interval_seconds: int = 30):
        self.url = url
        self.interval_seconds = max(5, interval_seconds)
        self._last_body: str | None = None
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            )
        }

    async def fetch(self) -> list[SourcePost]:
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers=self._headers,
        ) as client:
            response = await client.get(self.url)
            response.raise_for_status()
            body = response.text

        if body == self._last_body:
            return []

        self._last_body = body
        return [SourcePost(source="web", text=body, url=self.url)]

    async def stream(self):
        while True:
            try:
                for post in await self.fetch():
                    yield post
            except httpx.HTTPError:
                # A temporary source failure should not stop a 24/7 watcher.
                pass
            await asyncio.sleep(self.interval_seconds)
