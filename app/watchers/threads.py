import asyncio
from datetime import datetime

import httpx

from app.watchers.base import BaseWatcher, SourcePost


THREADS_SEARCH_URL = "https://graph.threads.net/keyword_search"
THREADS_FIELDS = "id,permalink,username,text,timestamp"


def parse_threads_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


class ThreadsWatcher(BaseWatcher):
    """Monitor recent public Threads posts matching one or more keywords."""

    name = "threads"

    def __init__(
        self,
        access_token: str,
        queries: list[str],
        *,
        interval_seconds: int = 30,
        limit: int = 50,
    ) -> None:
        if not access_token:
            raise ValueError("Threads access token is required")
        if not queries:
            raise ValueError("At least one Threads query is required")

        self.access_token = access_token
        self.queries = queries
        self.interval_seconds = max(10, interval_seconds)
        self.limit = min(100, max(1, limit))
        self._seen_post_ids: set[str] = set()

    async def _search(self, client: httpx.AsyncClient, query: str) -> list[dict]:
        response = await client.get(
            THREADS_SEARCH_URL,
            params={
                "q": query,
                "search_type": "RECENT",
                "search_mode": "KEYWORD",
                "fields": THREADS_FIELDS,
                "limit": self.limit,
            },
        )
        response.raise_for_status()
        payload = response.json()

        data = payload.get("data", []) if isinstance(payload, dict) else []
        return data if isinstance(data, list) else []

    async def fetch(self) -> list[SourcePost]:
        posts: list[SourcePost] = []

        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"Authorization": f"Bearer {self.access_token}"},
        ) as client:
            for query in self.queries:
                rows = await self._search(client, query)

                for row in rows:
                    if not isinstance(row, dict):
                        continue

                    post_id = str(row.get("id") or "").strip()
                    if not post_id or post_id in self._seen_post_ids:
                        continue

                    self._seen_post_ids.add(post_id)

                    text = str(row.get("text") or "")
                    permalink = str(row.get("permalink") or "") or None
                    username = str(row.get("username") or "").strip()

                    posts.append(
                        SourcePost(
                            source=f"threads:@{username}" if username else "threads",
                            text=text,
                            url=permalink,
                            created_at=parse_threads_timestamp(row.get("timestamp")),
                        )
                    )

        posts.sort(
            key=lambda post: post.created_at or datetime.min,
        )
        return posts

    async def stream(self):
        backoff = self.interval_seconds

        while True:
            try:
                for post in await self.fetch():
                    yield post
                backoff = self.interval_seconds
                await asyncio.sleep(self.interval_seconds)
            except httpx.HTTPStatusError as exc:
                # Slow down on API/rate-limit failures without killing the watcher.
                if exc.response.status_code == 429:
                    backoff = min(max(backoff * 2, 60), 900)
                else:
                    backoff = min(max(backoff, 30), 300)
                await asyncio.sleep(backoff)
            except httpx.HTTPError:
                backoff = min(max(backoff * 2, 30), 300)
                await asyncio.sleep(backoff)
