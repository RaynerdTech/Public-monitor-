import httpx
import pytest

from app.core.extractor import extract_referral_links
from app.watchers.youtube import (
    YouTubeWatcher,
    parse_youtube_timestamp,
    youtube_video_to_source_post,
)


def test_parse_youtube_timestamp():
    parsed = parse_youtube_timestamp("2026-09-08T10:20:30Z")
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.tzinfo is not None


def test_youtube_video_maps_description_and_source():
    video = {
        "id": "video123",
        "snippet": {
            "publishedAt": "2026-09-08T10:20:30Z",
            "channelId": "channel123",
            "channelTitle": "Referral News",
            "title": "Claude guest pass",
            "description": "Use https://claude.ai/referral/example123?s=youtube",
        },
    }
    post = youtube_video_to_source_post(video)
    assert post is not None
    assert post.source == "youtube:Referral News"
    assert post.url == "https://www.youtube.com/watch?v=video123"
    assert extract_referral_links(post.text) == [
        "https://claude.ai/referral/example123?s=youtube"
    ]


@pytest.mark.anyio
async def test_youtube_watcher_fetch_and_dedupe(monkeypatch):
    search_payload = {
        "items": [
            {
                "id": {"kind": "youtube#video", "videoId": "video123"},
                "snippet": {"publishedAt": "2026-09-08T10:20:30Z"},
            }
        ]
    }
    video_payload = {
        "items": [
            {
                "id": "video123",
                "snippet": {
                    "publishedAt": "2026-09-08T10:20:30Z",
                    "channelId": "channel123",
                    "channelTitle": "Referral News",
                    "title": "Claude referral",
                    "description": "https://claude.ai/referral/example123",
                },
            }
        ]
    }

    async def fake_get(self, url, params=None, **kwargs):
        request = httpx.Request("GET", url)
        if url.endswith("/search"):
            assert params["type"] == "video"
            assert params["order"] == "date"
            assert params["q"] == "claude referral|claude guest pass|claude.ai/referral"
            assert params["key"] == "api-key"
            return httpx.Response(200, request=request, json=search_payload)
        if url.endswith("/videos"):
            assert params["id"] == "video123"
            return httpx.Response(200, request=request, json=video_payload)
        raise AssertionError(url)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    watcher = YouTubeWatcher(
        "api-key",
        ["claude referral|claude guest pass|claude.ai/referral"],
        interval_seconds=900,
        max_results=50,
        lookback_minutes=30,
    )
    first = await watcher.fetch()
    second = await watcher.fetch()

    assert len(first) == 1
    assert first[0].source == "youtube:Referral News"
    assert "https://claude.ai/referral/example123" in first[0].text
    assert second == []


def test_seconds_until_youtube_quota_reset_uses_pacific_midnight():
    from datetime import datetime, timezone
    from app.watchers.youtube import seconds_until_youtube_quota_reset

    # 2026-09-08 20:00 UTC = 13:00 PDT. Next midnight PDT is 11 hours away,
    # plus the 2-minute safety buffer.
    now = datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc)
    assert seconds_until_youtube_quota_reset(now) == (11 * 3600) + 120


def test_detects_google_daily_quota_error():
    from app.watchers.youtube import is_daily_quota_error

    request = httpx.Request("GET", "https://www.googleapis.com/youtube/v3/search")
    response = httpx.Response(
        403,
        request=request,
        json={
            "error": {
                "errors": [{"reason": "quotaExceeded"}],
                "message": "Quota exceeded",
            }
        },
    )
    exc = httpx.HTTPStatusError("quota", request=request, response=response)
    assert is_daily_quota_error(exc) is True
