from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx


THREADS_AUTH_URL = "https://threads.net/oauth/authorize"
THREADS_TOKEN_URL = "https://graph.threads.net/oauth/access_token"
THREADS_LONG_LIVED_URL = "https://graph.threads.net/access_token"
THREADS_REFRESH_URL = "https://graph.threads.net/refresh_access_token"
THREADS_ME_URL = "https://graph.threads.net/v1.0/me"


@dataclass
class ThreadsTokenRecord:
    access_token: str
    user_id: str | None = None
    username: str | None = None
    token_type: str = "bearer"
    expires_at: str | None = None
    created_at: str | None = None
    refreshed_at: str | None = None

    @property
    def expiry(self) -> datetime | None:
        if not self.expires_at:
            return None
        try:
            value = self.expires_at.replace("Z", "+00:00")
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def is_expired(self) -> bool:
        expiry = self.expiry
        return bool(expiry and expiry <= datetime.now(timezone.utc))

    def needs_refresh(self, within_days: int = 7) -> bool:
        expiry = self.expiry
        if not expiry:
            return False
        return expiry <= datetime.now(timezone.utc) + timedelta(days=within_days)


def build_threads_authorization_url(
    app_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
) -> str:
    if not app_id:
        raise ValueError("Threads App ID is required")
    if not redirect_uri.startswith("https://"):
        raise ValueError("Threads redirect URI must use HTTPS")
    if not scopes:
        raise ValueError("At least one Threads scope is required")

    query = urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "scope": ",".join(scopes),
            "response_type": "code",
            "state": state,
        }
    )
    return f"{THREADS_AUTH_URL}?{query}"


def new_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def _require_json_object(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Threads API returned an unexpected response")
    return payload


def exchange_code_for_short_lived_token(
    *,
    app_id: str,
    app_secret: str,
    code: str,
    redirect_uri: str,
    timeout_seconds: float = 20,
) -> tuple[str, str | None]:
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        response = client.post(
            THREADS_TOKEN_URL,
            params={
                "client_id": app_id,
                "client_secret": app_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        payload = _require_json_object(response)

    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Threads did not return a short-lived access token")
    user_id = str(payload.get("user_id") or "").strip() or None
    return token, user_id


def exchange_for_long_lived_token(
    *,
    short_lived_token: str,
    app_secret: str,
    timeout_seconds: float = 20,
) -> tuple[str, int | None, str]:
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        response = client.get(
            THREADS_LONG_LIVED_URL,
            params={
                "grant_type": "th_exchange_token",
                "client_secret": app_secret,
                "access_token": short_lived_token,
            },
        )
        payload = _require_json_object(response)

    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Threads did not return a long-lived access token")

    expires_raw = payload.get("expires_in")
    try:
        expires_in = int(expires_raw) if expires_raw is not None else None
    except (TypeError, ValueError):
        expires_in = None
    token_type = str(payload.get("token_type") or "bearer")
    return token, expires_in, token_type


def refresh_long_lived_token(
    *,
    access_token: str,
    timeout_seconds: float = 20,
) -> tuple[str, int | None, str]:
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        response = client.get(
            THREADS_REFRESH_URL,
            params={
                "grant_type": "th_refresh_token",
                "access_token": access_token,
            },
        )
        payload = _require_json_object(response)

    token = str(payload.get("access_token") or access_token).strip()
    expires_raw = payload.get("expires_in")
    try:
        expires_in = int(expires_raw) if expires_raw is not None else None
    except (TypeError, ValueError):
        expires_in = None
    token_type = str(payload.get("token_type") or "bearer")
    return token, expires_in, token_type


def fetch_threads_identity(
    *, access_token: str, timeout_seconds: float = 20
) -> tuple[str | None, str | None]:
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        response = client.get(
            THREADS_ME_URL,
            params={
                "fields": "id,username",
                "access_token": access_token,
            },
        )
        payload = _require_json_object(response)

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None, None
    user_id = str(data.get("id") or "").strip() or None
    username = str(data.get("username") or "").strip() or None
    return user_id, username


def save_threads_token(path: str, record: ThreadsTokenRecord) -> None:
    token_path = Path(path)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps(asdict(record), indent=2), encoding="utf-8")
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass


def load_threads_token(path: str) -> ThreadsTokenRecord | None:
    token_path = Path(path)
    if not token_path.exists():
        return None
    try:
        payload = json.loads(token_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        token = str(payload.get("access_token") or "").strip()
        if not token:
            return None
        return ThreadsTokenRecord(
            access_token=token,
            user_id=str(payload.get("user_id") or "").strip() or None,
            username=str(payload.get("username") or "").strip() or None,
            token_type=str(payload.get("token_type") or "bearer"),
            expires_at=str(payload.get("expires_at") or "").strip() or None,
            created_at=str(payload.get("created_at") or "").strip() or None,
            refreshed_at=str(payload.get("refreshed_at") or "").strip() or None,
        )
    except (OSError, ValueError, TypeError):
        return None


def create_token_record(
    *,
    access_token: str,
    user_id: str | None,
    username: str | None,
    token_type: str,
    expires_in: int | None,
) -> ThreadsTokenRecord:
    now = datetime.now(timezone.utc)
    expires_at = (
        (now + timedelta(seconds=expires_in)).isoformat() if expires_in else None
    )
    return ThreadsTokenRecord(
        access_token=access_token,
        user_id=user_id,
        username=username,
        token_type=token_type,
        expires_at=expires_at,
        created_at=now.isoformat(),
    )


def refresh_token_record(
    *, record: ThreadsTokenRecord, timeout_seconds: float = 20
) -> ThreadsTokenRecord:
    token, expires_in, token_type = refresh_long_lived_token(
        access_token=record.access_token,
        timeout_seconds=timeout_seconds,
    )
    now = datetime.now(timezone.utc)
    record.access_token = token
    record.token_type = token_type
    record.refreshed_at = now.isoformat()
    if expires_in:
        record.expires_at = (now + timedelta(seconds=expires_in)).isoformat()
    return record


def resolve_threads_redirect_uri(
    *,
    configured_uri: str = "",
    render_external_url: str = "",
    render_external_hostname: str = "",
    railway_public_domain: str = "",
) -> str:
    """Resolve the public HTTPS callback URL for hosted Threads OAuth.

    An explicitly configured URI always wins. On Render, the platform-provided
    external URL/hostname is used automatically. Railway remains as a backwards-
    compatible fallback for existing deployments.
    """
    uri = configured_uri.strip()

    if not uri and render_external_url.strip():
        uri = render_external_url.strip().rstrip("/") + "/threads/callback"

    if not uri and render_external_hostname.strip():
        hostname = render_external_hostname.strip().strip("/")
        uri = f"https://{hostname}/threads/callback"

    if not uri and railway_public_domain.strip():
        hostname = railway_public_domain.strip().strip("/")
        uri = f"https://{hostname}/threads/callback"

    if not uri:
        return ""

    parsed = urlparse(uri)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Threads redirect URI must be a complete HTTPS URL")
    return uri


def resolve_threads_server_port(*, port_env: str = "", fallback: int = 8765) -> int:
    raw = port_env.strip()
    if raw:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError("PORT must be an integer") from exc
        if not 1 <= value <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        return value
    return fallback
