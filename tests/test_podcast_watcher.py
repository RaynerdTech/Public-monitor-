import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.watchers.podcast import (
    PODCAST_INDEX_RECENT_DATA,
    PodcastWatcher,
    _parse_feed_timestamp,
    parse_rss_entries,
    podcast_index_auth_headers,
    podcast_index_episode_to_post,
)


def test_podcast_index_auth_headers_match_documented_sha1_scheme():
    headers = podcast_index_auth_headers(
        "key", "secret", "ReferralMonitor/0.9", epoch_seconds=1234567890
    )
    expected = hashlib.sha1(b"keysecret1234567890").hexdigest()
    assert headers["X-Auth-Key"] == "key"
    assert headers["X-Auth-Date"] == "1234567890"
    assert headers["Authorization"] == expected
    assert headers["User-Agent"] == "ReferralMonitor/0.9"


def test_podcast_index_episode_to_post_keeps_full_referral_description():
    post = podcast_index_episode_to_post(
        {
            "id": 123,
            "title": "Claude guest pass",
            "description": "<p>Use https://claude.ai/referral/abc123 now</p>",
            "feedTitle": "AI Show",
            "link": "https://example.com/episode",
            "datePublished": 1700000000,
        }
    )
    assert post is not None
    assert post.source == "podcast:AI Show"
    assert "https://claude.ai/referral/abc123" in post.text
    assert post.url == "https://example.com/episode"
    assert post.created_at is not None
    assert post.created_at.tzinfo == timezone.utc


def test_parse_rss_entries_supports_rss_description_and_content_encoded():
    xml = """<?xml version='1.0'?>
    <rss version='2.0' xmlns:content='http://purl.org/rss/1.0/modules/content/'>
      <channel>
        <title>Claude Podcast</title>
        <item>
          <guid>ep-1</guid>
          <title>New episode</title>
          <link>https://example.com/ep-1</link>
          <pubDate>Tue, 10 Sep 2026 12:00:00 GMT</pubDate>
          <description><![CDATA[Guest pass details]]></description>
          <content:encoded><![CDATA[https://claude.ai/referral/rss123]]></content:encoded>
        </item>
      </channel>
    </rss>"""
    rows = parse_rss_entries(xml, "https://example.com/feed.xml")
    assert len(rows) == 1
    identity, post = rows[0]
    assert identity == "ep-1"
    assert post.source == "podcast-rss:Claude Podcast"
    assert post.url == "https://example.com/ep-1"
    assert "https://claude.ai/referral/rss123" in post.text
    assert post.created_at is not None


def test_parse_rss_entries_supports_atom():
    xml = """<feed xmlns='http://www.w3.org/2005/Atom'>
      <title>Atom Show</title>
      <entry>
        <id>tag:example,2026:1</id>
        <title>Claude Code</title>
        <link href='https://example.com/a1'/>
        <updated>2026-09-10T12:00:00Z</updated>
        <summary>https://claude.ai/referral/atom123</summary>
      </entry>
    </feed>"""
    rows = parse_rss_entries(xml, "https://example.com/atom.xml")
    assert len(rows) == 1
    _, post = rows[0]
    assert post.source == "podcast-rss:Atom Show"
    assert post.url == "https://example.com/a1"
    assert "atom123" in post.text


def test_parse_rss_entries_preserves_referral_in_html_href():
    xml = """<rss version='2.0'>
      <channel>
        <title>AI Show</title>
        <item>
          <guid>ep-link</guid>
          <title>Claude episode</title>
          <pubDate>Tue, 15 Sep 2026 12:00:00 GMT</pubDate>
          <description><![CDATA[
            Get the pass <a href="https://claude.ai/referral/href-code">here</a>
          ]]></description>
        </item>
      </channel>
    </rss>"""
    rows = parse_rss_entries(xml, "https://example.com/feed.xml")
    assert len(rows) == 1
    assert "https://claude.ai/referral/href-code" in rows[0][1].text


def test_parse_feed_timestamp_handles_rfc822_and_iso():
    assert _parse_feed_timestamp("Tue, 10 Sep 2026 12:00:00 GMT") is not None
    assert _parse_feed_timestamp("2026-09-10T12:00:00Z") is not None
    assert _parse_feed_timestamp("not-a-date") is None


@pytest.mark.anyio
async def test_recent_index_ignores_old_and_undated_episodes(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    watcher = PodcastWatcher(
        "key",
        "secret",
        "ReferralMonitor/0.9",
        lookback_minutes=30,
        source_registry_path=str(tmp_path / "podcast-sources.json"),
    )

    async def fake_api_get(_client, url, *, params=None):
        assert url == PODCAST_INDEX_RECENT_DATA
        return {
            "data": {
                "items": [
                    {
                        "episodeId": "fresh",
                        "episodeAdded": watcher._since + 1,
                        "episodeTitle": "Fresh Claude referral",
                    },
                    {
                        "episodeId": "old",
                        "episodeAdded": watcher._since + 2,
                        "episodeTitle": "Old Claude referral",
                    },
                    {
                        "episodeId": "undated",
                        "episodeAdded": watcher._since + 3,
                        "episodeTitle": "Undated Claude referral",
                    },
                ]
            },
            "itemCount": 3,
        }

    episodes = {
        "fresh": {
            "id": "fresh",
            "title": "Fresh",
            "description": "https://claude.ai/referral/fresh-code",
            "datePublished": int((now - timedelta(minutes=5)).timestamp()),
        },
        "old": {
            "id": "old",
            "title": "Old",
            "description": "https://claude.ai/referral/old-code",
            "datePublished": int((now - timedelta(days=30)).timestamp()),
        },
        "undated": {
            "id": "undated",
            "title": "Undated",
            "description": "https://claude.ai/referral/undated-code",
        },
    }

    async def fake_episode_details(_client, episode_id):
        return episodes[episode_id]

    monkeypatch.setattr(watcher, "_api_get", fake_api_get)
    monkeypatch.setattr(watcher, "_episode_details", fake_episode_details)

    posts = await watcher._fetch_recent_index(object())

    assert len(posts) == 1
    assert "fresh-code" in posts[0].text


@pytest.mark.anyio
async def test_recent_index_checks_advertised_transcript(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    watcher = PodcastWatcher(
        "key",
        "secret",
        "ReferralMonitor/0.9",
        lookback_minutes=30,
        source_registry_path=str(tmp_path / "podcast-sources.json"),
    )

    async def fake_api_get(_client, url, *, params=None):
        assert url == PODCAST_INDEX_RECENT_DATA
        return {
            "data": {
                "items": [
                    {
                        "episodeId": "transcript-episode",
                        "episodeAdded": watcher._since + 1,
                        "episodeTitle": "Claude guest pass discussion",
                    }
                ]
            },
            "itemCount": 1,
        }

    async def fake_episode_details(_client, _episode_id):
        return {
            "id": "transcript-episode",
            "title": "Claude guest pass discussion",
            "description": "The link is in the transcript.",
            "datePublished": int((now - timedelta(minutes=2)).timestamp()),
            "transcripts": [{"url": "https://example.com/transcript.vtt"}],
        }

    async def fake_transcript(_client, _episode):
        return "https://claude.ai/referral/transcript-code"

    monkeypatch.setattr(watcher, "_api_get", fake_api_get)
    monkeypatch.setattr(watcher, "_episode_details", fake_episode_details)
    monkeypatch.setattr(watcher, "_fetch_episode_transcripts", fake_transcript)

    posts = await watcher._fetch_recent_index(object())
    assert len(posts) == 1
    assert "transcript-code" in posts[0].text


@pytest.mark.anyio
async def test_transcript_json_unescapes_referral_url(tmp_path):
    watcher = PodcastWatcher(
        "key",
        "secret",
        "ReferralMonitor/0.9",
        source_registry_path=str(tmp_path / "podcast-sources.json"),
    )

    class Response:
        headers = {"content-type": "application/json"}
        text = r'{"text":"https:\/\/claude.ai\/referral\/json-code"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"text": "https://claude.ai/referral/json-code"}

    class Client:
        async def get(self, _url, **_kwargs):
            return Response()

    text = await watcher._fetch_episode_transcripts(
        Client(),
        {"transcripts": [{"url": "https://example.com/transcript.json"}]},
    )
    assert "https://claude.ai/referral/json-code" in text


@pytest.mark.anyio
async def test_transcript_connect_error_retries_without_stopping_watcher(tmp_path):
    watcher = PodcastWatcher(
        "key",
        "secret",
        "ReferralMonitor/0.9",
        transcript_retries=2,
        source_registry_path=str(tmp_path / "podcast-sources.json"),
    )

    class Client:
        calls = 0

        async def get(self, url, **_kwargs):
            self.calls += 1
            raise httpx.ConnectError("unreachable", request=httpx.Request("GET", url))

    client = Client()
    text = await watcher._fetch_episode_transcripts(
        client,
        {"transcripts": [{"url": "https://cdn.example.com/transcript.vtt"}]},
    )

    assert text == ""
    assert client.calls == 2
    assert watcher._last_transcript_failures == 1


@pytest.mark.anyio
async def test_rss_conditional_fetch_detects_edited_entry(tmp_path):
    watcher = PodcastWatcher(
        "key",
        "secret",
        "ReferralMonitor/0.9",
        source_registry_path=str(tmp_path / "podcast-sources.json"),
    )
    feed_url = "https://example.com/feed.xml"
    requests = []

    def rss(description):
        return f"""<rss version='2.0'><channel><title>AI Show</title>
        <item><guid>same-episode</guid><title>Claude</title>
        <description><![CDATA[{description}]]></description></item>
        </channel></rss>"""

    class Response:
        status_code = 200

        def __init__(self, text, etag):
            self.text = text
            self.headers = {"etag": etag, "content-type": "application/rss+xml"}

        def raise_for_status(self):
            return None

    class Client:
        async def get(self, _url, *, headers):
            requests.append(dict(headers))
            if len(requests) == 1:
                return Response(rss("No link yet"), '"v1"')
            return Response(
                rss("https://claude.ai/referral/edited-rss"), '"v2"'
            )

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    semaphore = asyncio.Semaphore(1)
    assert await watcher._fetch_rss_feed(Client(), feed_url, cutoff, semaphore) == []
    posts = await watcher._fetch_rss_feed(Client(), feed_url, cutoff, semaphore)

    assert len(posts) == 1
    assert "edited-rss" in posts[0].text
    assert requests[1]["If-None-Match"] == '"v1"'


def test_rss_prioritizes_feeds_that_produced_a_referral(tmp_path):
    watcher = PodcastWatcher(
        "key",
        "secret",
        "ReferralMonitor/0.9",
        rss_max_feeds=1,
        source_registry_path=str(tmp_path / "podcast-sources.json"),
    )
    watcher._feeds = {
        "https://example.com/new.xml": {
            "reason": "Podcast Index discovery: Claude",
            "added_at": "2026-09-15T12:00:00+00:00",
        },
        "https://example.com/proven.xml": {
            "reason": "Produced a Claude referral",
            "added_at": "2026-09-14T12:00:00+00:00",
        },
    }
    assert watcher._rss_feed_urls() == ["https://example.com/proven.xml"]


@pytest.mark.anyio
async def test_recent_data_promotes_relevant_new_feed(monkeypatch, tmp_path):
    watcher = PodcastWatcher(
        "key",
        "secret",
        "ReferralMonitor/0.9",
        source_registry_path=str(tmp_path / "podcast-sources.json"),
    )

    async def fake_api_get(_client, url, *, params=None):
        assert url == PODCAST_INDEX_RECENT_DATA
        return {
            "data": {
                "feeds": [
                    {
                        "feedId": 42,
                        "feedUrl": "https://example.com/claude.xml",
                        "feedTitle": "Claude Code Weekly",
                        "feedDescription": "AI news",
                    }
                ],
                "items": [],
            },
            "feedCount": 1,
            "itemCount": 0,
        }

    monkeypatch.setattr(watcher, "_api_get", fake_api_get)
    await watcher._fetch_recent_index(object())

    assert "https://example.com/claude.xml" in watcher._feeds
    assert watcher._last_recent_feeds_promoted == 1
