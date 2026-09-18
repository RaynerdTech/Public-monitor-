"""Regression tests for the v10.8.0 zero-result investigation.

Each test pins one root cause found while diagnosing why Threads, Reddit,
Facebook, Instagram and Direct Web reported raw results but produced no
Telegram alerts.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.watchers.apify_facebook import (
    ApifyFacebookWatcher,
    facebook_apify_row_to_source_post,
)
from app.watchers.base import SourcePost
from app.watchers.candidates import PollDiagnostics, evaluate_candidate
from app.watchers.reddit import parse_reddit_timestamp
from app.watchers.sites import DirectWebsiteWatcher


# --------------------------------------------------------------------------
# Root cause 1: the freshness gate ran before the referral-link check
# --------------------------------------------------------------------------


def test_row_with_referral_link_but_no_timestamp_is_accepted():
    post = SourcePost(
        source="facebook:search",
        text="Try Claude https://claude.ai/referral/TEST-FB-123",
        url="https://www.facebook.com/post/1",
        created_at=None,
    )
    verdict = evaluate_candidate(post, max_post_age_minutes=360, lookback_minutes=12)
    assert verdict.accepted
    assert verdict.reason == "accepted_timestamp_unavailable"
    assert verdict.referral_links == ("https://claude.ai/referral/TEST-FB-123",)


def test_row_without_a_referral_link_is_rejected_even_when_fresh():
    post = SourcePost(
        source="threads:@someone",
        text="Ask me about my Claude referral",
        created_at=datetime.now(timezone.utc),
    )
    verdict = evaluate_candidate(post, max_post_age_minutes=360, lookback_minutes=12)
    assert not verdict.accepted
    assert verdict.reason == "no_referral_link"


def test_post_older_than_lookback_but_inside_max_age_is_accepted():
    created = datetime.now(timezone.utc) - timedelta(minutes=45)
    post = SourcePost("reddit", "https://claude.ai/referral/abc", created_at=created)
    verdict = evaluate_candidate(post, max_post_age_minutes=180, lookback_minutes=7)
    assert verdict.accepted
    assert verdict.reason == "accepted_indexed_late"


def test_post_past_the_max_age_is_rejected():
    created = datetime.now(timezone.utc) - timedelta(hours=9)
    post = SourcePost("reddit", "https://claude.ai/referral/abc", created_at=created)
    verdict = evaluate_candidate(post, max_post_age_minutes=180, lookback_minutes=7)
    assert not verdict.accepted
    assert verdict.reason == "older_than_max_post_age"


def test_rejected_referral_link_is_always_logged_in_full(capsys):
    """The failure that used to be silent must now be loud."""
    diagnostics = PollDiagnostics("Reddit", sample_limit=0)
    created = datetime.now(timezone.utc) - timedelta(hours=9)
    post = SourcePost(
        "reddit:r/ClaudeAI",
        "https://claude.ai/referral/lost-code",
        url="https://reddit.com/r/ClaudeAI/comments/x/",
        created_at=created,
    )
    verdict = evaluate_candidate(post, max_post_age_minutes=180, lookback_minutes=7)
    assert diagnostics.record(verdict, post, row_id="x") is False

    output = capsys.readouterr().out
    assert "referral_candidate_rejected" in output
    assert "older_than_max_post_age" in output
    assert "lost-code" in output


def test_poll_counters_separate_no_link_from_stale():
    diagnostics = PollDiagnostics("Threads", sample_limit=0)
    now = datetime.now(timezone.utc)
    rows = [
        SourcePost("t", "no link here", created_at=now),
        SourcePost("t", "https://claude.ai/referral/a", created_at=now),
        SourcePost(
            "t",
            "https://claude.ai/referral/b",
            created_at=now - timedelta(hours=9),
        ),
    ]
    diagnostics.record_raw(len(rows))
    for post in rows:
        diagnostics.record(
            evaluate_candidate(post, max_post_age_minutes=180, lookback_minutes=7),
            post,
        )

    fields = diagnostics.as_fields()
    assert fields["raw_results"] == 3
    assert fields["rows_with_referral_link"] == 2
    assert fields["accepted"] == 1
    assert fields["rejected_no_link"] == 1
    assert fields["rejected_stale"] == 1
    assert "newest_result_age_seconds" in fields


# --------------------------------------------------------------------------
# Root cause 2: Facebook timestamps were never mapped
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row",
    [
        {"time": "2 minutes ago"},
        {"time": "about 3 hours ago"},
        {"publish_time": 1750341959},
        {"creation_time": "2026-09-17T10:00:00Z"},
        {"post": {"publishTime": 1750341959}},
        {"relativeTime": "just now"},
    ],
)
def test_facebook_timestamp_variants_are_mapped(row):
    row = {
        "url": "https://www.facebook.com/post/1",
        "text": "https://claude.ai/referral/TEST-FB-123",
        **row,
    }
    post = facebook_apify_row_to_source_post(row)
    assert post is not None
    assert post.created_at is not None, "unmapped timestamp silently drops the row"


def test_facebook_search_row_reaches_the_pipeline(monkeypatch):
    """End-to-end replay of the controlled TEST-FB-123 post."""
    watcher = ApifyFacebookWatcher(
        ["claude referral"],
        actor_id="memo23~facebook-search-scraper",
        lookback_minutes=12,
        max_post_age_minutes=360,
    )

    async def fake_run_actor(_actor, _payload, *, source, max_items):
        return [
            {
                "postId": "1234567890",
                "url": "https://www.facebook.com/permalink.php?story_fbid=1",
                "text": "Try Claude free https://claude.ai/referral/TEST-FB-123",
                "author": {"name": "Test User"},
                "time": "2 minutes ago",
            }
        ]

    monkeypatch.setattr(watcher.client, "run_actor", fake_run_actor)
    posts = asyncio.run(watcher.fetch())
    assert len(posts) == 1
    assert "TEST-FB-123" in posts[0].text


def test_facebook_row_with_no_timestamp_at_all_still_reaches_the_pipeline(monkeypatch):
    watcher = ApifyFacebookWatcher(
        ["claude referral"],
        actor_id="memo23~facebook-search-scraper",
        lookback_minutes=12,
    )

    async def fake_run_actor(_actor, _payload, *, source, max_items):
        return [
            {
                "url": "https://www.facebook.com/post/2",
                "text": "https://claude.ai/referral/NO-TIMESTAMP",
            }
        ]

    monkeypatch.setattr(watcher.client, "run_actor", fake_run_actor)
    posts = asyncio.run(watcher.fetch())
    assert [post.url for post in posts] == ["https://www.facebook.com/post/2"]


# --------------------------------------------------------------------------
# Root cause 3: Reddit ISO timestamps
# --------------------------------------------------------------------------


def test_reddit_timestamp_accepts_epoch_and_iso():
    assert parse_reddit_timestamp(1750341959) == datetime(
        2025, 6, 19, 14, 5, 59, tzinfo=timezone.utc
    )
    assert parse_reddit_timestamp("2026-01-07T20:01:30.000Z") == datetime(
        2026, 1, 7, 20, 1, 30, tzinfo=timezone.utc
    )
    assert parse_reddit_timestamp(None) is None
    assert parse_reddit_timestamp("not a date") is None


# --------------------------------------------------------------------------
# Root cause 4: Direct Web sent conditional requests for sitemaps
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sitemaps_are_never_fetched_conditionally(monkeypatch):
    """A cached 304 on a sitemap used to zero out the whole poll.

    Symptom in production: sitemaps_polled: 7, pages_fetched: 0.
    """
    watcher = DirectWebsiteWatcher(["https://example.com"], lookback_minutes=180)
    conditional_by_url: dict[str, bool] = {}
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    sitemap = (
        "<urlset><url><loc>https://example.com/new-post</loc>"
        f"<lastmod>{now}</lastmod></url></urlset>"
    )

    async def fake_get(_client, url, *, conditional=True):
        conditional_by_url[url] = conditional
        if url.endswith("/sitemap.xml"):
            return httpx.Response(
                200, text=sitemap, headers={"content-type": "application/xml"}
            )
        if url == "https://example.com/new-post":
            return httpx.Response(
                200,
                text='<a href="https://claude.ai/referral/page-code">Open</a>',
                headers={"content-type": "text/html"},
            )
        return None

    monkeypatch.setattr(watcher, "_get", fake_get)
    posts = await watcher.fetch()

    assert [post.url for post in posts] == ["https://example.com/new-post"]
    assert conditional_by_url["https://example.com/sitemap.xml"] is False
    # Page bodies keep the bandwidth-saving conditional request.
    assert conditional_by_url["https://example.com/new-post"] is True


@pytest.mark.anyio
async def test_direct_web_reports_why_pages_were_not_fetched(monkeypatch, capsys):
    watcher = DirectWebsiteWatcher(["https://example.com"], lookback_minutes=5)
    stale = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat().replace(
        "+00:00", "Z"
    )
    sitemap = (
        "<urlset><url><loc>https://example.com/old-post</loc>"
        f"<lastmod>{stale}</lastmod></url></urlset>"
    )

    async def fake_get(_client, url, **_kwargs):
        if url.endswith("/sitemap.xml"):
            return httpx.Response(
                200, text=sitemap, headers={"content-type": "application/xml"}
            )
        return None

    monkeypatch.setattr(watcher, "_get", fake_get)
    assert await watcher.fetch() == []

    output = capsys.readouterr().out
    assert '"dated_rejected_too_old": 1' in output
    assert '"page_candidates": 1' in output
