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
            # Instagram providers sometimes return seconds, sometimes milliseconds.
            raw_number = float(value)
            if raw_number > 10_000_000_000:
                raw_number /= 1000.0
            return datetime.fromtimestamp(raw_number, tz=timezone.utc)
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


def instagram_apify_row_to_source_post(row: dict) -> SourcePost | None:
    url = str(row.get("url") or row.get("postUrl") or row.get("inputUrl") or "").strip()
    caption = str(row.get("caption") or row.get("text") or row.get("description") or "").strip()
    hashtags = row.get("hashtags")
    hashtag_text = ""
    if isinstance(hashtags, list):
        hashtag_text = " ".join(str(tag) for tag in hashtags if str(tag).strip())

    username = str(row.get("username") or row.get("ownerUsername") or "").strip()
    published = _parse_datetime(
        row.get("publishedAt")
        or row.get("timestamp")
        or row.get("takenAt")
        or row.get("taken_at")
    )

    if not url and not caption:
        return None

    text = "\n".join(part for part in (caption, hashtag_text) if part)
    source = f"instagram:search:@{username}" if username else "instagram:search"
    return SourcePost(source=source, text=text, url=url or None, created_at=published)


class ApifyInstagramWatcher(BaseWatcher):
    name = "instagram"

    def __init__(
        self,
        queries: list[str],
        *,
        actor_id: str,
        interval_seconds: int = 900,
        lookback_minutes: int = 17,
        max_results: int = 10,
        content_type: str = "posts_and_reels",
        search_coverage: str = "efficient",
        hashtag_feed_type: str = "recent",
        timeout_seconds: float = 150.0,
        max_run_cost_usd: float = 0.10,
    ) -> None:
        self.queries = [query.strip() for query in queries if query.strip()]
        if not self.queries:
            raise ValueError("At least one Instagram query is required")
        self.actor_id = actor_id
        self.interval_seconds = max(60, int(interval_seconds))
        self.lookback_minutes = max(1, int(lookback_minutes))
        self.max_results = max(1, int(max_results))
        self.content_type = content_type
        self.search_coverage = search_coverage
        self.hashtag_feed_type = hashtag_feed_type
        self.client = ApifyClient(
            get_apify_token,
            timeout_seconds=timeout_seconds,
            max_run_cost_usd=max_run_cost_usd,
        )
        self._seen: set[str] = set()

    def build_input(self, query: str) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.lookback_minutes)
        today = datetime.now(timezone.utc).date().isoformat()
        return {
            "searchQuery": query,
            "resultsLimit": self.max_results,
            "contentType": self.content_type,
            "searchCoverage": self.search_coverage,
            "hashtagFeedType": self.hashtag_feed_type,
            "oldestPostDate": cutoff.date().isoformat(),
            "newestPostDate": today,
        }

    async def fetch(self) -> list[SourcePost]:
        activity("source_poll_started", source="Instagram")
        posts: list[SourcePost] = []

        for query in self.queries:
            rows = await self.client.run_actor(
                self.actor_id,
                self.build_input(query),
                source="Instagram",
                max_items=self.max_results,
            )
            for row in rows:
                post = instagram_apify_row_to_source_post(row)
                if post is None or not is_recent_timestamp(post.created_at, self.lookback_minutes):
                    continue
                key = post.url or f"{post.created_at}:{post.text[:120]}"
                if key in self._seen:
                    continue
                self._seen.add(key)
                posts.append(post)

        posts.sort(key=lambda post: post.created_at or datetime.min.replace(tzinfo=timezone.utc))
        activity("source_poll_completed", source="Instagram", posts_found=len(posts))
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
                    source="Instagram",
                    http_status=exc.response.status_code,
                    retry_in_seconds=backoff,
                    level="ERROR",
                )
                await asyncio.sleep(backoff)
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                backoff = min(max(backoff * 2, 60), 600)
                activity(
                    "source_poll_failed",
                    source="Instagram",
                    error_type=type(exc).__name__,
                    retry_in_seconds=backoff,
                    level="ERROR",
                )
                await asyncio.sleep(backoff)
