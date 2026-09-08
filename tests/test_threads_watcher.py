import asyncio

import httpx

from app.watchers.threads import ThreadsWatcher, parse_threads_timestamp


def test_parse_threads_timestamp():
    parsed = parse_threads_timestamp("2026-09-06T12:30:00+0000")
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.minute == 30


def test_threads_watcher_maps_recent_posts(monkeypatch):
    watcher = ThreadsWatcher("token", ["claude.ai/referral"], interval_seconds=30)

    async def fake_search(_client, _query):
        return [
            {
                "id": "post-1",
                "username": "tester",
                "text": "new one https://claude.ai/referral/demo-code?s=ios",
                "permalink": "https://www.threads.net/@tester/post/post-1",
                "timestamp": "2026-09-06T12:30:00+0000",
            }
        ]

    monkeypatch.setattr(watcher, "_search", fake_search)

    async def run():
        first = await watcher.fetch()
        second = await watcher.fetch()

        assert len(first) == 1
        assert first[0].source == "threads:@tester"
        assert "demo-code" in first[0].text
        assert second == []

    asyncio.run(run())
