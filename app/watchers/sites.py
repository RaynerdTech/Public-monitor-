import asyncio
import html
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

    def _conditional_headers(self, url: str) -> dict[str, str]:
        headers = {
            "User-Agent": "ReferralMonitor/10.3 (+direct feed and sitemap watcher)",
            "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,text/html,*/*",
        }
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

    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response | None:
        try:
            response = await client.get(url, headers=self._conditional_headers(url))
            if response.status_code == 304:
                return response
            response.raise_for_status()
            self._remember_validators(url, response)
            return response
        except httpx.HTTPError:
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

            async def fetch_endpoint(url: str) -> tuple[str, httpx.Response | None]:
                async with semaphore:
                    return url, await self._get(client, url)

            responses = await asyncio.gather(*(fetch_endpoint(url) for url in endpoints))
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
            child_responses = await asyncio.gather(*(fetch_endpoint(url) for url in preferred))
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
            for url, modified in unique_candidates.items():
                if modified is not None:
                    if modified >= cutoff:
                        fresh_pages.append((url, modified))
                    continue
                # Undated sitemap history is bootstrapped silently. Only URLs that
                # appear for the first time after bootstrap are fetched.
                if url not in self._seen_undated_pages:
                    self._seen_undated_pages.add(url)
                    if self._bootstrapped:
                        fresh_pages.append((url, None))

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
            feeds_polled=feeds_polled,
            sitemaps_polled=sitemaps_polled,
            pages_fetched=pages_fetched,
            posts_found=len(posts),
        )
        return posts

    async def stream(self):
        while True:
            for post in await self.fetch():
                yield post
            await asyncio.sleep(self.interval_seconds)
