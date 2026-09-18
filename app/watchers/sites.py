import asyncio
import html
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import httpx

from app.core.activity_log import activity
from app.core.extractor import extract_referral_links
from app.watchers.base import BaseWatcher, SourcePost


_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)


def _host_min_interval_seconds() -> float:
    """Minimum gap between two requests to the same host.

    Reddit's feed endpoints answer 429 when an IP asks again inside roughly two
    seconds (its own headers report x-ratelimit-remaining: 0.0 with reset: 2).
    The watcher previously fired every endpoint through one Semaphore(8), so
    three subreddit feeds left within the same millisecond and all three were
    refused. Requests to different hosts still run in parallel.
    """
    try:
        value = float(os.getenv("WEB_DIRECT_HOST_MIN_INTERVAL_SECONDS", "2.1"))
    except ValueError:
        return 2.1
    return max(0.0, value)


class _HostPacer:
    """Serialize requests per host and keep a minimum gap between them."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._locks: dict[str, asyncio.Lock] = {}
        self._next_allowed: dict[str, float] = {}

    async def wait(self, url: str) -> None:
        if self._min_interval <= 0:
            return
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            earliest = self._next_allowed.get(host, 0.0)
            if now < earliest:
                await asyncio.sleep(earliest - now)
                now = loop.time()
            self._next_allowed[host] = now + self._min_interval


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _parse_timestamp(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        iso = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(iso)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _element_text(node: ET.Element) -> str:
    raw = ET.tostring(node, encoding="unicode", method="html")
    urls = [url.rstrip(".,);]}") for url in _URL_RE.findall(html.unescape(raw))]
    plain = " ".join(html.unescape(_TAG_RE.sub(" ", raw)).split())
    return "\n".join(part for part in (plain, *urls) if part)


def parse_feed_entries(xml_text: str, feed_url: str) -> list[SourcePost]:
    """Parse RSS/Atom entries while preserving referral URLs in href attributes."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    source_name = urlparse(feed_url).netloc or "website"
    posts: list[SourcePost] = []
    for entry in root.iter():
        if _local_name(entry.tag) not in {"item", "entry"}:
            continue
        link = ""
        published = ""
        for child in list(entry):
            name = _local_name(child.tag)
            if name == "link" and not link:
                link = str(child.attrib.get("href") or "".join(child.itertext())).strip()
            elif name in {"pubdate", "published", "updated", "date"} and not published:
                published = " ".join(child.itertext()).strip()
        posts.append(
            SourcePost(
                source=f"web-feed:{source_name}",
                text=_element_text(entry),
                url=urljoin(feed_url, link) if link else feed_url,
                created_at=_parse_timestamp(published),
            )
        )
    return posts


def parse_sitemap_entries(xml_text: str) -> tuple[list[str], list[tuple[str, datetime | None]]]:
    """Return child sitemaps and page URLs from a sitemap document."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], []

    child_sitemaps: list[str] = []
    pages: list[tuple[str, datetime | None]] = []
    root_name = _local_name(root.tag)
    for node in list(root):
        location = ""
        last_modified = ""
        for child in list(node):
            name = _local_name(child.tag)
            if name == "loc":
                location = " ".join(child.itertext()).strip()
            elif name == "lastmod":
                last_modified = " ".join(child.itertext()).strip()
        if not location:
            continue
        if root_name == "sitemapindex":
            child_sitemaps.append(location)
        elif root_name == "urlset":
            pages.append((location, _parse_timestamp(last_modified)))
    return child_sitemaps, pages


def _source_endpoints(source: str) -> list[str]:
    value = source.strip()
    if not value:
        return []
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    if not parsed.netloc:
        return []
    if parsed.path not in {"", "/"} or parsed.query:
        return [value]

    base = f"{parsed.scheme}://{parsed.netloc}"
    # Covers WordPress core/Yoast, Shopify's default blog, and generic sitemaps.
    return [
        f"{base}/feed/",
        f"{base}/wp-sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/sitemap.xml",
        f"{base}/blogs/news.atom",
    ]


class DirectWebsiteWatcher(BaseWatcher):
    """Poll feeds and sitemaps for known sites without waiting for web indexing."""

    def __init__(
        self,
        sources: list[str],
        *,
        interval_seconds: int = 120,
        lookback_minutes: int = 180,
        timeout_seconds: int = 15,
        max_pages_per_poll: int = 50,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.sources = [source.strip() for source in sources if source.strip()]
        self.interval_seconds = max(30, int(interval_seconds))
        self.lookback_minutes = max(5, int(lookback_minutes))
        self.timeout_seconds = max(3, int(timeout_seconds))
        self.max_pages_per_poll = max(1, int(max_pages_per_poll))
        self._status = status_callback or (lambda _message: None)
        self._validators: dict[str, dict[str, str]] = {}
        self._bootstrapped = False
        self._seen_undated_pages: set[str] = set()
        self._seen_referrals_by_url: dict[str, frozenset[str]] = {}
        self._pacer = _HostPacer(_host_min_interval_seconds())

    def _conditional_headers(self, url: str, *, conditional: bool = True) -> dict[str, str]:
        headers = {
            "User-Agent": "ReferralMonitor/10.7 (+direct feed and sitemap watcher)",
            "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,text/html,*/*",
        }
        if conditional:
            headers.update(self._validators.get(url, {}))
        return headers

    def _remember_validators(self, url: str, response: httpx.Response) -> None:
        values: dict[str, str] = {}
        if response.headers.get("etag"):
            values["If-None-Match"] = response.headers["etag"]
        if response.headers.get("last-modified"):
            values["If-Modified-Since"] = response.headers["last-modified"]
        if values:
            self._validators[url] = values

    async def _paced_get(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        conditional: bool,
    ) -> httpx.Response:
        await self._pacer.wait(url)
        return await client.get(
            url, headers=self._conditional_headers(url, conditional=conditional)
        )

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float:
        raw = (response.headers.get("retry-after") or "").strip()
        try:
            seconds = float(raw)
        except ValueError:
            seconds = 0.0
        return min(30.0, max(2.5, seconds))

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        conditional: bool = True,
    ) -> httpx.Response | None:
        """Fetch one URL.

        Discovery documents (feeds and sitemaps) are fetched unconditionally.
        Sending If-None-Match/If-Modified-Since for them meant a cached 304 from
        the origin or its CDN skipped parsing entirely, so the sitemap was
        counted as polled while contributing zero page candidates - exactly the
        "sitemaps_polled: 7, pages_fetched: 0" symptom. Conditional requests are
        still used for the much larger page bodies, where they actually save
        bandwidth.
        """
        try:
            response = await self._paced_get(client, url, conditional=conditional)
            if response.status_code == 429:
                delay = self._retry_after_seconds(response)
                activity(
                    "web_direct_rate_limited",
                    source="Web / Direct",
                    target_url=url,
                    retry_after_seconds=round(delay, 2),
                    level="INFO",
                )
                await asyncio.sleep(delay)
                response = await self._paced_get(client, url, conditional=conditional)
            if response.status_code == 304:
                return response
            response.raise_for_status()
            if conditional:
                self._remember_validators(url, response)
            return response
        except httpx.HTTPError as exc:
            status_code: int | None = None
            body_snippet: str | None = None
            retry_after: str | None = None
            response = getattr(exc, "response", None)
            if response is not None:
                status_code = response.status_code
                retry_after = response.headers.get("retry-after")
                try:
                    body_snippet = " ".join(response.text.split())[:300]
                except (UnicodeDecodeError, httpx.ResponseNotRead):
                    body_snippet = None
            activity(
                "web_direct_fetch_failed",
                source="Web / Direct",
                target_url=url,
                error_type=type(exc).__name__,
                status_code=status_code,
                retry_after=retry_after,
                body_snippet=body_snippet,
                level="WARNING",
            )
            return None

    def _append_if_new(self, posts: list[SourcePost], post: SourcePost) -> bool:
        referrals = frozenset(extract_referral_links(post.text))
        if not referrals:
            return False
        key = post.url or post.source
        if self._seen_referrals_by_url.get(key) == referrals:
            return False
        self._seen_referrals_by_url[key] = referrals
        posts.append(post)
        return True

    async def fetch(self) -> list[SourcePost]:
        if not self.sources:
            activity("source_poll_skipped", source="Web / Direct", reason="not_configured")
            return []

        activity("source_poll_started", source="Web / Direct")
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.lookback_minutes)
        endpoints = list(dict.fromkeys(
            endpoint for source in self.sources for endpoint in _source_endpoints(source)
        ))
        timeout = httpx.Timeout(self.timeout_seconds, connect=min(8.0, self.timeout_seconds))
        headers = {"Accept-Encoding": "gzip, deflate"}
        posts: list[SourcePost] = []
        feeds_polled = 0
        sitemaps_polled = 0
        pages_fetched = 0
        child_sitemaps: list[str] = []
        page_candidates: list[tuple[str, datetime | None]] = []

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            semaphore = asyncio.Semaphore(8)

            async def fetch_endpoint(
                url: str, *, conditional: bool = True
            ) -> tuple[str, httpx.Response | None]:
                async with semaphore:
                    return url, await self._get(client, url, conditional=conditional)

            async def fetch_discovery(url: str) -> tuple[str, httpx.Response | None]:
                return await fetch_endpoint(url, conditional=False)

            responses = await asyncio.gather(*(fetch_discovery(url) for url in endpoints))
            for url, response in responses:
                if response is None or response.status_code == 304:
                    continue
                content_type = (response.headers.get("content-type") or "").lower()
                text = response.text[:4_000_000]
                sitemap_children, sitemap_pages = parse_sitemap_entries(text)
                if sitemap_children or sitemap_pages:
                    sitemaps_polled += 1
                    child_sitemaps.extend(sitemap_children)
                    page_candidates.extend(sitemap_pages)
                    continue
                if "xml" in content_type or "rss" in content_type or "atom" in content_type:
                    entries = parse_feed_entries(text, url)
                    if entries:
                        feeds_polled += 1
                    for post in entries:
                        if post.created_at is not None and post.created_at < cutoff:
                            continue
                        self._append_if_new(posts, post)

            # Sitemap indexes are typically small. Fetch one level of child maps,
            # prioritizing blog/post maps and capping work to avoid crawling a store.
            preferred = sorted(
                dict.fromkeys(child_sitemaps),
                key=lambda url: ("post" not in url.lower() and "blog" not in url.lower(), url),
            )[:10]
            child_responses = await asyncio.gather(*(fetch_discovery(url) for url in preferred))
            for _url, response in child_responses:
                if response is None or response.status_code == 304:
                    continue
                sitemaps_polled += 1
                _children, pages = parse_sitemap_entries(response.text[:4_000_000])
                page_candidates.extend(pages)

            unique_candidates: dict[str, datetime | None] = {}
            for url, modified in page_candidates:
                existing = unique_candidates.get(url)
                if existing is None or (modified is not None and modified > existing):
                    unique_candidates[url] = modified

            fresh_pages: list[tuple[str, datetime | None]] = []
            dated_candidates = 0
            dated_too_old = 0
            undated_candidates = 0
            undated_bootstrapped = 0
            undated_new = 0
            newest_lastmod: datetime | None = None
            for url, modified in unique_candidates.items():
                if modified is not None:
                    dated_candidates += 1
                    if newest_lastmod is None or modified > newest_lastmod:
                        newest_lastmod = modified
                    if modified >= cutoff:
                        fresh_pages.append((url, modified))
                    else:
                        dated_too_old += 1
                    continue
                undated_candidates += 1
                # Undated sitemap history is bootstrapped silently. Only URLs that
                # appear for the first time after bootstrap are fetched.
                if url not in self._seen_undated_pages:
                    self._seen_undated_pages.add(url)
                    if self._bootstrapped:
                        undated_new += 1
                        fresh_pages.append((url, None))
                    else:
                        undated_bootstrapped += 1

            fresh_pages.sort(
                key=lambda item: item[1] or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            page_responses = await asyncio.gather(
                *(fetch_endpoint(url) for url, _modified in fresh_pages[: self.max_pages_per_poll])
            )
            for (url, modified), (_response_url, response) in zip(
                fresh_pages[: self.max_pages_per_poll], page_responses
            ):
                if response is None or response.status_code == 304:
                    continue
                pages_fetched += 1
                self._append_if_new(
                    posts,
                    SourcePost(
                        source=f"web-direct:{urlparse(url).netloc or 'website'}",
                        text=response.text[:1_500_000],
                        url=url,
                        created_at=modified,
                    ),
                )

        self._bootstrapped = True

        posts.sort(key=lambda post: post.created_at or datetime.now(timezone.utc))
        activity(
            "source_poll_completed",
            source="Web / Direct",
            configured_sources=len(self.sources),
            endpoints_probed=len(endpoints),
            feeds_polled=feeds_polled,
            sitemaps_polled=sitemaps_polled,
            child_sitemaps_seen=len(set(child_sitemaps)),
            page_candidates=len(unique_candidates),
            dated_candidates=dated_candidates,
            dated_rejected_too_old=dated_too_old,
            undated_candidates=undated_candidates,
            undated_bootstrapped=undated_bootstrapped,
            undated_new=undated_new,
            pages_selected=len(fresh_pages),
            pages_fetched=pages_fetched,
            lookback_minutes=self.lookback_minutes,
            newest_sitemap_lastmod=newest_lastmod,
            posts_found=len(posts),
        )
        return posts

    async def stream(self):
        while True:
            for post in await self.fetch():
                yield post
            await asyncio.sleep(self.interval_seconds)
