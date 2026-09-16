from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from app.core.activity_log import activity
from app.services.scrape_creators import ScrapeCreatorsClient
from app.watchers.base import BaseWatcher, SourcePost
from app.watchers.freshness import is_recent_timestamp


INSTAGRAM_HASHTAG_PATH = "/v1/instagram/search/hashtag"
INSTAGRAM_REELS_PATH = "/v2/instagram/reels/search"


def parse_instagram_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def instagram_row_to_source_post(row: dict, *, discovery: str) -> SourcePost | None:
    row_id = str(row.get("id") or row.get("pk") or row.get("shortcode") or "").strip()
    if not row_id:
        return None

    owner = row.get("owner") if isinstance(row.get("owner"), dict) else {}
    username = str(owner.get("username") or row.get("username") or "").strip()
    caption = row.get("caption")
    if isinstance(caption, dict):
        caption_text = str(caption.get("text") or "").strip()
    else:
        caption_text = str(caption or "").strip()

    parts = [caption_text] if caption_text else []
    comments = row.get("comments")
    if isinstance(comments, list):
        for comment in comments:
            if isinstance(comment, dict):
                text = str(comment.get("text") or "").strip()
                if text:
                    parts.append(text)

    source_url = str(row.get("url") or "").strip() or None
    if source_url is None:
        shortcode = str(row.get("shortcode") or "").strip()
        if shortcode:
            source_url = f"https://www.instagram.com/p/{shortcode}/"

    return SourcePost(
        source=f"instagram:{discovery}:@{username}" if username else f"instagram:{discovery}",
        text="\n".join(dict.fromkeys(parts)),
        url=source_url,
        created_at=parse_instagram_timestamp(
            row.get("taken_at") or row.get("created_at") or row.get("timestamp")
        ),
    )


class InstagramWatcher(BaseWatcher):
    name = "instagram"

    def __init__(
        self,
        api_key: str,
        *,
        hashtags: list[str],
        reels_queries: list[str],
        interval_seconds: int = 900,
        hashtag_date_posted: str = "last-day",
        reels_date_posted: str = "last-week",
        lookback_minutes: int = 17,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.hashtags = [tag.strip().lstrip("#") for tag in hashtags if tag.strip()]
        self.reels_queries = [query.strip() for query in reels_queries if query.strip()]
        if not self.hashtags and not self.reels_queries:
            raise ValueError("Configure at least one Instagram hashtag or Reels query")
        if hashtag_date_posted not in {"last-hour", "last-day", "last-week", "last-month", "last-year"}:
            raise ValueError("Unsupported Instagram hashtag date window")
        if reels_date_posted not in {"last-week", "last-month", "last-year"}:
            raise ValueError("Unsupported Instagram Reels date window")

        self.interval_seconds = max(60, int(interval_seconds))
        self.hashtag_date_posted = hashtag_date_posted
        self.reels_date_posted = reels_date_posted
        self.lookback_minutes = max(1, int(lookback_minutes))
        self.client = ScrapeCreatorsClient(api_key, timeout_seconds=timeout_seconds)
        self._seen_ids: set[str] = set()

    async def _fetch_hashtag(self, hashtag: str) -> list[dict]:
        result = await self.client.get(
            INSTAGRAM_HASHTAG_PATH,
            params={
                "hashtag": hashtag,
                "date_posted": self.hashtag_date_posted,
                "media_type": "all",
            },
            source="Instagram",
        )
        rows = result.payload.get("posts", [])
        return rows if isinstance(rows, list) else []

    async def _fetch_reels(self, query: str) -> list[dict]:
        result = await self.client.get(
            INSTAGRAM_REELS_PATH,
            params={"query": query, "date_posted": self.reels_date_posted, "page": 1},
            source="Instagram",
        )
        rows = result.payload.get("reels", [])
        return rows if isinstance(rows, list) else []

    async def fetch(self) -> list[SourcePost]:
        activity("source_poll_started", source="Instagram")
        posts: list[SourcePost] = []

        for hashtag in self.hashtags:
            for row in await self._fetch_hashtag(hashtag):
                if not isinstance(row, dict):
                    continue
                row_id = str(row.get("id") or row.get("pk") or row.get("shortcode") or "").strip()
                dedupe_key = f"hashtag:{row_id}"
                if not row_id or dedupe_key in self._seen_ids:
                    continue
                self._seen_ids.add(dedupe_key)
                post = instagram_row_to_source_post(row, discovery=f"hashtag:{hashtag}")
                if post is not None and is_recent_timestamp(
                    post.created_at, self.lookback_minutes
                ):
                    posts.append(post)

        for query in self.reels_queries:
            for row in await self._fetch_reels(query):
                if not isinstance(row, dict):
                    continue
                row_id = str(row.get("id") or row.get("pk") or row.get("shortcode") or "").strip()
                dedupe_key = f"reel:{row_id}"
                if not row_id or dedupe_key in self._seen_ids:
                    continue
                self._seen_ids.add(dedupe_key)
                post = instagram_row_to_source_post(row, discovery="reels")
                if post is not None and is_recent_timestamp(
                    post.created_at, self.lookback_minutes
                ):
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
            except (httpx.HTTPError, RuntimeError) as exc:
                backoff = min(max(backoff * 2, 60), 600)
                activity(
                    "source_poll_failed",
                    source="Instagram",
                    error_type=type(exc).__name__,
                    retry_in_seconds=backoff,
                    level="ERROR",
                )
                await asyncio.sleep(backoff)
