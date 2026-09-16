from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from app.core.activity_log import activity
from app.services.apify import ApifyClient
from app.services.runtime_secrets import get_apify_token
from app.watchers.base import BaseWatcher, SourcePost
from app.watchers.freshness import is_recent_timestamp


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def facebook_apify_row_to_source_post(row: dict) -> SourcePost | None:
    url = str(row.get("url") or row.get("postUrl") or row.get("permalink") or "").strip()
    text = str(row.get("postText") or row.get("text") or row.get("message") or "").strip()
    published = _parse_datetime(
        row.get("publishedAt") or row.get("timestamp") or row.get("createdAt")
    )

    author = row.get("author")
    author_name = ""
    if isinstance(author, dict):
        author_name = str(author.get("name") or author.get("username") or "").strip()
    elif isinstance(author, str):
        author_name = author.strip()

    if not url and not text:
        return None

    source = "facebook:search"
    if author_name:
        source += f":{author_name}"

    return SourcePost(source=source, text=text, url=url or None, created_at=published)


class ApifyFacebookWatcher(BaseWatcher):
    name = "facebook"

    def __init__(
        self,
        queries: list[str],
        *,
        actor_id: str,
        interval_seconds: int = 600,
        lookback_minutes: int = 12,
        max_results: int = 10,
        page_delay_ms: int = 800,
        timeout_seconds: float = 150.0,
        max_run_cost_usd: float = 0.10,
    ) -> None:
        self.queries = [query.strip() for query in queries if query.strip()]
        if not self.queries:
            raise ValueError("At least one Facebook query is required")
        self.actor_id = actor_id
        self.interval_seconds = max(60, int(interval_seconds))
        self.lookback_minutes = max(1, int(lookback_minutes))
        self.max_results = max(1, int(max_results))
        self.page_delay_ms = max(0, int(page_delay_ms))
        self.client = ApifyClient(
            get_apify_token,
            timeout_seconds=timeout_seconds,
            max_run_cost_usd=max_run_cost_usd,
        )
        self._seen: set[str] = set()

    def build_input(self, query: str) -> dict:
        # Facebook's Actor accepts a calendar-day lower bound. Minute-level freshness
        # is enforced again locally after the Actor returns results.
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.lookback_minutes)
        return {
            "searchType": "posts",
            "searchQueries": [query],
            "onlyPostsNewerThan": cutoff.date().isoformat(),
            "maxItems": self.max_results,
            "pageDelayMs": self.page_delay_ms,
        }

    async def fetch(self) -> list[SourcePost]:
        activity("source_poll_started", source="Facebook")
        posts: list[SourcePost] = []

        for query in self.queries:
            rows = await self.client.run_actor(
                self.actor_id,
                self.build_input(query),
                source="Facebook",
                max_items=self.max_results,
            )
            for row in rows:
                post = facebook_apify_row_to_source_post(row)
                if post is None or not is_recent_timestamp(post.created_at, self.lookback_minutes):
                    continue
                key = post.url or f"{post.created_at}:{post.text[:120]}"
                if key in self._seen:
                    continue
                self._seen.add(key)
                posts.append(post)

        posts.sort(key=lambda post: post.created_at or datetime.min.replace(tzinfo=timezone.utc))
        activity("source_poll_completed", source="Facebook", posts_found=len(posts))
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
                if exc.response.status_code == 402:
                    backoff = max(self.interval_seconds, 900)
                elif exc.response.status_code == 429:
                    backoff = min(max(backoff * 2, 60), 900)
                else:
                    backoff = min(max(backoff, 60), 600)
                activity(
                    "source_poll_failed",
                    source="Facebook",
                    http_status=exc.response.status_code,
                    retry_in_seconds=backoff,
                    level="ERROR",
                )
                await asyncio.sleep(backoff)
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                backoff = min(max(backoff * 2, 60), 600)
                activity(
                    "source_poll_failed",
                    source="Facebook",
                    error_type=type(exc).__name__,
                    retry_in_seconds=backoff,
                    level="ERROR",
                )
                await asyncio.sleep(backoff)
