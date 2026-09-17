import asyncio
import hashlib
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.core.activity_log import activity
from app.core.extractor import extract_referral_links
from app.services.usage_registry import record_count
from app.watchers.base import BaseWatcher, SourcePost


PODCAST_INDEX_BASE_URL = "https://api.podcastindex.org/api/1.0"
PODCAST_INDEX_RECENT_DATA = f"{PODCAST_INDEX_BASE_URL}/recent/data"
PODCAST_INDEX_SEARCH_BYTERM = f"{PODCAST_INDEX_BASE_URL}/search/byterm"
PODCAST_INDEX_EPISODE_BYID = f"{PODCAST_INDEX_BASE_URL}/episodes/byid"
PODCAST_INDEX_PODCAST_BYFEEDID = f"{PODCAST_INDEX_BASE_URL}/podcasts/byfeedid"


_URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)


def podcast_index_auth_headers(
    api_key: str,
    api_secret: str,
    user_agent: str,
    *,
    epoch_seconds: int | None = None,
) -> dict[str, str]:
    stamp = int(time.time()) if epoch_seconds is None else int(epoch_seconds)
    authorization = hashlib.sha1(
        f"{api_key}{api_secret}{stamp}".encode("utf-8")
    ).hexdigest()
    return {
        "User-Agent": user_agent,
        "X-Auth-Key": api_key,
        "X-Auth-Date": str(stamp),
        "Authorization": authorization,
    }


def _epoch_datetime(value: object) -> datetime | None:
    try:
        stamp = int(value)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(stamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _strip_html(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = html.unescape(value)
    # Preserve URLs stored only in attributes such as <a href="...">. A plain
    # tag stripper would otherwise remove the exact referral we need to detect.
    embedded_urls = [url.rstrip(".,);]}") for url in _URL_RE.findall(text)]
    # Podcast descriptions are frequently HTML. ElementTree is too strict for
    # arbitrary fragments, so use a small tag stripper after unescaping.
    out: list[str] = []
    inside = False
    for char in text:
        if char == "<":
            inside = True
            out.append(" ")
        elif char == ">":
            inside = False
            out.append(" ")
        elif not inside:
            out.append(char)
    plain = " ".join("".join(out).split())
    return "\n".join(part for part in (plain, *embedded_urls) if part)


def _episode_transcript_urls(episode: dict) -> list[str]:
    candidates: list[object] = [episode.get("transcriptUrl")]
    transcripts = episode.get("transcripts")
    if isinstance(transcripts, list):
        for transcript in transcripts:
            if isinstance(transcript, str):
                candidates.append(transcript)
            elif isinstance(transcript, dict):
                candidates.append(transcript.get("url"))

    urls: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        url = str(candidate or "").strip()
        if url.lower().startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def podcast_index_episode_to_post(episode: dict) -> SourcePost | None:
    episode_id = str(episode.get("id") or "").strip()
    if not episode_id:
        return None

    title = str(episode.get("title") or "").strip()
    description = _strip_html(episode.get("description"))
    feed_title = str(episode.get("feedTitle") or "").strip() or "unknown"
    link = str(episode.get("link") or "").strip()
    enclosure = str(episode.get("enclosureUrl") or "").strip()
    transcript_urls = _episode_transcript_urls(episode)

    return SourcePost(
        source=f"podcast:{feed_title}",
        text="\n".join(
            part for part in (title, description, link, *transcript_urls) if part
        ),
        url=link or enclosure or None,
        created_at=_epoch_datetime(episode.get("datePublished")),
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _node_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def _find_child_text(node: ET.Element, names: set[str]) -> str:
    for child in list(node):
        if _local_name(child.tag) in names:
            text = _node_text(child)
            if text:
                return text
    return ""


def _parse_feed_timestamp(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_rss_entries(xml_text: str, feed_url: str) -> list[tuple[str, SourcePost]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    feed_title = "unknown"
    channel = next((n for n in root.iter() if _local_name(n.tag) == "channel"), None)
    if channel is not None:
        feed_title = _find_child_text(channel, {"title"}) or feed_title
    elif _local_name(root.tag) == "feed":
        feed_title = _find_child_text(root, {"title"}) or feed_title

    entries: list[tuple[str, SourcePost]] = []
    for entry in root.iter():
        if _local_name(entry.tag) not in {"item", "entry"}:
            continue

        title = _find_child_text(entry, {"title"})
        description_parts: list[str] = []
        link = ""
        guid = ""
        published = ""

        for child in list(entry):
            name = _local_name(child.tag)
            if name in {"description", "summary", "content", "encoded"}:
                value = _node_text(child)
                if value:
                    description_parts.append(_strip_html(value))
            elif name == "link" and not link:
                link = (child.attrib.get("href") or _node_text(child)).strip()
            elif name in {"guid", "id"} and not guid:
                guid = _node_text(child).strip()
            elif name in {"pubdate", "published", "updated"} and not published:
                published = _node_text(child).strip()

        text = "\n".join(part for part in [title, *description_parts, link] if part)
        identity = guid or link or f"{feed_url}|{title}|{published}"
        entries.append(
            (
                identity,
                SourcePost(
                    source=f"podcast-rss:{feed_title}",
                    text=text,
                    url=link or feed_url,
                    created_at=_parse_feed_timestamp(published),
                ),
            )
        )
    return entries


class PodcastWatcher(BaseWatcher):
    """Podcast Index discovery + direct RSS monitoring for referral links."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        user_agent: str,
        *,
        interval_seconds: int = 120,
        recent_max: int = 1000,
        lookback_minutes: int = 30,
        discovery_queries: list[str] | None = None,
        discovery_interval_seconds: int = 21600,
        rss_interval_seconds: int = 120,
        rss_max_feeds: int = 100,
        source_registry_path: str = "podcast_sources.json",
        transcript_retries: int = 2,
        transcript_failure_log_seconds: int = 900,
        status_callback=None,
    ) -> None:
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.user_agent = user_agent.strip() or "ReferralMonitor/0.9"
        self.interval_seconds = max(30, int(interval_seconds))
        self.recent_max = max(1, int(recent_max))
        self.lookback_minutes = max(5, int(lookback_minutes))
        self.discovery_queries = [
            q.strip() for q in (discovery_queries or []) if q.strip()
        ]
        self.discovery_interval_seconds = max(300, int(discovery_interval_seconds))
        self.rss_interval_seconds = max(30, int(rss_interval_seconds))
        self.rss_max_feeds = max(1, int(rss_max_feeds))
        self.source_registry_path = Path(source_registry_path)
        self.transcript_retries = max(1, int(transcript_retries))
        self.transcript_failure_log_seconds = max(
            60, int(transcript_failure_log_seconds)
        )
        self.status_callback = status_callback or (lambda _message: None)

        self._since = int(time.time()) - self.lookback_minutes * 60
        self._last_discovery_at = 0.0
        self._last_rss_at = 0.0
        self._seen_episode_ids: set[str] = set()
        self._seen_rss_entries: set[str] = set()
        self._rss_bootstrapped_feeds: set[str] = set()
        self._rss_validators: dict[str, dict[str, str]] = {}
        self._last_index_scanned = 0
        self._last_index_relevant = 0
        self._last_recent_feeds_promoted = 0
        self._last_transcripts_checked = 0
        self._last_transcript_failures = 0
        self._transcript_failure_logged_at: dict[str, float] = {}
        self._last_rss_polled = 0
        self._feeds: dict[str, dict] = self._load_registry()

    def _status(self, message: str) -> None:
        self.status_callback(message)

    def _load_registry(self) -> dict[str, dict]:
        try:
            payload = json.loads(self.source_registry_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        feeds = payload.get("feeds") if isinstance(payload, dict) else None
        if not isinstance(feeds, dict):
            return {}
        return {
            str(url): metadata
            for url, metadata in feeds.items()
            if isinstance(metadata, dict)
        }

    def _save_registry(self) -> None:
        payload = {"feeds": self._feeds}
        try:
            self.source_registry_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
        except OSError as exc:
            self._status(f"Could not save podcast RSS registry: {type(exc).__name__}")

    def _promote_feed(
        self,
        feed_url: str,
        *,
        title: str = "",
        feed_id: str = "",
        reason: str,
        save: bool = True,
    ) -> bool:
        url = feed_url.strip()
        if not url:
            return False
        existing = self._feeds.get(url, {})
        existing_reason = str(existing.get("reason") or "")
        selected_reason = reason or existing_reason
        if existing_reason.startswith("Produced a Claude referral"):
            selected_reason = existing_reason
        updated = {
            "title": title or existing.get("title", ""),
            "feed_id": feed_id or existing.get("feed_id", ""),
            "reason": selected_reason,
            "added_at": existing.get("added_at") or datetime.now(timezone.utc).isoformat(),
        }
        if updated == existing:
            return False
        self._feeds[url] = updated
        if save:
            self._save_registry()
        return True

    async def _api_get(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: dict | None = None,
    ) -> dict:
        response = await client.get(
            url,
            params=params,
            headers=podcast_index_auth_headers(
                self.api_key, self.api_secret, self.user_agent
            ),
        )
        record_count("podcast_index.requests")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def _discover_feeds(self, client: httpx.AsyncClient) -> None:
        if not self.discovery_queries:
            return
        for query in self.discovery_queries:
            changed = False
            payload = await self._api_get(
                client,
                PODCAST_INDEX_SEARCH_BYTERM,
                params={"q": query, "max": 50},
            )
            feeds = payload.get("feeds") or []
            if not isinstance(feeds, list):
                continue
            for feed in feeds:
                if not isinstance(feed, dict):
                    continue
                feed_url = str(feed.get("url") or "").strip()
                if not feed_url:
                    continue
                changed = self._promote_feed(
                    feed_url,
                    title=str(feed.get("title") or ""),
                    feed_id=str(feed.get("id") or ""),
                    reason=f"Podcast Index discovery: {query}",
                    save=False,
                ) or changed
            if changed:
                self._save_registry()

    async def _episode_details(
        self, client: httpx.AsyncClient, episode_id: str
    ) -> dict | None:
        payload = await self._api_get(
            client,
            PODCAST_INDEX_EPISODE_BYID,
            params={"id": episode_id, "fulltext": ""},
        )
        episode = payload.get("episode")
        return episode if isinstance(episode, dict) else None

    async def _feed_details(
        self, client: httpx.AsyncClient, feed_id: str
    ) -> dict | None:
        if not feed_id:
            return None
        payload = await self._api_get(
            client,
            PODCAST_INDEX_PODCAST_BYFEEDID,
            params={"id": feed_id},
        )
        feed = payload.get("feed")
        return feed if isinstance(feed, dict) else None

    @staticmethod
    def _has_relevant_hint(text: str) -> bool:
        lowered = text.lower()
        return any(
            token in lowered
            for token in ("claude", "anthropic", "guest pass", "referral")
        )

    async def _fetch_episode_transcripts(
        self, client: httpx.AsyncClient, episode: dict
    ) -> str:
        """Fetch the small set of transcript files advertised by Podcast Index."""
        bodies: list[str] = []
        headers = {
            "User-Agent": self.user_agent,
            "Accept": (
                "application/json,application/srt,text/vtt,text/srt,text/plain,text/html,*/*"
            ),
        }
        for url in _episode_transcript_urls(episode)[:3]:
            self._last_transcripts_checked += 1
            final_error: httpx.HTTPError | None = None
            response: httpx.Response | None = None
            for attempt in range(self.transcript_retries):
                try:
                    response = await client.get(
                        url,
                        headers=headers,
                        follow_redirects=True,
                        timeout=httpx.Timeout(12.0, connect=6.0),
                    )
                    response.raise_for_status()
                    final_error = None
                    break
                except httpx.HTTPStatusError as exc:
                    final_error = exc
                    # Retry rate limits and server failures, not permanent 4xx.
                    if exc.response.status_code < 500 and exc.response.status_code != 429:
                        break
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    final_error = exc
                if attempt + 1 < self.transcript_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))

            if final_error is not None or response is None:
                self._last_transcript_failures += 1
                host = urlparse(url).netloc or "unknown"
                now = time.monotonic()
                last_logged = self._transcript_failure_logged_at.get(host, 0.0)
                if now - last_logged >= self.transcript_failure_log_seconds:
                    fields = {
                        "source": "Podcast / RSS",
                        "host": host,
                        "error_type": type(final_error).__name__,
                        "attempts": self.transcript_retries,
                        "level": "WARNING",
                    }
                    if isinstance(final_error, httpx.HTTPStatusError):
                        fields["http_status"] = final_error.response.status_code
                    activity("podcast_transcript_host_unreachable", **fields)
                    self._transcript_failure_logged_at[host] = now
                continue

            content_type = (response.headers.get("content-type") or "").lower()
            if content_type and not any(
                marker in content_type
                for marker in ("json", "text/", "application/srt", "application/xml")
            ):
                continue
            body = response.text[:1_500_000]
            bodies.append(body)
            if "json" in content_type:
                # Decoding JSON turns escaped `\/` URL separators back into `/`,
                # allowing the normal referral extractor to see transcript links.
                try:
                    decoded = response.json()
                except ValueError:
                    pass
                else:
                    bodies.append(json.dumps(decoded, ensure_ascii=False)[:1_500_000])
        return "\n".join(bodies)

    async def _fetch_recent_index(self, client: httpx.AsyncClient) -> list[SourcePost]:
        posts: list[SourcePost] = []
        self._last_index_scanned = 0
        self._last_index_relevant = 0
        self._last_recent_feeds_promoted = 0
        self._last_transcripts_checked = 0
        self._last_transcript_failures = 0
        cursor = self._since
        newest_added = cursor
        published_cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=self.lookback_minutes
        )

        # Walk a few batches if the index has more than `recent_max` new items.
        # If there is still a backlog, the next watcher cycle continues from the
        # newest cursor rather than repeatedly scanning the same first page.
        for _ in range(5):
            payload = await self._api_get(
                client,
                PODCAST_INDEX_RECENT_DATA,
                params={"max": self.recent_max, "since": cursor},
            )
            data = payload.get("data") or {}
            items = data.get("items") if isinstance(data, dict) else []
            if not isinstance(items, list):
                items = []
            feeds = data.get("feeds") if isinstance(data, dict) else []
            if not isinstance(feeds, list):
                feeds = []

            registry_changed = False
            for feed in feeds:
                if not isinstance(feed, dict):
                    continue
                feed_summary = "\n".join(
                    part
                    for part in (
                        str(feed.get("feedTitle") or "").strip(),
                        _strip_html(feed.get("feedDescription")),
                    )
                    if part
                )
                if not self._has_relevant_hint(feed_summary):
                    continue
                promoted = self._promote_feed(
                    str(feed.get("feedUrl") or ""),
                    title=str(feed.get("feedTitle") or ""),
                    feed_id=str(feed.get("feedId") or ""),
                    reason="Recent Podcast Index metadata",
                    save=False,
                )
                registry_changed = promoted or registry_changed
                if promoted:
                    self._last_recent_feeds_promoted += 1
            if registry_changed:
                self._save_registry()

            for item in items:
                if not isinstance(item, dict):
                    continue
                episode_id = str(item.get("episodeId") or "").strip()
                if not episode_id or episode_id in self._seen_episode_ids:
                    continue
                self._last_index_scanned += 1

                try:
                    added = int(item.get("episodeAdded") or 0)
                except (TypeError, ValueError):
                    added = 0
                newest_added = max(newest_added, added)

                summary = "\n".join(
                    part
                    for part in (
                        str(item.get("episodeTitle") or "").strip(),
                        _strip_html(item.get("episodeDescription")),
                    )
                    if part
                )
                if not self._has_relevant_hint(summary):
                    self._seen_episode_ids.add(episode_id)
                    continue
                self._last_index_relevant += 1

                episode = await self._episode_details(client, episode_id)
                if not episode:
                    self._seen_episode_ids.add(episode_id)
                    continue
                post = podcast_index_episode_to_post(episode)
                if post is None:
                    self._seen_episode_ids.add(episode_id)
                    continue

                if not extract_referral_links(post.text):
                    transcript_text = await self._fetch_episode_transcripts(
                        client, episode
                    )
                    if transcript_text:
                        post.text = f"{post.text}\n{transcript_text}"
                if not extract_referral_links(post.text):
                    self._seen_episode_ids.add(episode_id)
                    continue

                # `recent/data` means recently added to Podcast Index, not
                # necessarily recently published. An old feed can therefore
                # suddenly return months of historical episodes. Only alert for
                # episodes whose real publication time is inside our lookback.
                if post.created_at is None:
                    activity(
                        "source_post_ignored",
                        source="Podcast / RSS",
                        reason="missing_publication_time",
                        episode_id=episode_id,
                    )
                    self._seen_episode_ids.add(episode_id)
                    continue
                if post.created_at < published_cutoff:
                    activity(
                        "source_post_ignored",
                        source="Podcast / RSS",
                        reason="outside_publication_lookback",
                        episode_id=episode_id,
                        published_at=post.created_at.isoformat(),
                    )
                    self._seen_episode_ids.add(episode_id)
                    continue

                feed_id = str(episode.get("feedId") or item.get("feedId") or "").strip()
                try:
                    feed = await self._feed_details(client, feed_id)
                except httpx.HTTPError as exc:
                    feed = None
                    activity(
                        "podcast_feed_lookup_failed",
                        source="Podcast / RSS",
                        feed_id=feed_id,
                        error_type=type(exc).__name__,
                        level="WARNING",
                    )
                if feed:
                    self._promote_feed(
                        str(feed.get("url") or ""),
                        title=str(feed.get("title") or episode.get("feedTitle") or ""),
                        feed_id=feed_id,
                        reason="Produced a Claude referral",
                    )
                posts.append(post)
                self._seen_episode_ids.add(episode_id)

            try:
                next_since = int(payload.get("nextSince") or 0)
            except (TypeError, ValueError):
                next_since = 0
            try:
                total_count = int(payload.get("itemCount") or 0) + int(
                    payload.get("feedCount") or 0
                )
            except (TypeError, ValueError):
                total_count = len(items)
            if next_since <= cursor or total_count < self.recent_max:
                break
            cursor = next_since
            newest_added = max(newest_added, next_since)

        if newest_added > self._since:
            self._since = newest_added
        elif not posts:
            # Keep a tiny overlap. Episode IDs are de-duped in memory, and referral
            # codes are de-duped in the database, so overlap protects against races.
            self._since = max(self._since, int(time.time()) - 5)
        return posts

    def _rss_feed_urls(self) -> list[str]:
        # Give feeds that previously produced a referral priority when the registry
        # is larger than the configured poll limit.
        def sort_key(url: str) -> tuple[int, float, str]:
            metadata = self._feeds.get(url, {})
            reason = str(metadata.get("reason") or "")
            try:
                added_at = datetime.fromisoformat(
                    str(metadata.get("added_at") or "").replace("Z", "+00:00")
                ).timestamp()
            except (TypeError, ValueError):
                added_at = 0.0
            return (
                0 if reason.startswith("Produced a Claude referral") else 1,
                -added_at,
                url,
            )

        return sorted(self._feeds, key=sort_key)[: self.rss_max_feeds]

    async def _fetch_rss_feed(
        self,
        client: httpx.AsyncClient,
        feed_url: str,
        cutoff: datetime,
        semaphore: asyncio.Semaphore,
    ) -> list[SourcePost]:
        request_headers = self._rss_validators.get(feed_url, {})
        try:
            async with semaphore:
                response = await client.get(feed_url, headers=request_headers)
            if response.status_code == 304:
                return []
            response.raise_for_status()
        except httpx.HTTPError as exc:
            activity(
                "podcast_rss_fetch_failed",
                source="Podcast / RSS",
                feed_url=feed_url,
                error_type=type(exc).__name__,
                level="WARNING",
            )
            return []

        validators: dict[str, str] = {}
        if response.headers.get("etag"):
            validators["If-None-Match"] = response.headers["etag"]
        if response.headers.get("last-modified"):
            validators["If-Modified-Since"] = response.headers["last-modified"]
        if validators:
            self._rss_validators[feed_url] = validators
        else:
            self._rss_validators.pop(feed_url, None)

        bootstrapped = feed_url in self._rss_bootstrapped_feeds
        posts: list[SourcePost] = []
        for identity, post in parse_rss_entries(response.text, feed_url):
            content_hash = hashlib.sha1(post.text.encode("utf-8")).hexdigest()
            seen_key = f"{feed_url}|{identity}|{content_hash}"
            if seen_key in self._seen_rss_entries:
                continue
            self._seen_rss_entries.add(seen_key)

            # Do not dump old feed history into Telegram on startup. Keep a small
            # lookback for genuinely recent episodes, then monitor only newly
            # appearing or edited RSS entries after the feed is bootstrapped.
            if post.created_at is not None and post.created_at < cutoff:
                continue
            if post.created_at is None and not bootstrapped:
                continue
            if extract_referral_links(post.text):
                posts.append(post)
        self._rss_bootstrapped_feeds.add(feed_url)
        return posts

    async def _fetch_rss(self) -> list[SourcePost]:
        feed_urls = self._rss_feed_urls()
        self._last_rss_polled = len(feed_urls)
        if not feed_urls:
            return []
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*",
        }
        timeout = httpx.Timeout(15.0, connect=8.0)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.lookback_minutes)
        semaphore = asyncio.Semaphore(min(8, len(feed_urls)))
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            batches = await asyncio.gather(
                *(
                    self._fetch_rss_feed(client, feed_url, cutoff, semaphore)
                    for feed_url in feed_urls
                )
            )
        return [post for batch in batches for post in batch]

    async def fetch(self) -> list[SourcePost]:
        if not self.api_key or not self.api_secret:
            activity("source_poll_skipped", source="Podcast / RSS", reason="not_configured")
            return []

        activity("source_poll_started", source="Podcast / RSS")
        now = time.monotonic()
        posts: list[SourcePost] = []
        self._last_rss_polled = 0
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            if now - self._last_discovery_at >= self.discovery_interval_seconds:
                await self._discover_feeds(client)
                self._last_discovery_at = now
            posts.extend(await self._fetch_recent_index(client))

        if now - self._last_rss_at >= self.rss_interval_seconds:
            posts.extend(await self._fetch_rss())
            self._last_rss_at = now

        # A referral can appear both through Podcast Index and the direct RSS feed.
        # De-dupe by source URL + text here before the shared referral-code dedupe.
        unique: list[SourcePost] = []
        seen: set[tuple[str | None, str]] = set()
        for post in posts:
            key = (post.url, post.text)
            if key not in seen:
                seen.add(key)
                unique.append(post)
        unique.sort(
            key=lambda post: post.created_at
            or datetime.min.replace(tzinfo=timezone.utc)
        )
        activity(
            "source_poll_completed",
            source="Podcast / RSS",
            posts_found=len(unique),
            rss_feeds=len(self._feeds),
            rss_feeds_polled=self._last_rss_polled,
            recent_episodes_scanned=self._last_index_scanned,
            relevant_candidates=self._last_index_relevant,
            recent_feeds_promoted=self._last_recent_feeds_promoted,
            transcripts_checked=self._last_transcripts_checked,
            transcript_failures=self._last_transcript_failures,
        )
        return unique

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
                    backoff = min(max(backoff * 2, 120), 3600)
                elif code in {401, 403}:
                    backoff = 3600
                else:
                    backoff = min(max(backoff, 120), 900)
                self._status(
                    f"Podcast Index HTTP {code}. Retrying automatically in {backoff}s."
                )
                activity(
                    "source_poll_failed",
                    source="Podcast / RSS",
                    http_status=code,
                    retry_in_seconds=backoff,
                    level="ERROR",
                )
                await asyncio.sleep(backoff)
            except httpx.HTTPError as exc:
                backoff = min(max(backoff * 2, 120), 900)
                self._status(
                    f"Podcast network error. Retrying automatically in {backoff}s."
                )
                activity(
                    "source_poll_failed",
                    source="Podcast / RSS",
                    error_type=type(exc).__name__,
                    retry_in_seconds=backoff,
                    level="ERROR",
                )
                await asyncio.sleep(backoff)
