import asyncio

import httpx

import app.config as config
import app.services.credit_monitor as cm


def test_url_capable_engines_get_the_literal_referral_term(monkeypatch):
    monkeypatch.delenv("REDDIT_QUERIES", raising=False)
    monkeypatch.setattr(config, "SEARCH_INCLUDE_BROAD_KEYWORDS", False)
    assert config._link_focused_queries(
        "REDDIT_QUERIES", "claude referral", engine_matches_urls=True
    ) == ["claude.ai/referral"]


def test_keyword_only_engines_never_get_a_bare_url(monkeypatch):
    """Threads/Instagram/Facebook search cannot match a literal URL.

    Sending one returned raw_results: 0 in production even though a public post
    containing the referral link existed.
    """
    monkeypatch.delenv("THREADS_QUERIES", raising=False)
    monkeypatch.setattr(config, "SEARCH_INCLUDE_BROAD_KEYWORDS", False)
    queries = config._link_focused_queries(
        "THREADS_QUERIES", "claude referral", engine_matches_urls=False
    )
    assert queries == ["claude referral"]
    assert not any("claude.ai/referral" in query for query in queries)


def test_explicit_env_queries_always_win(monkeypatch):
    monkeypatch.setenv("THREADS_QUERIES", "custom one||custom two||custom one")
    monkeypatch.setattr(config, "SEARCH_INCLUDE_BROAD_KEYWORDS", False)
    assert config._link_focused_queries(
        "THREADS_QUERIES", "claude referral", engine_matches_urls=False
    ) == ["custom one", "custom two"]


def test_broad_keywords_are_optional_and_deduped(monkeypatch):
    monkeypatch.delenv("THREADS_QUERIES", raising=False)
    monkeypatch.setattr(config, "SEARCH_INCLUDE_BROAD_KEYWORDS", True)
    queries = config._link_focused_queries(
        "THREADS_QUERIES", "claude referral", engine_matches_urls=False
    )
    assert queries[0] == "claude referral"
    assert "claude.ai/referral" in queries
    assert len(queries) == len(set(queries))


def test_credit_status_includes_all_sources(monkeypatch):
    monkeypatch.setattr(cm, "_last_scrape_creators_balance", 6400)
    monkeypatch.setattr(cm, "_last_scrape_creators_hours", 100.0)
    monkeypatch.setattr(cm, "_last_apify_remaining_usd", 4.25)
    monkeypatch.setattr(cm, "_last_apify_hours", 8.0)
    monkeypatch.setattr(cm, "_last_x_total_balance_usd", 12.50)
    monkeypatch.setattr(cm, "_last_x_project_usage", 100)
    monkeypatch.setattr(cm, "_last_x_project_cap", 1000)
    text = cm.credit_status_text()
    assert "💳 Credits & usage" in text
    assert "Scrape Creators" in text
    assert "Threads + Reddit" in text
    assert "Apify" in text
    assert "Facebook + Instagram" in text
    assert "$12.50 balance" in text
    assert "YouTube" in text
    assert "Exa web" in text
    assert "Podcast Index" in text
    assert "Direct websites" in text
    assert "Telegram" in text
    assert "\n\n" in text


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
