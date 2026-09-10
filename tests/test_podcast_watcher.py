import hashlib
from datetime import timezone

from app.watchers.podcast import (
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


def test_parse_feed_timestamp_handles_rfc822_and_iso():
    assert _parse_feed_timestamp("Tue, 10 Sep 2026 12:00:00 GMT") is not None
    assert _parse_feed_timestamp("2026-09-10T12:00:00Z") is not None
    assert _parse_feed_timestamp("not-a-date") is None
