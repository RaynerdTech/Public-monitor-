from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.config import SOURCE_DIAGNOSTIC_SAMPLES
from app.core.activity_log import activity
from app.core.extractor import extract_referral_links_from_data
from app.services.apify import ApifyClient
from app.services.runtime_secrets import get_apify_token
from app.watchers.base import BaseWatcher, SourcePost
from app.watchers.candidates import PollDiagnostics, evaluate_candidate


_RELATIVE_TIME_RE = re.compile(
    r"^(?:about\s+)?(\d+)\s*(second|sec|minute|min|hour|hr|day|week)s?\s*(?:ago)?$",
    re.IGNORECASE,
)
_RELATIVE_UNIT_SECONDS = {
    "second": 1, "sec": 1,
    "minute": 60, "min": 60,
    "hour": 3600, "hr": 3600,
    "day": 86400,
    "week": 604800,
}


def _parse_relative_time(raw: str, now: datetime) -> datetime | None:
    """Facebook search results expose relative times such as '2 minutes ago'."""
    text = raw.strip().lower()
    if text in {"just now", "now", "a moment ago"}:
        return now
    match = _RELATIVE_TIME_RE.match(text)
    if not match:
        return None
    amount = int(match.group(1))
    unit_seconds = _RELATIVE_UNIT_SECONDS.get(match.group(2).lower())
    if unit_seconds is None:
        return None
    return now - timedelta(seconds=amount * unit_seconds)


def _parse_datetime(value: object, *, now: datetime | None = None) -> datetime | None:
    if value is None:
        return None
    current = now or datetime.now(timezone.utc)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            number = float(value)
            # Some Actors emit milliseconds rather than seconds.
            if number > 10_000_000_000:
                number /= 1000.0
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        return _parse_datetime(int(raw), now=current)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return _parse_relative_time(raw, current)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# memo23~facebook-search-scraper is a *search results* scraper: depending on the
# result row it may expose an epoch, an ISO string, or a relative label, under
# several different key names. Missing all of them used to silently zero out the
# poll, because an unmapped timestamp was treated as "not recent".
_FACEBOOK_TIME_KEYS = (
    "publishedAt", "published_at", "timestamp", "createdAt", "created_at",
    "publishTime", "publish_time", "creationTime", "creation_time",
    "postedAt", "posted_at", "taken_at", "time", "date", "relativeTime",
)


def _facebook_published_at(row: dict) -> datetime | None:
    for key in _FACEBOOK_TIME_KEYS:
        if key not in row:
            continue
        parsed = _parse_datetime(row.get(key))
        if parsed is not None:
            return parsed
    nested = row.get("post")
    if isinstance(nested, dict):
        for key in _FACEBOOK_TIME_KEYS:
            parsed = _parse_datetime(nested.get(key))
            if parsed is not None:
                return parsed
    return None


def facebook_apify_row_to_source_post(row: dict) -> SourcePost | None:
    url = str(
        row.get("url")
        or row.get("postUrl")
        or row.get("permalink")
        or row.get("link")
        or ""
    ).strip()
    text = str(
        row.get("postText")
        or row.get("text")
        or row.get("message")
        or row.get("content")
        or row.get("caption")
        or ""
    ).strip()
    nested_referrals = extract_referral_links_from_data(row)
    if nested_referrals:
        text = "\n".join(part for part in [text, *nested_referrals] if part)
    published = _facebook_published_at(row)

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
        max_post_age_minutes: int = 360,
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
        self.max_post_age_minutes = max(1, int(max_post_age_minutes))
        self.max_results = max(1, int(max_results))
        self.page_delay_ms = max(0, int(page_delay_ms))
        self.client = ApifyClient(
            get_apify_token,
            timeout_seconds=timeout_seconds,
            max_run_cost_usd=max_run_cost_usd,
        )
        self._seen: set[str] = set()

    def build_input(self, query: str) -> dict:
        normalized = self.actor_id.replace("/", "~").strip("~")

        # SilentFlow exposes exactly what this monitor needs: native Facebook
        # recent-post sorting plus a date range. Keep the date window broader
        # than the minute-level max-age gate so timezone/day boundaries cannot
        # hide a fresh post; final freshness is still enforced locally.
        if normalized == "silentflow~facebook-search-scraper":
            now = datetime.now(timezone.utc)
            return {
                "query": query,
                "search_type": "posts",
                "max_posts": self.max_results,
                "recent_posts": True,
                "start_date": (now - timedelta(days=1)).date().isoformat(),
                "end_date": (now + timedelta(days=1)).date().isoformat(),
            }

        # Backwards compatibility for deployments that deliberately keep the
        # older memo23 Actor. It has no native recency/date controls.
        return {
            "searchType": "posts",
            "searchQueries": [query],
            "maxItems": self.max_results,
            "pageDelayMs": self.page_delay_ms,
            "proxy": {"useApifyProxy": True},
        }

    async def fetch(self) -> list[SourcePost]:
        activity(
            "source_poll_started",
            source="Facebook",
            queries=self.queries,
            actor=self.actor_id,
            lookback_minutes=self.lookback_minutes,
            max_post_age_minutes=self.max_post_age_minutes,
        )
        posts: list[SourcePost] = []
        diagnostics = PollDiagnostics("Facebook", sample_limit=SOURCE_DIAGNOSTIC_SAMPLES)
        rows_without_timestamp = 0

        for query in self.queries:
            rows = await self.client.run_actor(
                self.actor_id,
                self.build_input(query),
                source="Facebook",
                max_items=self.max_results,
            )
            diagnostics.record_raw(len(rows))
            for row in rows:
                post = facebook_apify_row_to_source_post(row)
                if post is not None and post.created_at is None:
                    rows_without_timestamp += 1
                key = ""
                if post is not None:
                    key = post.url or f"{post.created_at}:{post.text[:120]}"
                    if key in self._seen:
                        diagnostics.record_duplicate(key)
                        continue
                verdict = evaluate_candidate(
                    post,
                    max_post_age_minutes=self.max_post_age_minutes,
                    lookback_minutes=self.lookback_minutes,
                )
                if diagnostics.record(verdict, post, row_id=key) and post is not None:
                    self._seen.add(key)
                    posts.append(post)

        posts.sort(key=lambda post: post.created_at or datetime.min.replace(tzinfo=timezone.utc))
        diagnostics.completed(
            posts_found=len(posts),
            rows_without_timestamp=rows_without_timestamp,
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
