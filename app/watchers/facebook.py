from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app.core.activity_log import activity
from app.services.scrape_creators import ScrapeCreatorsClient
from app.watchers.base import BaseWatcher, SourcePost
from app.watchers.freshness import is_recent_timestamp


FACEBOOK_PAGE_POSTS_PATH = "/v1/facebook/profile/posts"
FACEBOOK_GROUP_POSTS_PATH = "/v1/facebook/group/posts"


@dataclass(frozen=True)
class FacebookSource:
    kind: str
    url: str


def parse_facebook_source(value: str) -> FacebookSource:
    raw = value.strip()
    if not raw:
        raise ValueError("Facebook source cannot be blank")

    lowered = raw.lower()
    if lowered.startswith("page:"):
        return FacebookSource("page", raw[5:].strip())
    if lowered.startswith("group:"):
        return FacebookSource("group", raw[6:].strip())
    if "/groups/" in lowered:
        return FacebookSource("group", raw)
    return FacebookSource("page", raw)


def parse_facebook_timestamp(value: object) -> datetime | None:
    try:
        timestamp = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def facebook_row_to_source_post(row: dict, *, kind: str) -> SourcePost | None:
    row_id = str(row.get("id") or "").strip()
    if not row_id:
        return None

    author = row.get("author") if isinstance(row.get("author"), dict) else {}
    author_name = str(author.get("name") or author.get("short_name") or "").strip()
    text = str(row.get("text") or row.get("description") or "").strip()

    parts = [text] if text else []
    comments = row.get("topComments")
    if isinstance(comments, list):
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            comment_text = str(comment.get("text") or "").strip()
            if comment_text:
                parts.append(comment_text)

    source_url = str(row.get("url") or row.get("permalink") or "").strip() or None
    label = f"facebook:{kind}"
    if author_name:
        label += f":{author_name}"

    return SourcePost(
        source=label,
        text="\n".join(dict.fromkeys(parts)),
        url=source_url,
        created_at=parse_facebook_timestamp(row.get("publishTime") or row.get("created_time")),
    )


class FacebookWatcher(BaseWatcher):
    name = "facebook"

    def __init__(
        self,
        api_key: str,
        sources: list[str],
        *,
        interval_seconds: int = 600,
        lookback_minutes: int = 12,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.sources = [parse_facebook_source(source) for source in sources if source.strip()]
        if not self.sources:
            raise ValueError("At least one Facebook page or group source is required")
        self.interval_seconds = max(60, int(interval_seconds))
        self.lookback_minutes = max(1, int(lookback_minutes))
        self.client = ScrapeCreatorsClient(api_key, timeout_seconds=timeout_seconds)
        self._seen_ids: set[str] = set()

    async def _fetch_source(self, source: FacebookSource) -> list[dict]:
        if source.kind == "group":
            path = FACEBOOK_GROUP_POSTS_PATH
            params: dict[str, object] = {"url": source.url, "sort_by": "CHRONOLOGICAL"}
        else:
            path = FACEBOOK_PAGE_POSTS_PATH
            params = {"url": source.url}

        result = await self.client.get(path, params=params, source="Facebook")
        rows = result.payload.get("posts", [])
        return rows if isinstance(rows, list) else []

    async def fetch(self) -> list[SourcePost]:
        activity("source_poll_started", source="Facebook")
        posts: list[SourcePost] = []

        for source in self.sources:
            for row in await self._fetch_source(source):
                if not isinstance(row, dict):
                    continue
                row_id = str(row.get("id") or "").strip()
                dedupe_key = f"{source.kind}:{source.url}:{row_id}"
                if not row_id or dedupe_key in self._seen_ids:
                    continue
                self._seen_ids.add(dedupe_key)
                post = facebook_row_to_source_post(row, kind=source.kind)
                if post is not None and is_recent_timestamp(
                    post.created_at, self.lookback_minutes
                ):
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
            except (httpx.HTTPError, RuntimeError) as exc:
                backoff = min(max(backoff * 2, 60), 600)
                activity(
                    "source_poll_failed",
                    source="Facebook",
                    error_type=type(exc).__name__,
                    retry_in_seconds=backoff,
                    level="ERROR",
                )
                await asyncio.sleep(backoff)
