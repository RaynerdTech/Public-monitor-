from datetime import datetime, timezone

import httpx
import pytest

from app.watchers.sites import (
    DirectWebsiteWatcher,
    _source_endpoints,
    parse_feed_entries,
    parse_sitemap_entries,
)


def test_source_endpoints_cover_wordpress_shopify_and_sitemaps():
    endpoints = _source_endpoints("https://example.com")
    assert "https://example.com/feed/" in endpoints
    assert "https://example.com/wp-sitemap.xml" in endpoints
    assert "https://example.com/sitemap.xml" in endpoints
    assert "https://example.com/blogs/news.atom" in endpoints
    assert _source_endpoints("https://example.com/custom.xml") == [
        "https://example.com/custom.xml"
    ]


def test_parse_feed_preserves_referral_in_href():
    xml = """<rss version='2.0'><channel><item>
    <title>Fresh post</title>
    <link>https://example.com/fresh</link>
    <pubDate>Tue, 15 Sep 2026 13:00:00 GMT</pubDate>
    <description><![CDATA[
      <a href="https://claude.ai/referral/direct-feed-code">Open</a>
    ]]></description>
    </item></channel></rss>"""
    posts = parse_feed_entries(xml, "https://example.com/feed/")
    assert len(posts) == 1
    assert posts[0].url == "https://example.com/fresh"
    assert "https://claude.ai/referral/direct-feed-code" in posts[0].text


def test_parse_sitemap_supports_index_and_urlset():
    children, pages = parse_sitemap_entries(
        "<sitemapindex><sitemap><loc>https://example.com/posts.xml</loc>"
        "</sitemap></sitemapindex>"
    )
    assert children == ["https://example.com/posts.xml"]
    assert pages == []

    children, pages = parse_sitemap_entries(
        "<urlset><url><loc>https://example.com/new</loc>"
        "<lastmod>2026-09-15T13:00:00Z</lastmod></url></urlset>"
    )
    assert children == []
    assert pages[0][0] == "https://example.com/new"
    assert pages[0][1] == datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc)


@pytest.mark.anyio
async def test_direct_watcher_finds_recent_unindexed_sitemap_page(monkeypatch):
    watcher = DirectWebsiteWatcher(
        ["https://example.com"], lookback_minutes=180
    )
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    sitemap = (
        "<urlset><url><loc>https://example.com/new-post</loc>"
        f"<lastmod>{now}</lastmod></url></urlset>"
    )

    async def fake_get(_client, url, **_kwargs):
        if url.endswith("/sitemap.xml"):
            return httpx.Response(
                200, text=sitemap, headers={"content-type": "application/xml"}
            )
        if url == "https://example.com/new-post":
            return httpx.Response(
                200,
                text='<a href="https://claude.ai/referral/unindexed-code">Open</a>',
                headers={"content-type": "text/html"},
            )
        return None

    monkeypatch.setattr(watcher, "_get", fake_get)
    posts = await watcher.fetch()

    assert len(posts) == 1
    assert posts[0].url == "https://example.com/new-post"
    assert "unindexed-code" in posts[0].text


@pytest.mark.anyio
async def test_direct_watcher_deduplicates_same_feed_referral(monkeypatch):
    watcher = DirectWebsiteWatcher(["https://example.com/feed.xml"])
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    feed = f"""<rss version='2.0'><channel><item>
    <title>Fresh</title><link>https://example.com/post</link>
    <pubDate>{now}</pubDate>
    <description>https://claude.ai/referral/feed-code</description>
    </item></channel></rss>"""

    async def fake_get(_client, _url, **_kwargs):
        return httpx.Response(
            200, text=feed, headers={"content-type": "application/rss+xml"}
        )

    monkeypatch.setattr(watcher, "_get", fake_get)
    assert len(await watcher.fetch()) == 1
    assert await watcher.fetch() == []
