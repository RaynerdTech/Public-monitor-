from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from app.config import SOURCE_DIAGNOSTIC_SAMPLES
from app.core.activity_log import activity
from app.core.extractor import extract_referral_links_from_data
from app.services.scrape_creators import ScrapeCreatorsClient
from app.watchers.base import BaseWatcher, SourcePost
from app.watchers.candidates import PollDiagnostics, evaluate_candidate
from app.watchers.reddit import parse_reddit_timestamp, reddit_row_to_source_post


REDDIT_SEARCH_PATH = "/v1/reddit/search"


class ScrapeCreatorsRedditWatcher(BaseWatcher):
    name = "reddit"

    def __init__(
        self,
        api_key: str,
        queries: list[str],
        *,
        interval_seconds: int = 300,
        search_filter: str = "posts",
        timeframe: str = "day",
        lookback_minutes: int = 7,
        max_post_age_minutes: int = 180,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.queries = [query.strip() for query in queries if query.strip()]
        if not self.queries:
            raise ValueError("At least one Reddit search query is required")
        if search_filter not in {"posts", "comments"}:
            raise ValueError("Reddit search_filter must be posts or comments")
        if timeframe not in {"all", "day", "week", "month", "year"}:
            raise ValueError("Unsupported Reddit timeframe")
        self.interval_seconds = max(30, int(interval_seconds))
        self.search_filter = search_filter
        self.timeframe = timeframe
        self.lookback_minutes = max(1, int(lookback_minutes))
        self.max_post_age_minutes = max(1, int(max_post_age_minutes))
        self.client = ScrapeCreatorsClient(api_key, timeout_seconds=timeout_seconds)
        self._seen_post_ids: set[str] = set()

    async def _search(self, query: str) -> list[dict]:
        result = await self.client.get(
            REDDIT_SEARCH_PATH,
            params={
                "query": query,
                "filter": self.search_filter,
                "sort": "new",
                "timeframe": self.timeframe,
                "trim": False,
            },
            source="Reddit",
        )
        key = "posts" if self.search_filter == "posts" else "comments"
        rows = result.payload.get(key)
        if isinstance(rows, list):
            return rows
        activity(
            "provider_payload_unexpected",
            source="Reddit",
            expected_key=key,
            payload_keys=sorted(result.payload.keys())[:20],
            level="WARNING",
        )
        return []

    @staticmethod
    def _comment_to_source_post(row: dict) -> SourcePost | None:
        comment_id = str(row.get("id") or row.get("name") or "").strip()
        if not comment_id:
            return None
        body = str(row.get("body") or row.get("text") or "").strip()
        permalink = str(row.get("permalink") or "").strip()
        source_url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else (permalink or None)
        subreddit = row.get("subreddit")
        if isinstance(subreddit, dict):
            subreddit = subreddit.get("name")
        author = row.get("author")
        if isinstance(author, dict):
            author = author.get("name")
        source_bits = ["reddit"]
        if subreddit:
            source_bits.append(f"r/{subreddit}")
        if author:
            source_bits.append(f"u/{author}")
        return SourcePost(
            source=":".join(str(bit) for bit in source_bits),
            text=body,
            url=source_url,
            created_at=parse_reddit_timestamp(
                row.get("created_utc") or row.get("created") or row.get("created_at_iso")
            ),
        )

    async def fetch(self) -> list[SourcePost]:
        activity(
            "source_poll_started",
            source="Reddit",
            queries=self.queries,
            timeframe=self.timeframe,
            lookback_minutes=self.lookback_minutes,
            max_post_age_minutes=self.max_post_age_minutes,
        )
        posts: list[SourcePost] = []
        diagnostics = PollDiagnostics("Reddit", sample_limit=SOURCE_DIAGNOSTIC_SAMPLES)

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
                row_id = str(row.get("id") or row.get("name") or row.get("post_id") or "").strip()
                if row_id and row_id in self._seen_post_ids:
                    diagnostics.record_duplicate(row_id)
                    continue
                if row_id:
                    self._seen_post_ids.add(row_id)
                if self.search_filter == "comments":
                    post = self._comment_to_source_post(row)
                else:
                    post = reddit_row_to_source_post(row)
                if post is not None:
                    nested_referrals = extract_referral_links_from_data(row)
                    if nested_referrals:
                        post.text = "\n".join(part for part in [post.text, *nested_referrals] if part)
                verdict = evaluate_candidate(
                    post,
                    max_post_age_minutes=self.max_post_age_minutes,
                    lookback_minutes=self.lookback_minutes,
                )
                if diagnostics.record(verdict, post, row_id=row_id) and post is not None:
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
                    source="Reddit",
                    http_status=exc.response.status_code,
                    retry_in_seconds=backoff,
                    level="ERROR",
                )
                await asyncio.sleep(backoff)
            except (httpx.HTTPError, RuntimeError) as exc:
                backoff = min(max(backoff * 2, 30), 300)
                activity(
                    "source_poll_failed",
                    source="Reddit",
                    error_type=type(exc).__name__,
                    retry_in_seconds=backoff,
                    level="ERROR",
                )
                await asyncio.sleep(backoff)
