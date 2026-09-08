import httpx
import pytest

from app.core.extractor import extract_referral_links
from app.watchers.x import (
    XWatcher,
    extract_x_entity_urls,
    parse_x_timestamp,
    x_row_to_source_post,
)


def test_parse_x_timestamp():
    parsed = parse_x_timestamp("2026-09-07T01:02:03.000Z")
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.tzinfo is not None


def test_expanded_x_url_is_added_to_source_text():
    row = {
        "id": "1234567890123456789",
        "text": "New pass https://t.co/abc123",
        "created_at": "2026-09-07T01:02:03.000Z",
        "author_id": "42",
        "entities": {
            "urls": [
                {
                    "url": "https://t.co/abc123",
                    "expanded_url": "https://claude.ai/referral/example123?s=ios",
                }
            ]
        },
    }

    assert extract_x_entity_urls(row) == [
        "https://claude.ai/referral/example123?s=ios"
    ]
    post = x_row_to_source_post(row, {"42": "exampleuser"})
    assert post is not None
    assert extract_referral_links(post.text) == [
        "https://claude.ai/referral/example123?s=ios"
    ]


@pytest.mark.anyio
async def test_x_watcher_fetch(monkeypatch):
    payload = {
        "data": [
            {
                "id": "1234567890123456789",
                "text": "New pass https://t.co/abc123",
                "created_at": "2026-09-07T01:02:03.000Z",
                "author_id": "42",
                "entities": {
                    "urls": [
                        {
                            "url": "https://t.co/abc123",
                            "expanded_url": "https://claude.ai/referral/example123",
                        }
                    ]
                },
            }
        ],
        "includes": {
            "users": [
                {
                    "id": "42",
                    "username": "exampleuser",
                }
            ]
        },
        "meta": {
            "newest_id": "1234567890123456789",
            "result_count": 1,
        },
    }

    async def fake_get(self, url, params=None, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(200, request=request, json=payload)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    watcher = XWatcher(
        "token",
        ['url:"claude.ai/referral"'],
        interval_seconds=20,
        max_results=100,
    )
    posts = await watcher.fetch()

    assert len(posts) == 1
    assert posts[0].source == "x:@exampleuser"
    assert posts[0].url == "https://x.com/exampleuser/status/1234567890123456789"
    assert "https://claude.ai/referral/example123" in posts[0].text
    assert watcher._since_id_by_query['url:"claude.ai/referral"'] == "1234567890123456789"

    # Same post should not be yielded twice if it appears in another poll.
    posts_again = await watcher.fetch()
    assert posts_again == []
