from datetime import datetime, timezone

import asyncio

import httpx

from app.services.apify import ApifyClient
from app.watchers.apify_facebook import (
    ApifyFacebookWatcher,
    facebook_apify_row_to_source_post,
)
from app.watchers.apify_instagram import (
    ApifyInstagramWatcher,
    instagram_apify_row_to_source_post,
)


def test_facebook_apify_row_maps_public_post():
    post = facebook_apify_row_to_source_post(
        {
            "postText": "Claude referral https://claude.ai/referral/fb-code",
            "url": "https://www.facebook.com/example/posts/123",
            "publishedAt": "2026-09-16T14:30:00Z",
            "author": {"name": "Example User"},
        }
    )
    assert post is not None
    assert post.source == "facebook:search:Example User"
    assert post.url == "https://www.facebook.com/example/posts/123"
    assert "fb-code" in post.text
    assert post.created_at == datetime(2026, 9, 16, 14, 30, tzinfo=timezone.utc)


def test_facebook_apify_input_is_posts_search_with_cost_control_date():
    watcher = ApifyFacebookWatcher(
        ["claude referral"],
        actor_id="memo23~facebook-search-scraper",
        max_results=10,
    )
    payload = watcher.build_input("claude referral")
    assert payload["searchType"] == "posts"
    assert payload["searchQueries"] == ["claude referral"]
    assert payload["maxItems"] == 10
    assert payload["onlyPostsNewerThan"]


def test_instagram_apify_row_maps_post_or_reel():
    post = instagram_apify_row_to_source_post(
        {
            "caption": "Free Claude referral https://claude.ai/referral/ig-code",
            "url": "https://www.instagram.com/reel/ABC123/",
            "publishedAt": "2026-09-16T14:31:00Z",
            "username": "creator",
            "hashtags": ["claude", "ai"],
        }
    )
    assert post is not None
    assert post.source == "instagram:search:@creator"
    assert post.url == "https://www.instagram.com/reel/ABC123/"
    assert "ig-code" in post.text
    assert "claude" in post.text


def test_instagram_apify_input_uses_broad_posts_and_reels_search():
    watcher = ApifyInstagramWatcher(
        ["claude referral"],
        actor_id="scraping_solutions~instagram-boolean-search-scraper-posts-reels",
        max_results=10,
    )
    payload = watcher.build_input("claude referral")
    assert payload["searchQuery"] == "claude referral"
    assert payload["resultsLimit"] == 10
    assert payload["contentType"] == "posts_and_reels"
    assert payload["searchCoverage"] == "efficient"
    assert payload["hashtagFeedType"] == "recent"
    assert payload["oldestPostDate"]
    assert payload["newestPostDate"]


def test_apify_client_uses_dynamic_token_and_sync_actor_endpoint(monkeypatch):
    state = {"token": "apify_api_first"}
    calls = []

    async def fake_post(self, url, params=None, headers=None, json=None, **kwargs):
        calls.append((url, params, headers, json))
        assert headers["Authorization"] == f"Bearer {state['token']}"
        request = httpx.Request("POST", url)
        return httpx.Response(201, request=request, json=[{"id": "row-1"}])

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    async def run():
        client = ApifyClient(lambda: state["token"], max_run_cost_usd=0.10)
        first = await client.run_actor(
            "memo23/facebook-search-scraper",
            {"searchType": "posts"},
            source="Facebook",
            max_items=10,
        )
        state["token"] = "apify_api_second"
        second = await client.run_actor(
            "memo23~facebook-search-scraper",
            {"searchType": "posts"},
            source="Facebook",
            max_items=10,
        )
        assert first == [{"id": "row-1"}]
        assert second == [{"id": "row-1"}]
        assert client.total_runs == 2
        assert calls[0][0].endswith("/actors/memo23~facebook-search-scraper/run-sync-get-dataset-items")
        assert calls[0][1]["maxItems"] == 10
        assert calls[0][1]["maxTotalChargeUsd"] == 0.10

    asyncio.run(run())
