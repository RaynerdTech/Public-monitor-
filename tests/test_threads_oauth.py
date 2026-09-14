from datetime import datetime, timedelta, timezone

import httpx

from app.services.threads_oauth import (
    ThreadsTokenRecord,
    build_threads_authorization_url,
    load_threads_token,
    load_threads_token_value,
    persist_threads_token_to_render,
    save_threads_token,
    serialize_threads_token,
)


def test_build_threads_authorization_url_contains_required_values():
    url = build_threads_authorization_url(
        "123",
        "https://example.com/threads/callback",
        ["threads_basic", "threads_keyword_search"],
        "state123",
    )
    assert url.startswith("https://threads.net/oauth/authorize?")
    assert "client_id=123" in url
    assert "redirect_uri=https%3A%2F%2Fexample.com%2Fthreads%2Fcallback" in url
    assert "scope=threads_basic%2Cthreads_keyword_search" in url
    assert "response_type=code" in url
    assert "state=state123" in url


def test_build_threads_authorization_url_requires_https():
    try:
        build_threads_authorization_url(
            "123",
            "http://localhost:8765/threads/callback",
            ["threads_basic"],
            "state123",
        )
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("Expected HTTP redirect URI to be rejected")


def test_threads_token_store_round_trip(tmp_path):
    path = tmp_path / "threads-token.json"
    record = ThreadsTokenRecord(
        access_token="secret-token",
        user_id="99",
        username="tester",
        expires_at=(datetime.now(timezone.utc) + timedelta(days=60)).isoformat(),
    )
    save_threads_token(str(path), record)
    loaded = load_threads_token(str(path))
    assert loaded is not None
    assert loaded.access_token == "secret-token"
    assert loaded.user_id == "99"
    assert loaded.username == "tester"
    assert not loaded.is_expired()


def test_threads_token_environment_record_round_trip():
    record = ThreadsTokenRecord(
        access_token="secret-token",
        user_id="99",
        username="tester",
        expires_at=(datetime.now(timezone.utc) + timedelta(days=60)).isoformat(),
    )

    loaded = load_threads_token_value(serialize_threads_token(record))

    assert loaded is not None
    assert loaded.access_token == "secret-token"
    assert loaded.user_id == "99"
    assert loaded.username == "tester"


def test_persist_threads_token_to_render_updates_one_secret_and_queues_deploy():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200 if request.method == "PUT" else 201, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    record = ThreadsTokenRecord(access_token="secret-token", user_id="99")
    try:
        persist_threads_token_to_render(
            api_key="render-api-key",
            service_id="srv-test",
            record=record,
            client=client,
        )
    finally:
        client.close()

    assert [request.method for request in requests] == ["PUT", "POST"]
    assert requests[0].url.path == (
        "/v1/services/srv-test/env-vars/THREADS_TOKEN_RECORD"
    )
    assert requests[0].headers["authorization"] == "Bearer render-api-key"
    assert b"secret-token" in requests[0].content
    assert requests[1].url.path == "/v1/services/srv-test/deploys"
    assert requests[1].content == b'{"deployMode":"deploy_only"}'


def test_resolve_threads_redirect_uri_from_railway_domain():
    from app.services.threads_oauth import resolve_threads_redirect_uri

    assert (
        resolve_threads_redirect_uri(
            configured_uri="",
            railway_public_domain="referral-monitor-production.up.railway.app",
        )
        == "https://referral-monitor-production.up.railway.app/threads/callback"
    )


def test_resolve_threads_redirect_uri_prefers_explicit_value():
    from app.services.threads_oauth import resolve_threads_redirect_uri

    assert (
        resolve_threads_redirect_uri(
            configured_uri="https://monitor.example.com/threads/callback",
            railway_public_domain="ignored.up.railway.app",
        )
        == "https://monitor.example.com/threads/callback"
    )



def test_resolve_threads_redirect_uri_from_render_external_url():
    from app.services.threads_oauth import resolve_threads_redirect_uri

    assert (
        resolve_threads_redirect_uri(
            configured_uri="",
            render_external_url="https://referral-monitor.onrender.com",
        )
        == "https://referral-monitor.onrender.com/threads/callback"
    )


def test_resolve_threads_redirect_uri_from_render_hostname():
    from app.services.threads_oauth import resolve_threads_redirect_uri

    assert (
        resolve_threads_redirect_uri(
            configured_uri="",
            render_external_hostname="referral-monitor.onrender.com",
        )
        == "https://referral-monitor.onrender.com/threads/callback"
    )


def test_explicit_redirect_uri_beats_render_and_railway():
    from app.services.threads_oauth import resolve_threads_redirect_uri

    assert (
        resolve_threads_redirect_uri(
            configured_uri="https://monitor.example.com/threads/callback",
            render_external_url="https://ignored.onrender.com",
            railway_public_domain="ignored.up.railway.app",
        )
        == "https://monitor.example.com/threads/callback"
    )

def test_resolve_threads_server_port_uses_platform_port():
    from app.services.threads_oauth import resolve_threads_server_port

    assert resolve_threads_server_port(port_env="4321", fallback=8765) == 4321
