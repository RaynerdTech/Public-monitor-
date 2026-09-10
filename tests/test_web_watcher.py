from datetime import datetime, timedelta, timezone
import asyncio


from app.watchers.web import ExaWebWatcher, exa_result_to_source_post, parse_exa_timestamp


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
        return f"live {url}"

    monkeypatch.setattr(watcher, "_search", fake_search)
    monkeypatch.setattr(watcher, "_fetch_page", fake_fetch)

    posts = asyncio.run(watcher.fetch())
    assert [post.url for post in posts] == ["https://example.com/fresh"]


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
