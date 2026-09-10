import asyncio
import html
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx

from app.watchers.base import BaseWatcher, SourcePost


EXA_SEARCH_URL = "https://api.exa.ai/search"


_TAG_RE = re.compile(r"<[^>]+>")


def parse_exa_timestamp(value: object) -> datetime | None:
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


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _plain_text_from_html(value: str) -> str:
    if not value:
        return ""
    return html.unescape(_TAG_RE.sub(" ", value))


def exa_result_to_source_post(result: dict, page_text: str = "") -> SourcePost | None:
    url = str(result.get("url") or "").strip()
    if not url:
        return None

    title = str(result.get("title") or "").strip()
    author = str(result.get("author") or "").strip()
    summary = str(result.get("summary") or "").strip()
    raw_text = str(result.get("text") or "").strip()
    highlights = result.get("highlights") or []
    if not isinstance(highlights, list):
        highlights = []

    pieces = [
        title,
        url,
        author,
        summary,
        raw_text,
        *[str(item) for item in highlights if item],
        page_text,
        _plain_text_from_html(page_text),
    ]
    text = "\n".join(piece for piece in pieces if piece)

    return SourcePost(
        source="web:exa",
        text=text,
        url=url,
        created_at=parse_exa_timestamp(result.get("publishedDate")),
    )


class ExaWebWatcher(BaseWatcher):
    """Fresh public-web discovery through Exa Search.

    Broad Exa discovery is intentionally strict: a result must have a recent
    publication timestamp. Known useful domains should later be monitored
    directly instead of allowing undated/stale pages into broad discovery.
    """

    def __init__(
        self,
        api_key: str,
        queries: list[str],
        *,
        interval_seconds: int = 1200,
        max_results: int = 10,
        lookback_minutes: int = 60,
        search_type: str = "fast",
        page_fetch_timeout_seconds: int = 12,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.queries = [query.strip() for query in queries if query.strip()]
        self.interval_seconds = max(60, interval_seconds)
        self.max_results = min(100, max(1, max_results))
        self.lookback_minutes = max(5, lookback_minutes)
        self.search_type = search_type.strip() or "fast"
        self.page_fetch_timeout_seconds = max(3, page_fetch_timeout_seconds)
        self._seen_urls: set[str] = set()
        self._status = status_callback or (lambda _message: None)

    async def _search(
        self,
        client: httpx.AsyncClient,
        query: str,
        cutoff: datetime,
    ) -> list[dict]:
        # Filter at Exa itself instead of retrieving old results and filtering only
        # after the request. This greatly reduces stale historical candidates.
        payload = {
            "query": query,
            "type": self.search_type,
            "numResults": self.max_results,
            "startPublishedDate": _iso_z(cutoff),
        }
        last_error: httpx.HTTPError | None = None
        for attempt in range(3):
            try:
                response = await client.post(EXA_SEARCH_URL, json=payload)
                response.raise_for_status()
                body = response.json()
                break
            except httpx.HTTPStatusError:
                # HTTP errors (bad key, credits, bad payload, rate limit, etc.) are
                # deterministic enough that the caller should see the real response.
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt == 2:
                    raise
                await asyncio.sleep(1 << attempt)
        else:  # defensive; loop either breaks or raises
            assert last_error is not None
            raise last_error
        rows = body.get("results", []) if isinstance(body, dict) else []
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    async def _fetch_page(self, client: httpx.AsyncClient, url: str) -> str | None:
        """Fetch a candidate page.

        None means the URL is definitively dead (404/410) and must be discarded.
        An empty string means the page could not be read (403/429/network), but the
        URL may still exist. Fresh Exa metadata can still be used in that case.
        """
        try:
            response = await client.get(url)
        except httpx.HTTPError:
            return ""

        if response.status_code in {404, 410}:
            return None
        try:
            response.raise_for_status()
        except httpx.HTTPError:
            return ""

        content_type = (response.headers.get("content-type") or "").lower()
        if content_type and not any(
            marker in content_type
            for marker in ("text/", "application/xhtml", "application/xml", "application/json")
        ):
            return ""
        return response.text[:1_500_000]

    async def fetch(self) -> list[SourcePost]:
        if not self.api_key or not self.queries:
            return []

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=self.lookback_minutes)
        future_tolerance = now + timedelta(minutes=10)
        candidates: list[dict] = []
        candidate_urls: set[str] = set()

        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        # Exa fast search is normally quick, but a fresh-date constrained query can
        # occasionally take longer or hit a transient network timeout. Give the API
        # enough room and let _search retry transient failures automatically.
        exa_timeout = httpx.Timeout(45.0, connect=15.0)
        async with httpx.AsyncClient(timeout=exa_timeout, headers=headers) as exa_client:
            for query in self.queries:
                rows = await self._search(exa_client, query, cutoff)
                for row in rows:
                    url = str(row.get("url") or "").strip()
                    if not url or url in self._seen_urls or url in candidate_urls:
                        continue

                    published_at = parse_exa_timestamp(row.get("publishedDate"))
                    # Broad discovery must be demonstrably recent. Undated pages are
                    # no longer accepted because they are the main source of stale
                    # results. Useful known domains can be promoted to direct watchers.
                    if published_at is None:
                        self._status(f"Skipping undated broad web result: {url}")
                        continue
                    if published_at < cutoff or published_at > future_tolerance:
                        continue

                    candidate_urls.add(url)
                    candidates.append(row)

        page_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/json,text/plain,*/*",
        }
        timeout = httpx.Timeout(self.page_fetch_timeout_seconds)
        posts: list[SourcePost] = []
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=page_headers,
        ) as page_client:
            for row in candidates:
                url = str(row.get("url") or "").strip()
                page_text = await self._fetch_page(page_client, url)
                self._seen_urls.add(url)
                if page_text is None:
                    self._status(f"Skipping dead web result (404/410): {url}")
                    continue
                post = exa_result_to_source_post(row, page_text)
                if post is not None:
                    posts.append(post)

        posts.sort(key=lambda post: post.created_at or now)
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
                code = exc.response.status_code
                if code == 429:
                    backoff = min(max(backoff * 2, 60), 1800)
                elif code in {401, 402, 403}:
                    backoff = 3600
                else:
                    backoff = min(max(backoff, 120), 900)
                self._status(f"Exa web search HTTP {code}. Retrying automatically in {backoff}s.")
                await asyncio.sleep(backoff)
            except httpx.HTTPError:
                backoff = min(max(backoff * 2, 60), 900)
                self._status(f"Exa web search network error. Retrying automatically in {backoff}s.")
                await asyncio.sleep(backoff)
