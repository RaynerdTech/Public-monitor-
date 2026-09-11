import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from app.core.activity_log import activity
from app.watchers.base import BaseWatcher, SourcePost


YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def parse_youtube_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _google_error_reason(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return ""
    errors = error.get("errors")
    if isinstance(errors, list):
        for item in errors:
            if isinstance(item, dict) and item.get("reason"):
                return str(item["reason"])
    status = error.get("status")
    return str(status or "")


def is_daily_quota_error(exc: httpx.HTTPStatusError) -> bool:
    if exc.response.status_code not in {403, 429}:
        return False
    reason = _google_error_reason(exc.response).lower()
    if reason in {
        "quotaexceeded",
        "dailylimitexceeded",
        "ratelimitexceeded",
        "resource_exhausted",
    }:
        return True
    body = exc.response.text.lower()
    return "quota" in body and ("exceed" in body or "limit" in body)


def seconds_until_youtube_quota_reset(now: datetime | None = None) -> int:
    """Seconds until the next YouTube daily reset (midnight Pacific), plus 2 minutes."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    pacific_now = current.astimezone(PACIFIC_TZ)
    next_day = (pacific_now + timedelta(days=1)).date()
    reset = datetime.combine(next_day, datetime.min.time(), tzinfo=PACIFIC_TZ)
    return max(60, int((reset - pacific_now).total_seconds()) + 120)


def youtube_video_to_source_post(video: dict) -> SourcePost | None:
    video_id = str(video.get("id") or "").strip()
    if not video_id:
        return None

    snippet = video.get("snippet") or {}
    if not isinstance(snippet, dict):
        return None

    title = str(snippet.get("title") or "").strip()
    description = str(snippet.get("description") or "").strip()
    channel_title = str(snippet.get("channelTitle") or "").strip()
    channel_id = str(snippet.get("channelId") or "").strip()

    parts = [part for part in (title, description) if part]
    source_name = channel_title or channel_id or "unknown"

    return SourcePost(
        source=f"youtube:{source_name}",
        text="\n".join(parts),
        url=f"https://www.youtube.com/watch?v={video_id}",
        created_at=parse_youtube_timestamp(snippet.get("publishedAt")),
    )


class YouTubeWatcher(BaseWatcher):
    """Quota-conscious watcher for recent public YouTube videos."""

    def __init__(
        self,
        api_key: str,
        queries: list[str],
        *,
        interval_seconds: int = 1200,
        max_results: int = 50,
        lookback_minutes: int = 30,
        overlap_seconds: int = 90,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.queries = [query.strip() for query in queries if query.strip()]
        self.interval_seconds = max(60, int(interval_seconds))
        self.max_results = min(50, max(1, int(max_results)))
        self.lookback_minutes = max(5, int(lookback_minutes))
        self.overlap_seconds = max(0, int(overlap_seconds))
        self.status_callback = status_callback
        self._seen_video_ids: set[str] = set()
        self._last_search_at: datetime | None = None

    def _status(self, message: str) -> None:
        if self.status_callback:
            self.status_callback(message)

    def _published_after(self, now: datetime) -> datetime:
        if self._last_search_at is None:
            return now - timedelta(minutes=self.lookback_minutes)
        return self._last_search_at - timedelta(seconds=self.overlap_seconds)

    async def _search(self, client: httpx.AsyncClient, query: str, published_after: datetime) -> list[str]:
        params = {
            "part": "snippet",
            "type": "video",
            "order": "date",
            "maxResults": self.max_results,
            "q": query,
            "publishedAfter": _rfc3339(published_after),
            "key": self.api_key,
        }
        response = await client.get(YOUTUBE_SEARCH_URL, params=params)
        response.raise_for_status()
        payload = response.json()

        ids: list[str] = []
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            identifier = item.get("id") or {}
            if not isinstance(identifier, dict):
                continue
            video_id = str(identifier.get("videoId") or "").strip()
            if video_id and video_id not in ids:
                ids.append(video_id)
        return ids

    async def _video_details(self, client: httpx.AsyncClient, video_ids: list[str]) -> list[dict]:
        if not video_ids:
            return []
        params = {
            "part": "snippet",
            "id": ",".join(video_ids[:50]),
            "key": self.api_key,
        }
        response = await client.get(YOUTUBE_VIDEOS_URL, params=params)
        response.raise_for_status()
        payload = response.json()
        return [item for item in (payload.get("items") or []) if isinstance(item, dict)]

    async def fetch(self) -> list[SourcePost]:
        if not self.api_key or not self.queries:
            activity("source_poll_skipped", source="YouTube", reason="not_configured")
            return []

        activity("source_poll_started", source="YouTube")
        now = datetime.now(timezone.utc)
        published_after = self._published_after(now)
        candidate_ids: list[str] = []

        async with httpx.AsyncClient(timeout=20) as client:
            for query in self.queries:
                for video_id in await self._search(client, query, published_after):
                    if video_id not in self._seen_video_ids and video_id not in candidate_ids:
                        candidate_ids.append(video_id)

            details: list[dict] = []
            for offset in range(0, len(candidate_ids), 50):
                details.extend(await self._video_details(client, candidate_ids[offset : offset + 50]))

        self._last_search_at = now

        posts: list[SourcePost] = []
        for video in details:
            video_id = str(video.get("id") or "").strip()
            if not video_id or video_id in self._seen_video_ids:
                continue
            self._seen_video_ids.add(video_id)
            post = youtube_video_to_source_post(video)
            if post is not None:
                posts.append(post)

        posts.sort(key=lambda post: post.created_at or datetime.min.replace(tzinfo=timezone.utc))
        activity(
            "source_poll_completed",
            source="YouTube",
            candidates=len(candidate_ids),
            posts_found=len(posts),
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
                if is_daily_quota_error(exc):
                    sleep_seconds = seconds_until_youtube_quota_reset()
                    reset_at = datetime.now(timezone.utc) + timedelta(seconds=sleep_seconds)
                    self._status(
                        "YouTube daily search quota reached. Sleeping until after the next "
                        f"quota reset, then continuing automatically ({reset_at.isoformat(timespec='minutes')})."
                    )
                    activity(
                        "source_poll_failed",
                        source="YouTube",
                        http_status=exc.response.status_code,
                        retry_in_seconds=sleep_seconds,
                        level="ERROR",
                    )
                    await asyncio.sleep(sleep_seconds)
                    backoff = self.interval_seconds
                    continue

                if exc.response.status_code == 429:
                    backoff = min(max(backoff * 2, 900), 3600)
                elif exc.response.status_code in {400, 401, 403}:
                    # Configuration/auth errors may be fixed while this process is running.
                    # Retry slowly rather than exiting permanently or burning requests.
                    backoff = 21600
                else:
                    backoff = min(max(backoff, 300), 1800)
                self._status(
                    f"YouTube HTTP {exc.response.status_code}. Retrying automatically in {backoff}s."
                )
                activity(
                    "source_poll_failed",
                    source="YouTube",
                    http_status=exc.response.status_code,
                    retry_in_seconds=backoff,
                    level="ERROR",
                )
                await asyncio.sleep(backoff)
            except httpx.HTTPError as exc:
                backoff = min(max(backoff * 2, 300), 1800)
                self._status(f"YouTube network error. Retrying automatically in {backoff}s.")
                activity(
                    "source_poll_failed",
                    source="YouTube",
                    error_type=type(exc).__name__,
                    retry_in_seconds=backoff,
                    level="ERROR",
                )
                await asyncio.sleep(backoff)
