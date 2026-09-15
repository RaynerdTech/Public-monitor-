from datetime import datetime, timedelta, timezone
import asyncio


from app.watchers.web import (
    EXA_CONTENTS_URL,
    ExaWebWatcher,
    exa_result_to_source_post,
    parse_exa_timestamp,
)


def test_parse_exa_timestamp():
    value = parse_exa_timestamp("2026-09-08T17:12:34.000Z")
    assert value is not None
    assert value.tzinfo == timezone.utc
    assert value.year == 2026


def test_exa_result_to_source_post_includes_page_text_and_url():
    post = exa_result_to_source_post(
        {
            "url": "https://example.com/post",
            "title": "Fresh Claude guest pass",
            "publishedDate": "2026-09-08T17:12:34.000Z",
        },
        '<a href="https://claude.ai/referral/ABC123">claim</a>',
    )
    assert post is not None
    assert post.source == "web:exa"
    assert post.url == "https://example.com/post"
    assert "https://claude.ai/referral/ABC123" in post.text


def test_exa_result_to_source_post_includes_extracted_links():
    post = exa_result_to_source_post(
        {
            "url": "https://example.com/post",
            "publishedDate": "2026-09-08T17:12:34.000Z",
            "extras": {
                "links": [
                    "https://claude.ai/referral/from-exa",
                    {"url": "https://example.com/other"},
                ]
            },
        }
    )
    assert post is not None
    assert "https://claude.ai/referral/from-exa" in post.text


def test_exa_result_requires_url():
    assert exa_result_to_source_post({"title": "missing url"}) is None


def test_search_sends_server_side_publication_cutoff():
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    class Client:
        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return Response()

    cutoff = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    watcher = ExaWebWatcher("test-key", ["query"])
    asyncio.run(watcher._search(Client(), "query", cutoff))

    assert captured["json"]["startPublishedDate"] == "2026-09-09T12:00:00.000Z"
    assert captured["json"]["numResults"] == 10


def test_exa_content_fallback_requests_targeted_links_and_highlights():
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    class Client:
        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return Response()

    watcher = ExaWebWatcher("test-key", ["query"])
    asyncio.run(
        watcher._fetch_exa_contents(Client(), ["https://example.com/blocked"])
    )

    assert captured["url"] == EXA_CONTENTS_URL
    assert captured["json"]["ids"] == ["https://example.com/blocked"]
    assert captured["json"]["extras"]["links"] == 50
    assert captured["json"]["highlights"]["maxCharacters"] == 3000


def test_fetch_drops_undated_and_old_results(monkeypatch):
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    old = (now - timedelta(hours=3)).isoformat().replace("+00:00", "Z")

    watcher = ExaWebWatcher(
        "test-key",
        ["query"],
        lookback_minutes=60,
    )

    async def fake_search(_client, _query, _cutoff):
        return [
            {"url": "https://example.com/fresh", "publishedDate": fresh},
            {"url": "https://example.com/undated"},
            {"url": "https://example.com/old", "publishedDate": old},
        ]

    async def fake_fetch(_client, url):
        return f"live {url} https://claude.ai/referral/fresh-code"

    monkeypatch.setattr(watcher, "_search", fake_search)
    monkeypatch.setattr(watcher, "_fetch_page", fake_fetch)

    posts = asyncio.run(watcher.fetch())
    assert [post.url for post in posts] == ["https://example.com/fresh"]


def test_fetch_uses_exa_link_without_fetching_page(monkeypatch):
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    watcher = ExaWebWatcher("test-key", ["query"], lookback_minutes=60)

    async def fake_search(_client, _query, _cutoff):
        return [
            {
                "url": "https://example.com/post",
                "publishedDate": fresh,
                "extras": {"links": ["https://claude.ai/referral/exa-code"]},
            }
        ]

    async def unexpected_fetch(_client, _url):
        raise AssertionError("page fetch should be skipped")

    monkeypatch.setattr(watcher, "_search", fake_search)
    monkeypatch.setattr(watcher, "_fetch_page", unexpected_fetch)

    posts = asyncio.run(watcher.fetch())
    assert len(posts) == 1
    assert "exa-code" in posts[0].text


def test_fetch_uses_exa_fallback_when_page_is_blocked(monkeypatch):
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    watcher = ExaWebWatcher("test-key", ["query"], lookback_minutes=60)

    async def fake_search(_client, _query, _cutoff):
        return [{"url": "https://example.com/blocked", "publishedDate": fresh}]

    async def fake_page_fetch(_client, _url):
        return ""

    async def fake_exa_contents(_client, urls):
        assert urls == ["https://example.com/blocked"]
        return [
            {
                "url": "https://example.com/blocked",
                "extras": {"links": ["https://claude.ai/referral/fallback-code"]},
            }
        ]

    monkeypatch.setattr(watcher, "_search", fake_search)
    monkeypatch.setattr(watcher, "_fetch_page", fake_page_fetch)
    monkeypatch.setattr(watcher, "_fetch_exa_contents", fake_exa_contents)

    posts = asyncio.run(watcher.fetch())
    assert len(posts) == 1
    assert "fallback-code" in posts[0].text


def test_fetch_rechecks_page_that_did_not_contain_referral(monkeypatch):
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    watcher = ExaWebWatcher("test-key", ["query"], lookback_minutes=60)
    fetches = 0

    async def fake_search(_client, _query, _cutoff):
        return [{"url": "https://example.com/edited", "publishedDate": fresh}]

    async def fake_fetch(_client, _url):
        nonlocal fetches
        fetches += 1
        if fetches == 1:
            return "No referral yet"
        return "Edited: https://claude.ai/referral/added-later"

    monkeypatch.setattr(watcher, "_search", fake_search)
    monkeypatch.setattr(watcher, "_fetch_page", fake_fetch)

    assert asyncio.run(watcher.fetch()) == []
    posts = asyncio.run(watcher.fetch())
    assert len(posts) == 1
    assert fetches == 2


def test_fetch_reports_new_code_added_to_previously_seen_page(monkeypatch):
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    watcher = ExaWebWatcher("test-key", ["query"], lookback_minutes=60)
    searches = 0

    async def fake_search(_client, _query, _cutoff):
        nonlocal searches
        searches += 1
        links = ["https://claude.ai/referral/first"]
        if searches > 1:
            links.append("https://claude.ai/referral/second")
        return [
            {
                "url": "https://example.com/edited",
                "publishedDate": fresh,
                "extras": {"links": links},
            }
        ]

    monkeypatch.setattr(watcher, "_search", fake_search)

    assert len(asyncio.run(watcher.fetch())) == 1
    updated = asyncio.run(watcher.fetch())
    assert len(updated) == 1
    assert "https://claude.ai/referral/second" in updated[0].text


def test_fetch_discards_dead_live_page(monkeypatch):
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")

    watcher = ExaWebWatcher("test-key", ["query"], lookback_minutes=60)

    async def fake_search(_client, _query, _cutoff):
        return [{"url": "https://example.com/deleted", "publishedDate": fresh}]

    async def fake_fetch(_client, _url):
        return None

    monkeypatch.setattr(watcher, "_search", fake_search)
    monkeypatch.setattr(watcher, "_fetch_page", fake_fetch)

    assert asyncio.run(watcher.fetch()) == []
