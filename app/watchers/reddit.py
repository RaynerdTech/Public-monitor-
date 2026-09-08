import asyncio
import time
from datetime import datetime, timezone

import httpx

from app.watchers.base import BaseWatcher, SourcePost


REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_SEARCH_URL = "https://oauth.reddit.com/search"


def parse_reddit_timestamp(value: object) -> datetime | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def reddit_row_to_source_post(row: dict) -> SourcePost | None:
    post_id = str(row.get("id") or "").strip()
    if not post_id:
        return None

    subreddit = str(row.get("subreddit") or "").strip()
    author = str(row.get("author") or "").strip()
    permalink = str(row.get("permalink") or "").strip()
    source_url = f"https://www.reddit.com{permalink}" if permalink else None

    parts: list[str] = []
    for key in ("title", "selftext", "url_overridden_by_dest", "url"):
        value = row.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())

    source_bits = ["reddit"]
    if subreddit:
        source_bits.append(f"r/{subreddit}")
    if author:
        source_bits.append(f"u/{author}")

    return SourcePost(
        source=":".join(source_bits),
        text="\n".join(parts),
        url=source_url,
        created_at=parse_reddit_timestamp(row.get("created_utc")),
    )


class RedditWatcher(BaseWatcher):
    """Read-only Reddit OAuth search watcher for new public submissions."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        user_agent: str,
        queries: list[str],
        *,
        interval_seconds: int = 20,
        limit: int = 100,
    ) -> None:
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.user_agent = user_agent.strip()
        self.queries = [query.strip() for query in queries if query.strip()]
        self.interval_seconds = max(10, interval_seconds)
        self.limit = min(100, max(1, limit))
        self._access_token: str | None = None
        self._token_expires_at = 0.0
        self._seen_post_ids: set[str] = set()

    async def _get_access_token(self, client: httpx.AsyncClient) -> str:
        # Refresh with a small safety margin before Reddit expires the token.
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        response = await client.post(
            REDDIT_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            headers={"User-Agent": self.user_agent},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise httpx.HTTPError("Reddit token response was not JSON object data")

        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise httpx.HTTPError("Reddit did not return an OAuth access token")

        try:
            expires_in = int(payload.get("expires_in") or 3600)
        except (TypeError, ValueError):
            expires_in = 3600

        self._access_token = token
        self._token_expires_at = time.time() + max(60, expires_in)
        return token

    async def _search(
        self,
        client: httpx.AsyncClient,
        token: str,
        query: str,
    ) -> dict:
        response = await client.get(
            REDDIT_SEARCH_URL,
            params={
                "q": query,
                "sort": "new",
                "t": "hour",
                "limit": self.limit,
                "raw_json": 1,
            },
            headers={
                "Authorization": f"bearer {token}",
                "User-Agent": self.user_agent,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def fetch(self) -> list[SourcePost]:
        posts: list[SourcePost] = []

        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            token = await self._get_access_token(client)

            for query in self.queries:
                try:
                    payload = await self._search(client, token, query)
                except httpx.HTTPStatusError as exc:
                    # Retry once with a fresh OAuth token if Reddit rejects an expired token.
                    if exc.response.status_code != 401:
                        raise
                    self._access_token = None
                    self._token_expires_at = 0
                    token = await self._get_access_token(client)
                    payload = await self._search(client, token, query)

                data = payload.get("data")
                children = data.get("children", []) if isinstance(data, dict) else []
                if not isinstance(children, list):
                    continue

                for child in children:
                    if not isinstance(child, dict):
                        continue
                    row = child.get("data")
                    if not isinstance(row, dict):
                        continue

                    post_id = str(row.get("id") or "").strip()
                    if not post_id or post_id in self._seen_post_ids:
                        continue

                    self._seen_post_ids.add(post_id)
                    post = reddit_row_to_source_post(row)
                    if post is not None:
                        posts.append(post)

        posts.sort(
            key=lambda post: post.created_at or datetime.min.replace(tzinfo=timezone.utc)
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
                if exc.response.status_code == 429:
                    backoff = min(max(backoff * 2, 60), 900)
                elif exc.response.status_code in {401, 403}:
                    raise
                else:
                    backoff = min(max(backoff, 30), 300)
                await asyncio.sleep(backoff)
            except httpx.HTTPError:
                backoff = min(max(backoff * 2, 30), 300)
                await asyncio.sleep(backoff)
