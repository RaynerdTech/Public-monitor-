import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from app.config import (
    VALIDATION_MIN_GAP_SECONDS,
    VALIDATION_RETRIES,
    VALIDATION_TIMEOUT_SECONDS,
    VALIDATOR_BROWSER_FALLBACK_ENABLED,
    VALIDATOR_PROXY_URL,
)


@dataclass
class ValidationResult:
    status: str
    campaign: str | None = None
    is_valid: bool | None = None
    message: str | None = None
    raw: dict | None = None
    method: str = "direct"


_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
}

_GUEST_PASS_CAMPAIGNS = {
    "claude_code_guest_pass",
    "claude_code_guest_pass_a47c",
}


class _DirectRequestGate:
    """Serialize Claude API requests and leave a small gap between starts."""

    def __init__(self) -> None:
        self._loop = None
        self._lock: asyncio.Lock | None = None
        self._last_finished_at = 0.0

    def _state(self) -> tuple[asyncio.AbstractEventLoop, asyncio.Lock]:
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._lock is None:
            self._loop = loop
            self._lock = asyncio.Lock()
            self._last_finished_at = 0.0
        return loop, self._lock

    async def get(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        loop, lock = self._state()
        async with lock:
            remaining = VALIDATION_MIN_GAP_SECONDS - (
                loop.time() - self._last_finished_at
            )
            if remaining > 0:
                await asyncio.sleep(remaining)
            try:
                return await client.get(url)
            finally:
                self._last_finished_at = loop.time()


_DIRECT_REQUEST_GATE = _DirectRequestGate()


def classify_referral_payload(data, *, method: str = "direct") -> ValidationResult:
    if data is None:
        return ValidationResult(
            status="not_found",
            message="Referral not found",
            method=method,
        )

    if not isinstance(data, dict):
        return ValidationResult(
            status="unknown",
            message="Unexpected response shape",
            method=method,
        )

    raw_campaign = data.get("campaign")
    campaign = raw_campaign if isinstance(raw_campaign, str) else None
    is_valid = data.get("is_valid")

    if campaign in _GUEST_PASS_CAMPAIGNS and is_valid is True:
        status = "valid"
    elif campaign == "claude_invite_contest" and is_valid is True:
        status = "contest"
    elif is_valid is False:
        status = "inactive"
    elif is_valid is True and campaign:
        # Claude uses this endpoint for other campaigns too. A true result from
        # one of those campaigns is not a usable Claude Code guest pass.
        status = "contest"
    else:
        status = "unknown"

    message = None
    if status == "contest" and campaign != "claude_invite_contest":
        message = f"Non-guest-pass campaign: {campaign}"

    return ValidationResult(
        status=status,
        campaign=campaign,
        is_valid=is_valid,
        message=message,
        raw=data,
        method=method,
    )


def _looks_like_cloudflare(status_code: int, text: str, content_type: str = "") -> bool:
    if status_code != 403:
        return False

    haystack = f"{content_type}\n{text}".lower()
    return any(
        marker in haystack
        for marker in (
            "just a moment",
            "challenges.cloudflare.com",
            "cf-chl-",
            "cloudflare ray id",
        )
    )


def _classify_http_response(
    status_code: int,
    text: str,
    content_type: str = "",
    *,
    method: str,
) -> ValidationResult:
    if _looks_like_cloudflare(status_code, text, content_type):
        return ValidationResult(
            status="blocked",
            message="Cloudflare browser challenge",
            method=method,
        )

    if status_code == 403:
        return ValidationResult(
            status="blocked",
            message="HTTP 403 access blocked",
            method=method,
        )

    if status_code == 404:
        return ValidationResult(
            status="not_found",
            message="Referral not found",
            method=method,
        )

    if status_code != 200:
        return ValidationResult(
            status="error",
            message=(
                "HTTP 429 rate limited"
                if status_code == 429
                else f"HTTP {status_code}"
                if status_code
                else "Validation request failed"
            ),
            method=method,
        )

    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return ValidationResult(
            status="error",
            message="Invalid JSON response",
            method=method,
        )

    return classify_referral_payload(data, method=method)


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


async def _validate_json_api(
    code: str,
    *,
    method: str,
    proxy_url: str | None = None,
) -> ValidationResult:
    url = f"https://claude.ai/api/referral/code/{code}"
    last_error: str | None = None

    client_options = {
        "timeout": VALIDATION_TIMEOUT_SECONDS,
        "follow_redirects": True,
        "headers": _HEADERS,
    }
    if proxy_url:
        client_options["proxy"] = proxy_url

    async with httpx.AsyncClient(**client_options) as client:
        for attempt in range(VALIDATION_RETRIES):
            response: httpx.Response | None = None
            try:
                response = await _DIRECT_REQUEST_GATE.get(client, url)
                result = _classify_http_response(
                    response.status_code,
                    response.text,
                    response.headers.get("content-type", ""),
                    method=method,
                )

                # A 403 should move to the next configured fallback immediately.
                if result.status == "blocked":
                    return result
                if result.status != "error":
                    return result

                last_error = result.message
            except httpx.HTTPError as exc:
                last_error = (
                    f"Proxy request failed: {type(exc).__name__}"
                    if proxy_url
                    else str(exc)
                )

            if attempt + 1 < VALIDATION_RETRIES:
                retry_after = _retry_after_seconds(
                    response.headers.get("retry-after")
                    if response is not None
                    else None
                )
                delay = retry_after if retry_after is not None else 0.75 * (attempt + 1)
                await asyncio.sleep(min(30.0, max(0.0, delay)))

    return ValidationResult(
        status="error",
        message=last_error or "Validation request failed",
        method=method,
    )


async def _validate_direct(code: str) -> ValidationResult:
    return await _validate_json_api(code, method="direct")


async def _validate_proxy(code: str) -> ValidationResult:
    if not VALIDATOR_PROXY_URL:
        return ValidationResult(
            status="blocked",
            message="No clean proxy configured",
            method="proxy",
        )
    return await _validate_json_api(
        code,
        method="proxy",
        proxy_url=VALIDATOR_PROXY_URL,
    )


async def validate_referral(code: str) -> ValidationResult:
    direct = await _validate_direct(code)
    if direct.status != "blocked":
        return direct

    fallback_result = direct
    if VALIDATOR_PROXY_URL:
        fallback_result = await _validate_proxy(code)
        if fallback_result.status != "blocked":
            return fallback_result

    if not VALIDATOR_BROWSER_FALLBACK_ENABLED:
        return fallback_result

    from app.services.browser_validator import fetch_referral_api_in_browser

    browser = await fetch_referral_api_in_browser(code)
    if browser.status_code == 0:
        return ValidationResult(
            status="blocked",
            message=browser.message or "Browser fallback could not run",
            method="browser",
        )
    if browser.status_code == 403 and browser.message:
        return ValidationResult(
            status="blocked",
            message=browser.message,
            method="browser",
        )

    result = _classify_http_response(
        browser.status_code,
        browser.text,
        browser.content_type,
        method="browser",
    )
    if result.status == "blocked" and browser.message:
        result.message = browser.message
    return result
