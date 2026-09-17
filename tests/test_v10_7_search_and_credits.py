import asyncio

import httpx

import app.config as config
import app.services.credit_monitor as cm


def test_paid_social_search_defaults_to_referral_domain(monkeypatch):
    monkeypatch.setenv("THREADS_QUERIES", "claude referral")
    monkeypatch.setattr(config, "SEARCH_INCLUDE_BROAD_KEYWORDS", False)
    assert config._link_focused_queries("THREADS_QUERIES", "claude referral") == [
        "claude.ai/referral"
    ]


def test_broad_keywords_are_optional_and_deduped(monkeypatch):
    monkeypatch.setenv("THREADS_QUERIES", "claude referral||claude.ai/referral")
    monkeypatch.setattr(config, "SEARCH_INCLUDE_BROAD_KEYWORDS", True)
    assert config._link_focused_queries("THREADS_QUERIES", "claude referral") == [
        "claude.ai/referral",
        "claude referral",
    ]


def test_credit_status_includes_all_sources(monkeypatch):
    monkeypatch.setattr(cm, "_last_scrape_creators_balance", 6400)
    monkeypatch.setattr(cm, "_last_scrape_creators_hours", 100.0)
    monkeypatch.setattr(cm, "_last_apify_remaining_usd", 4.25)
    monkeypatch.setattr(cm, "_last_apify_hours", 8.0)
    monkeypatch.setattr(cm, "_last_x_total_balance_usd", 12.50)
    monkeypatch.setattr(cm, "_last_x_project_usage", 100)
    monkeypatch.setattr(cm, "_last_x_project_cap", 1000)
    text = cm.credit_status_text()
    assert "Scrape Creators (Threads + Reddit)" in text
    assert "Apify (Facebook + Instagram)" in text
    assert "X: $12.50" in text
    assert "YouTube:" in text
    assert "Exa web:" in text
    assert "Podcast Index:" in text
    assert "Direct websites:" in text
    assert "Telegram Bot API:" in text


def test_x_credit_and_usage_endpoints(monkeypatch):
    monkeypatch.setattr(cm, "X_BEARER_TOKEN", "test-token")

    async def fake_get(self, url, params=None, **kwargs):
        request = httpx.Request("GET", url)
        if url.endswith("/usage/credits"):
            return httpx.Response(
                200,
                request=request,
                json={"data": {"total_balance": 9.75}},
            )
        if url.endswith("/usage/tweets"):
            return httpx.Response(
                200,
                request=request,
                json={"data": {"project_usage": "123", "project_cap": "456"}},
            )
        raise AssertionError(url)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    balance, usage, cap = asyncio.run(cm.check_x_credit_once())
    assert balance == 9.75
    assert usage == 123
    assert cap == 456


def test_structured_payload_referral_extraction():
    from app.core.extractor import extract_referral_links_from_data

    payload = {
        "postText": "link is in metadata",
        "attachments": [
            {"target": {"url": "https://claude.ai/referral/structured-code?s=test"}}
        ],
    }
    assert extract_referral_links_from_data(payload) == [
        "https://claude.ai/referral/structured-code?s=test"
    ]
