from __future__ import annotations

from app.config import APIFY_API_TOKEN


_apify_token_override: str | None = None


def get_apify_token() -> str:
    """Return the active Apify token without exposing it to logs."""
    if _apify_token_override is not None:
        return _apify_token_override
    return APIFY_API_TOKEN


def set_apify_token(token: str) -> None:
    """Switch the active Apify token for the running process immediately."""
    global _apify_token_override
    cleaned = token.strip()
    if not cleaned:
        raise ValueError("Apify token cannot be blank")
    _apify_token_override = cleaned


def clear_apify_token_override() -> None:
    global _apify_token_override
    _apify_token_override = None


def masked_apify_token() -> str:
    token = get_apify_token()
    if not token:
        return "not configured"
    if len(token) <= 10:
        return "configured"
    return f"{token[:6]}...{token[-4:]}"
