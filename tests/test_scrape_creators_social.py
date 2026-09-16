import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from app.core.extractor import extract_referral_links
from app.services.scrape_creators import ScrapeCreatorsClient, ScrapeCreatorsResponse
from app.watchers.facebook import FacebookWatcher, parse_facebook_source
from app.watchers.instagram import InstagramWatcher
from app.watchers.scrape_reddit import ScrapeCreatorsRedditWatcher
from app.watchers.scrape_threads import ScrapeCreatorsThreadsWatcher


def _response(payload: dict, *, remaining: int = 99) -> ScrapeCreatorsResponse:
    return ScrapeCreatorsResponse(
        payload=payload,
        credits_charged=1,
        credits_remaining=remaining,
    )


def test_scrape_creators_client_uses_api_key_and_tracks_credits(monkeypatch):
    async def fake_get(self, url, params=None, **kwargs):
        assert self.headers["x-api-key"] == "secret-key"
        assert url.endswith("/v1/reddit/search")
        assert params == {"query": "claude referral"}
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            request=request,
            json={
                "success": True,
                "credits_charged": 1,
                "credits_remaining": 42,
                "posts": [],
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async def run():
        client = ScrapeCreatorsClient("secret-key")
        result = await client.get(
            "/v1/reddit/search",
            params={"query": "claude referral"},
            source="Reddit",
        )
        assert result.credits_charged == 1
        assert result.credits_remaining == 42
        assert client.total_requests == 1
        assert client.total_credits_charged == 1
        assert client.last_credits_remaining == 42

    asyncio.run(run())


def test_threads_scrape_creator_watcher_maps_and_dedupes(monkeypatch):
    watcher = ScrapeCreatorsThreadsWatcher("key", ["claude referral"])
    calls = []

    async def fake_get(path, *, params=None, source):
        calls.append((path, params, source))
        return _response(
            {
                "success": True,
                "posts": [
                    {
                        "id": "3623717915788120086_123",
                        "pk": "3623717915788120086",
                        "user": {"username": "tester"},
                        "caption": {
                            "text": "Claude guest pass https://claude.ai/referral/thread-code?s=threads"
                        },
                        "code": "DJKCD7AxRAW",
                        "taken_at": int(datetime.now(timezone.utc).timestamp()),
                    }
                ],
            }
        )

    monkeypatch.setattr(watcher.client, "get", fake_get)

    async def run():
        first = await watcher.fetch()
        second = await watcher.fetch()
        assert len(first) == 1
        assert second == []
        assert first[0].source == "threads:@tester"
        assert first[0].url == "https://www.threads.net/@tester/post/DJKCD7AxRAW"
        assert extract_referral_links(first[0].text) == [
            "https://claude.ai/referral/thread-code?s=threads"
        ]
        assert calls[0][0] == "/v1/threads/search"
        assert calls[0][1]["query"] == "claude referral"
        assert calls[0][1]["start_date"]
        assert calls[0][1]["end_date"]

    asyncio.run(run())


def test_reddit_scrape_creator_watcher_uses_newest_posts(monkeypatch):
    watcher = ScrapeCreatorsRedditWatcher(
        "key",
        ["claude referral"],
        search_filter="posts",
        timeframe="day",
    )
    calls = []

    async def fake_get(path, *, params=None, source):
        calls.append((path, params, source))
        return _response(
            {
                "success": True,
                "posts": [
                    {
                        "id": "abc123",
                        "subreddit": "ClaudeAI",
                        "author": "exampleuser",
                        "created_utc": int(datetime.now(timezone.utc).timestamp()),
                        "permalink": "/r/ClaudeAI/comments/abc123/new_guest_pass/",
                        "title": "New Claude referral",
                        "selftext": "https://claude.ai/referral/reddit-code",
                        "url": "https://www.reddit.com/r/ClaudeAI/comments/abc123/new_guest_pass/",
                    }
                ],
            }
        )

    monkeypatch.setattr(watcher.client, "get", fake_get)

    async def run():
        first = await watcher.fetch()
        second = await watcher.fetch()
        assert len(first) == 1
        assert second == []
        assert first[0].source == "reddit:r/ClaudeAI:u/exampleuser"
        assert "reddit-code" in first[0].text
        assert calls[0][0] == "/v1/reddit/search"
        assert calls[0][1]["sort"] == "new"
        assert calls[0][1]["timeframe"] == "day"

    asyncio.run(run())


def test_instagram_watcher_checks_hashtag_and_reels(monkeypatch):
    watcher = InstagramWatcher(
        "key",
        hashtags=["claudeai"],
        reels_queries=["claude referral"],
    )
    calls = []

    async def fake_get(path, *, params=None, source):
        calls.append((path, params, source))
        if path.endswith("/hashtag"):
            return _response(
                {
                    "success": True,
                    "posts": [
                        {
                            "id": "ig-post-1",
                            "shortcode": "POST1",
                            "url": "https://www.instagram.com/p/POST1/",
                            "caption": "Referral https://claude.ai/referral/ig-post-code",
                            "owner": {"username": "postuser"},
                            "taken_at": int(datetime.now(timezone.utc).timestamp()),
                        }
                    ],
                }
            )
        return _response(
            {
                "success": True,
                "reels": [
                    {
                        "id": "ig-reel-1",
                        "shortcode": "REEL1",
                        "url": "https://www.instagram.com/reel/REEL1/",
                        "caption": "Guest pass https://claude.ai/referral/ig-reel-code",
                        "owner": {"username": "reeluser"},
                        "taken_at": int(datetime.now(timezone.utc).timestamp()),
                    }
                ],
            }
        )

    monkeypatch.setattr(watcher.client, "get", fake_get)

    async def run():
        first = await watcher.fetch()
        second = await watcher.fetch()
        assert len(first) == 2
        assert second == []
        assert {post.source for post in first} == {
            "instagram:hashtag:claudeai:@postuser",
            "instagram:reels:@reeluser",
        }
        assert len(calls) == 4  # 2 endpoints per poll, including the dedupe poll.
        assert calls[0][0] == "/v1/instagram/search/hashtag"
        assert calls[1][0] == "/v2/instagram/reels/search"

    asyncio.run(run())


def test_facebook_source_parser_and_watcher(monkeypatch):
    assert parse_facebook_source("group:https://www.facebook.com/groups/123").kind == "group"
    assert parse_facebook_source("https://www.facebook.com/example").kind == "page"

    watcher = FacebookWatcher(
        "key",
        [
            "page:https://www.facebook.com/example",
            "group:https://www.facebook.com/groups/123",
        ],
    )
    calls = []

    async def fake_get(path, *, params=None, source):
        calls.append((path, params, source))
        if path.endswith("/profile/posts"):
            return _response(
                {
                    "success": True,
                    "posts": [
                        {
                            "id": "fb-page-1",
                            "text": "Claude link https://claude.ai/referral/fb-page-code",
                            "url": "https://www.facebook.com/example/posts/1",
                            "author": {"name": "Example Page"},
                            "publishTime": int(datetime.now(timezone.utc).timestamp()),
                            "topComments": [],
                        }
                    ],
                }
            )
        return _response(
            {
                "success": True,
                "posts": [
                    {
                        "id": "fb-group-1",
                        "text": "No link in body",
                        "url": "https://www.facebook.com/groups/123/permalink/2",
                        "author": {"name": "Example User"},
                        "publishTime": int(datetime.now(timezone.utc).timestamp()),
                        "topComments": [
                            {
                                "text": "Referral https://claude.ai/referral/fb-group-code"
                            }
                        ],
                    }
                ],
            }
        )

    monkeypatch.setattr(watcher.client, "get", fake_get)

    async def run():
        first = await watcher.fetch()
        second = await watcher.fetch()
        assert len(first) == 2
        assert second == []
        assert any("fb-page-code" in post.text for post in first)
        assert any("fb-group-code" in post.text for post in first)
        assert calls[0][0] == "/v1/facebook/profile/posts"
        assert calls[1][0] == "/v1/facebook/group/posts"
        assert calls[1][1]["sort_by"] == "CHRONOLOGICAL"

    asyncio.run(run())


def test_social_watchers_drop_stale_results(monkeypatch):
    stale_ts = int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp())

    threads = ScrapeCreatorsThreadsWatcher("key", ["claude referral"], lookback_minutes=7)

    async def fake_threads_get(path, *, params=None, source):
        return _response({
            "posts": [{
                "id": "stale-thread",
                "user": {"username": "old"},
                "caption": {"text": "https://claude.ai/referral/old"},
                "code": "OLD",
                "taken_at": stale_ts,
            }]
        })

    monkeypatch.setattr(threads.client, "get", fake_threads_get)

    reddit = ScrapeCreatorsRedditWatcher(
        "key", ["claude referral"], lookback_minutes=7
    )

    async def fake_reddit_get(path, *, params=None, source):
        return _response({
            "posts": [{
                "id": "stale-reddit",
                "subreddit": "ClaudeAI",
                "author": "old",
                "created_utc": stale_ts,
                "permalink": "/r/ClaudeAI/comments/stale/old/",
                "title": "old",
                "selftext": "https://claude.ai/referral/old",
            }]
        })

    monkeypatch.setattr(reddit.client, "get", fake_reddit_get)

    async def run():
        assert await threads.fetch() == []
        assert await reddit.fetch() == []

    asyncio.run(run())
