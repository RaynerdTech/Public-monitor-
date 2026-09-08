import asyncio
import json
from datetime import datetime, timezone

import httpx

from app.watchers.base import BaseWatcher, SourcePost


X_RECENT_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
X_STREAM_URL = "https://api.x.com/2/tweets/search/stream"
X_STREAM_RULES_URL = "https://api.x.com/2/tweets/search/stream/rules"


def parse_x_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def extract_x_entity_urls(row: dict) -> list[str]:
    """Return expanded URLs from an X Post's entities.

    X normally replaces links in Post text with t.co URLs. The useful destination
    URL is exposed in entities.urls as expanded_url/unwound_url.
    """
    entities = row.get("entities")
    if not isinstance(entities, dict):
        return []

    raw_urls = entities.get("urls")
    if not isinstance(raw_urls, list):
        return []

    urls: list[str] = []
    seen: set[str] = set()
    for item in raw_urls:
        if not isinstance(item, dict):
            continue
        for key in ("unwound_url", "expanded_url", "url"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                value = value.strip()
                if value not in seen:
                    seen.add(value)
                    urls.append(value)
                break
    return urls


def _username_map(payload: dict) -> dict[str, str]:
    includes = payload.get("includes")
    users = includes.get("users", []) if isinstance(includes, dict) else []
    usernames: dict[str, str] = {}
    if not isinstance(users, list):
        return usernames

    for user in users:
        if not isinstance(user, dict):
            continue
        user_id = str(user.get("id") or "").strip()
        username = str(user.get("username") or "").strip()
        if user_id and username:
            usernames[user_id] = username
    return usernames


def x_row_to_source_post(row: dict, usernames: dict[str, str]) -> SourcePost | None:
    post_id = str(row.get("id") or "").strip()
    if not post_id:
        return None

    author_id = str(row.get("author_id") or "").strip()
    username = usernames.get(author_id)
    post_url = (
        f"https://x.com/{username}/status/{post_id}"
        if username
        else f"https://x.com/i/web/status/{post_id}"
    )

    text = str(row.get("text") or "")
    expanded_urls = extract_x_entity_urls(row)
    if expanded_urls:
        # Feed destination URLs into our normal referral extractor. This matters
        # because the visible Post text usually only contains a t.co short URL.
        text = "\n".join([text, *expanded_urls])

    return SourcePost(
        source=f"x:@{username}" if username else "x",
        text=text,
        url=post_url,
        created_at=parse_x_timestamp(row.get("created_at")),
    )


class XWatcher(BaseWatcher):
    """Recent-search watcher retained for diagnostics/backfill."""

    def __init__(
        self,
        bearer_token: str,
        queries: list[str],
        interval_seconds: int = 20,
        max_results: int = 100,
    ) -> None:
        self.bearer_token = bearer_token.strip()
        self.queries = [query.strip() for query in queries if query.strip()]
        self.interval_seconds = max(10, interval_seconds)
        self.max_results = min(100, max(10, max_results))
        self._seen_post_ids: set[str] = set()
        self._since_id_by_query: dict[str, str] = {}

    async def _search(self, client: httpx.AsyncClient, query: str) -> dict:
        params = {
            "query": query,
            "max_results": self.max_results,
            "sort_order": "recency",
            "tweet.fields": "created_at,author_id,entities",
            "expansions": "author_id",
            "user.fields": "username",
        }

        since_id = self._since_id_by_query.get(query)
        if since_id:
            params["since_id"] = since_id

        response = await client.get(X_RECENT_SEARCH_URL, params=params)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def fetch(self) -> list[SourcePost]:
        posts: list[SourcePost] = []

        headers = {"Authorization": f"Bearer {self.bearer_token}"}
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers=headers,
        ) as client:
            for query in self.queries:
                payload = await self._search(client, query)
                rows = payload.get("data", [])
                if not isinstance(rows, list):
                    rows = []

                usernames = _username_map(payload)

                newest_id = None
                meta = payload.get("meta", {})
                if isinstance(meta, dict):
                    newest_id = str(meta.get("newest_id") or "").strip() or None

                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    post_id = str(row.get("id") or "").strip()
                    if not post_id or post_id in self._seen_post_ids:
                        continue

                    self._seen_post_ids.add(post_id)
                    post = x_row_to_source_post(row, usernames)
                    if post is not None:
                        posts.append(post)

                if newest_id:
                    self._since_id_by_query[query] = newest_id

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
                elif exc.response.status_code in {401, 402, 403}:
                    raise
                else:
                    backoff = min(max(backoff, 30), 300)
                await asyncio.sleep(backoff)
            except httpx.HTTPError:
                backoff = min(max(backoff * 2, 30), 300)
                await asyncio.sleep(backoff)


class XFilteredStreamWatcher:
    """Persistent near-real-time X Filtered Stream connection."""

    def __init__(
        self,
        bearer_token: str,
        rules: list[str],
        *,
        tag_prefix: str = "refmon:",
        reconnect_seconds: int = 5,
    ) -> None:
        self.bearer_token = bearer_token.strip()
        self.rules = [rule.strip() for rule in rules if rule.strip()]
        self.tag_prefix = tag_prefix
        self.reconnect_seconds = max(1, reconnect_seconds)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.bearer_token}"}

    async def list_rules(self, client: httpx.AsyncClient | None = None) -> list[dict]:
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=15, headers=self.headers)
        try:
            response = await client.get(X_STREAM_RULES_URL)
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data", []) if isinstance(payload, dict) else []
            return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
        finally:
            if owns_client:
                await client.aclose()

    async def sync_rules(self) -> list[dict]:
        """Own only rules tagged with refmon:, leaving unrelated rules untouched."""
        async with httpx.AsyncClient(timeout=15, headers=self.headers) as client:
            current = await self.list_rules(client)
            ours = [
                item for item in current
                if str(item.get("tag") or "").startswith(self.tag_prefix)
            ]
            desired_values = set(self.rules)
            current_values = {str(item.get("value") or "") for item in ours}

            stale_ids = [
                str(item.get("id"))
                for item in ours
                if str(item.get("value") or "") not in desired_values and item.get("id")
            ]
            if stale_ids:
                response = await client.post(
                    X_STREAM_RULES_URL,
                    json={"delete": {"ids": stale_ids}},
                )
                response.raise_for_status()

            additions = [rule for rule in self.rules if rule not in current_values]
            if additions:
                payload = {
                    "add": [
                        {"value": rule, "tag": f"{self.tag_prefix}{index + 1}"}
                        for index, rule in enumerate(additions)
                    ]
                }
                response = await client.post(X_STREAM_RULES_URL, json=payload)
                response.raise_for_status()

            return await self.list_rules(client)

    async def stream(self):
        params = {
            "tweet.fields": "created_at,author_id,entities",
            "expansions": "author_id",
            "user.fields": "username",
        }
        timeout = httpx.Timeout(connect=15, read=None, write=15, pool=15)
        backoff = self.reconnect_seconds

        while True:
            try:
                async with httpx.AsyncClient(
                    timeout=timeout,
                    headers=self.headers,
                    follow_redirects=True,
                ) as client:
                    async with client.stream("GET", X_STREAM_URL, params=params) as response:
                        response.raise_for_status()
                        backoff = self.reconnect_seconds

                        async for line in response.aiter_lines():
                            if not line.strip():
                                # X sends blank keep-alive lines while idle.
                                continue

                            try:
                                payload = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if not isinstance(payload, dict):
                                continue

                            row = payload.get("data")
                            if not isinstance(row, dict):
                                continue

                            post = x_row_to_source_post(row, _username_map(payload))
                            if post is not None:
                                yield post

            except httpx.HTTPStatusError as exc:
                # Auth/payment failures require user action instead of an endless loop.
                if exc.response.status_code in {401, 402, 403}:
                    raise
                if exc.response.status_code == 429:
                    backoff = min(max(backoff * 2, 60), 900)
                else:
                    backoff = min(max(backoff * 2, 5), 300)
                await asyncio.sleep(backoff)
            except httpx.HTTPError:
                backoff = min(max(backoff * 2, 5), 300)
                await asyncio.sleep(backoff)
