import httpx
import pytest

from app.core.extractor import extract_referral_links
from app.watchers.reddit import (
    RedditWatcher,
    parse_reddit_timestamp,
    reddit_row_to_source_post,
)


def test_parse_reddit_timestamp():
    parsed = parse_reddit_timestamp(1788786123)
    assert parsed is not None
    assert parsed.tzinfo is not None


def test_reddit_row_adds_destination_url_to_source_text():
    row = {
        "id": "abc123",
        "subreddit": "ClaudeAI",
        "author": "exampleuser",
        "created_utc": 1788786123,
        "permalink": "/r/ClaudeAI/comments/abc123/new_guest_pass/",
        "title": "New guest pass",
        "selftext": "Enjoy",
        "url_overridden_by_dest": "https://claude.ai/referral/example123?s=reddit",
    }

    post = reddit_row_to_source_post(row)
    assert post is not None
    assert post.source == "reddit:r/ClaudeAI:u/exampleuser"
    assert post.url == "https://www.reddit.com/r/ClaudeAI/comments/abc123/new_guest_pass/"
    assert extract_referral_links(post.text) == [
        "https://claude.ai/referral/example123?s=reddit"
    ]


@pytest.mark.anyio
async def test_reddit_watcher_fetch(monkeypatch):
    token_payload = {
        "access_token": "oauth-token",
        "token_type": "bearer",
        "expires_in": 3600,
        "scope": "*",
    }
    search_payload = {
        "data": {
            "children": [
                {
                    "kind": "t3",
                    "data": {
                        "id": "abc123",
                        "subreddit": "ClaudeAI",
                        "author": "exampleuser",
                        "created_utc": 1788786123,
                        "permalink": "/r/ClaudeAI/comments/abc123/new_guest_pass/",
                        "title": "New Claude referral",
                        "selftext": "https://claude.ai/referral/example123",
                        "url": "https://www.reddit.com/r/ClaudeAI/comments/abc123/new_guest_pass/",
                    },
                }
            ]
        }
    }

    async def fake_post(self, url, data=None, auth=None, headers=None, **kwargs):
        request = httpx.Request("POST", url)
        assert data == {"grant_type": "client_credentials"}
        assert auth == ("client-id", "client-secret")
        return httpx.Response(200, request=request, json=token_payload)

    async def fake_get(self, url, params=None, headers=None, **kwargs):
        request = httpx.Request("GET", url)
        assert headers["Authorization"] == "bearer oauth-token"
        assert params["sort"] == "new"
        return httpx.Response(200, request=request, json=search_payload)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    watcher = RedditWatcher(
        "client-id",
        "client-secret",
        "windows:referral-monitor:v0.6 (by u/exampleuser)",
        ["claude.ai/referral"],
        interval_seconds=20,
        limit=100,
    )
    posts = await watcher.fetch()

    assert len(posts) == 1
    assert posts[0].source == "reddit:r/ClaudeAI:u/exampleuser"
    assert "https://claude.ai/referral/example123" in posts[0].text

    # Same Reddit post should not be yielded twice on the next poll.
    posts_again = await watcher.fetch()
    assert posts_again == []
