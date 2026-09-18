from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from app.config import SOURCE_DIAGNOSTIC_SAMPLES
from app.core.activity_log import activity
from app.services.scrape_creators import ScrapeCreatorsClient
from app.watchers.base import BaseWatcher, SourcePost
from app.watchers.candidates import PollDiagnostics, evaluate_candidate


THREADS_SEARCH_PATH = "/v1/threads/search"


def _parse_timestamp(value: object) -> datetime | None:
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


def _referral_urls_from_nested(value: object) -> list[str]:
    found: list[str] = []

    def walk(item: object) -> None:
        if isinstance(item, dict):
            for nested in item.values():
                walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)
        elif isinstance(item, str) and "claude.ai/referral" in item.lower():
            for token in item.replace("\n", " ").split():
                cleaned = token.strip('"\'()[]{}<>,.;')
                if "claude.ai/referral" in cleaned.lower() and cleaned not in found:
                    found.append(cleaned)

    walk(value)
    return found


def threads_row_to_source_post(row: dict) -> SourcePost | None:
    post_id = str(row.get("id") or row.get("pk") or row.get("code") or "").strip()
    if not post_id:
        return None

    user = row.get("user") if isinstance(row.get("user"), dict) else {}
    username = str(user.get("username") or row.get("username") or "").strip()
    code = str(row.get("code") or "").strip()

    caption = row.get("caption")
    caption_text = ""
    if isinstance(caption, dict):
        caption_text = str(caption.get("text") or "").strip()
    elif isinstance(caption, str):
        caption_text = caption.strip()

    fragments: list[str] = []
    info = row.get("text_post_app_info")
    if isinstance(info, dict):
        text_fragments = info.get("text_fragments")
        if isinstance(text_fragments, dict):
            for fragment in text_fragments.get("fragments") or []:
                if not isinstance(fragment, dict):
                    continue
                for key in ("plaintext", "linkified_web_url"):
                    value = fragment.get(key)
                    if isinstance(value, str) and value.strip():
                        fragments.append(value.strip())

    parts: list[str] = []
    for value in [caption_text, *fragments, *_referral_urls_from_nested(row)]:
        if value and value not in parts:
            parts.append(value)

    if username and code:
        source_url = f"https://www.threads.net/@{username}/post/{code}"
    else:
        source_url = None

    return SourcePost(
        source=f"threads:@{username}" if username else "threads",
        text="\n".join(parts),
        url=source_url,
        created_at=_parse_timestamp(row.get("taken_at") or row.get("timestamp")),
    )


class ScrapeCreatorsThreadsWatcher(BaseWatcher):
    name = "threads"

    def __init__(
        self,
        api_key: str,
        queries: list[str],
        *,
        interval_seconds: int = 300,
        lookback_minutes: int = 7,
        max_post_age_minutes: int = 180,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.queries = [query.strip() for query in queries if query.strip()]
        if not self.queries:
            raise ValueError("At least one Threads search query is required")
        self.interval_seconds = max(30, int(interval_seconds))
        self.lookback_minutes = max(1, int(lookback_minutes))
        self.max_post_age_minutes = max(1, int(max_post_age_minutes))
        self.client = ScrapeCreatorsClient(api_key, timeout_seconds=timeout_seconds)
        self._seen_post_ids: set[str] = set()

    async def _search(self, query: str) -> list[dict]:
        now = datetime.now(timezone.utc)
        # start_date/end_date are calendar dates, not timestamps. Reach back a
        # full day so a post is never missed because the provider's day boundary
        # differs from ours; minute-level filtering happens locally afterwards.
        start = (now - timedelta(minutes=self.lookback_minutes, days=1)).date()
        end = (now + timedelta(days=1)).date()
        result = await self.client.get(
            THREADS_SEARCH_PATH,
            params={
                "query": query,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "trim": "false",
            },
            source="Threads",
        )
        rows = result.payload.get("posts")
        if isinstance(rows, list):
            return rows
        # Surface an unexpected envelope instead of silently reporting zero.
        activity(
            "provider_payload_unexpected",
            source="Threads",
            expected_key="posts",
            payload_keys=sorted(result.payload.keys())[:20],
            level="WARNING",
        )
        return []

    async def fetch(self) -> list[SourcePost]:
        activity(
            "source_poll_started",
            source="Threads",
            queries=self.queries,
            lookback_minutes=self.lookback_minutes,
            max_post_age_minutes=self.max_post_age_minutes,
        )
        posts: list[SourcePost] = []
        diagnostics = PollDiagnostics("Threads", sample_limit=SOURCE_DIAGNOSTIC_SAMPLES)

        for query in self.queries:
            rows = await self._search(query)
            diagnostics.record_raw(len(rows))
            for row in rows:
                if not isinstance(row, dict):
                    diagnostics.record(
                        evaluate_candidate(None, max_post_age_minutes=self.max_post_age_minutes),
                        None,
                    )
                    continue
                post_id = str(row.get("id") or row.get("pk") or row.get("code") or "").strip()
                if post_id and post_id in self._seen_post_ids:
                    diagnostics.record_duplicate(post_id)
                    continue
                if post_id:
                    self._seen_post_ids.add(post_id)
                post = threads_row_to_source_post(row)
                verdict = evaluate_candidate(
                    post,
                    max_post_age_minutes=self.max_post_age_minutes,
                    lookback_minutes=self.lookback_minutes,
                )
                if diagnostics.record(verdict, post, row_id=post_id) and post is not None:
                    posts.append(post)

        posts.sort(key=lambda post: post.created_at or datetime.min.replace(tzinfo=timezone.utc))
        diagnostics.completed(posts_found=len(posts))
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
                    backoff = min(max(backoff, 30), 300)
                activity(
                    "source_poll_failed",
                    source="Threads",
                    http_status=exc.response.status_code,
                    retry_in_seconds=backoff,
                    level="ERROR",
                )
                await asyncio.sleep(backoff)
            except (httpx.HTTPError, RuntimeError) as exc:
                backoff = min(max(backoff * 2, 30), 300)
                activity(
                    "source_poll_failed",
                    source="Threads",
                    error_type=type(exc).__name__,
                    retry_in_seconds=backoff,
                    level="ERROR",
                )
                await asyncio.sleep(backoff)
